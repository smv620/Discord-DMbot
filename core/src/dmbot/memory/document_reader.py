"""Reading a PDF or Word file in its own process, with a hard time limit (#251).

pypdf is pure Python: in a thread, a slow or hostile file competes with the bot's
event loop (voice, consent checks, every other server) for the interpreter, and a
thread can't be stopped. Each such file is read in a short-lived child process instead,
which is ended if it takes longer than READ_TIME_S. One process per file, not a pool: a
pool can't stop one task without breaking the others it's running.

The child is a guard for time and memory, not a sandbox. It starts with the bot's
environment, so it clears it first thing, and its answer comes back as plain bytes,
never unpickled, so a parser bug in the child can't reach the bot through the reply.

Every failure is a DocumentError in plain words, as from `text_of`.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from multiprocessing.connection import Connection
from pathlib import PurePath

from dmbot.memory.name_documents import UNREADABLE, DocumentError, text_of

READ_TIME_S = 30.0
# Read in a process: pure-Python parsers that a hostile file can keep busy. Other types
# are read in a thread (text decoding; web pages have their own time limit).
IN_A_PROCESS = (".pdf", ".docx")
# A child's memory: a damaged file can't take the whole server's (Linux only). A real
# 10 MB, 500-page PDF peaks near 62 MB (#251 review).
MEMORY_BYTES = 512 * 1024 * 1024
# The longest answer read back: text_of stops at 200,000 characters (up to 4 bytes each).
MAX_REPLY_BYTES = 1024 * 1024
TOO_SLOW = (
    "That file took too long to read. Split it into smaller files and add them one at a "
    "time, or type the names into the template: 📥 Add many > 📄 Get the template."
)
log = logging.getLogger(__name__)
# "spawn": a fresh interpreter. Forking the bot would copy its event loop and threads.
_CONTEXT = multiprocessing.get_context("spawn")
_SEP = b"\n"  # between the parts of an answer: kind, error kind, text

Parse = Callable[[str, bytes], str]
# Threads that wait on the children: their own, so files that hang can't take the
# shared ones that live work uses (hints, backups). Two read at once (name_lists'
# _PARSING), plus room for reads whose caller has gone and that are running out their
# time limit.
_WAITERS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dmbot-document")


def _limit_memory() -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    except (ImportError, ValueError, OSError):  # not Linux, or not allowed: no limit
        pass


def _child(conn: Connection, parse: Parse, filename: str, raw: bytes) -> None:
    """In the child process. Answers b"ok\\n\\n<text>", b"error\\n<cause>\\n<plain words>"
    or b"failed\\n<error kind>\\n", as plain bytes (never a pickle)."""
    os.environ.clear()  # the bot's keys and tokens: nothing here needs them
    logging.getLogger().addHandler(logging.NullHandler())  # the bot logs, not the child
    _limit_memory()
    try:
        parts = (b"ok", b"", parse(filename, raw).encode())
    except DocumentError as exc:
        cause = type(exc.__cause__).__name__ if exc.__cause__ is not None else ""
        parts = (b"error", cause.encode(), str(exc).encode())
    except BaseException as exc:  # anything at all goes back as plain words
        parts = (b"failed", type(exc).__name__.encode(), b"")
    try:
        conn.send_bytes(_SEP.join(parts))
    finally:
        conn.close()


def _run(parse: Parse, filename: str, raw: bytes, limit_s: float) -> tuple[str, str, str]:
    """Start the child, wait for its answer up to `limit_s`, and always end and collect
    it. Blocking: runs in a thread, all of it, so a cancelled caller can't leave a child
    running without its limit. Returns (kind, cause, text): kind is "ok", "error",
    "failed", "slow" or "died"."""
    receive, send = _CONTEXT.Pipe(duplex=False)
    child = _CONTEXT.Process(
        target=_child, args=(send, parse, filename, raw), name="dmbot-document", daemon=True
    )
    try:
        try:
            child.start()  # sends the file to the new process
        except Exception as exc:  # no process (container limits): plain words
            return "failed", type(exc).__name__, ""
        send.close()  # the child's end: EOF here if it dies without answering
        if not receive.poll(limit_s):
            child.kill()  # known stuck: no grace
            return "slow", "", ""
        try:
            answer = receive.recv_bytes(MAX_REPLY_BYTES)
        except (EOFError, OSError):  # it died (memory, killed) or answered too much
            return "died", "", ""
        kind, cause, text = [*answer.split(_SEP, 2), b"", b""][:3]
        return kind.decode(), cause.decode(), text.decode(errors="replace")
    finally:
        receive.close()
        send.close()
        if child.pid is not None:
            child.join(1)
            if child.is_alive():
                child.kill()
                child.join()


async def read_document(
    filename: str,
    raw: bytes,
    *,
    parse: Parse = text_of,
    limit_s: float = READ_TIME_S,
) -> str:
    """The file's text, as `text_of` gives it. PDF and Word files are read in a child
    process that is ended after `limit_s` seconds. `parse` must be importable by name
    (a child process starts it afresh); tests pass a slow one."""
    suffix = PurePath(filename).suffix.casefold()
    if suffix not in IN_A_PROCESS:
        return await asyncio.to_thread(parse, filename, raw)
    loop = asyncio.get_running_loop()
    work = loop.run_in_executor(_WAITERS, _run, parse, filename, raw, limit_s)
    # If the caller is cancelled, the work still finishes in its thread (within the
    # limit) and ends its child; nobody waits for it, so its result is dropped quietly.
    work.add_done_callback(lambda done: done.cancelled() or done.exception())
    kind, cause, text = await asyncio.shield(work)
    if kind == "ok":
        return text
    if kind == "error":
        if cause:  # text_of's own "couldn't read" (the child doesn't log)
            log.warning("Couldn't read a %s file: %s", suffix, cause)
        raise DocumentError(text)
    if kind == "slow":
        log.warning("Reading a %s file took over %.0f s: stopped", suffix, limit_s)
        raise DocumentError(TOO_SLOW)
    if kind == "died":
        log.warning("Reading a %s file ended its process", suffix)
    else:
        log.warning("Couldn't read a %s file: %s", suffix, cause or kind)
    raise DocumentError(UNREADABLE)
