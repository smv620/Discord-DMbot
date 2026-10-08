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
import threading
import time
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
# 10 MB, 500-page PDF peaks near 49 MB, and two can be read at once (#251 review).
MEMORY_BYTES = 256 * 1024 * 1024
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
# How often a waiting read checks whether nobody wants it any more: its caller was
# cancelled, or DMbot is closing (shutdown() below).
_POLL_S = 0.25

Parse = Callable[[str, bytes], str]
# Threads that wait on the children: their own, so files that hang can't take the
# shared ones that live work uses (hints, backups). Two read at once (name_lists'
# _PARSING), plus room for reads whose caller has gone and that are running out their
# time limit.
_WAITERS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dmbot-document")
_CLOSING = threading.Event()


def shutdown() -> None:
    """DMbot is closing: end every read now, and start no more. Without this a read in
    progress would hold up the interpreter's exit for up to READ_TIME_S, past
    `docker stop`'s grace period."""
    _CLOSING.set()
    _WAITERS.shutdown(wait=False, cancel_futures=True)


def _limit_memory() -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    except (ImportError, ValueError, OSError):  # not Linux, or not allowed: no limit
        pass


def _child(conn: Connection, parse: Parse, filename: str, raw: bytes) -> None:
    """In the child process. Answers b"ok\\n\\n<text>", b"error\\n<cause>\\n<plain words>"
    or b"failed\\n<error kind>\\n", as plain bytes (never a pickle). A traceback from
    the child itself (not from `parse`) goes to stderr as plain lines."""
    os.nice(10)  # behind the bot's voice and replies on a small server
    os.environ.clear()  # the bot's keys and tokens: nothing here needs them
    logging.getLogger().addHandler(logging.NullHandler())  # the bot logs, not the child
    _limit_memory()
    try:
        # errors="replace": a broken PDF can give a lone surrogate; keep the rest
        parts = (b"ok", b"", parse(filename, raw).encode(errors="replace"))
    except DocumentError as exc:
        cause = type(exc.__cause__).__name__ if exc.__cause__ is not None else ""
        parts = (b"error", cause.encode(), str(exc).encode())
    except BaseException as exc:  # anything at all goes back as plain words
        parts = (b"failed", type(exc).__name__.encode(), b"")
    try:
        conn.send_bytes(_SEP.join(parts))
    finally:
        conn.close()


def _run(
    parse: Parse, filename: str, raw: bytes, limit_s: float, stop: threading.Event
) -> tuple[str, str, str]:
    """Start the child, wait for its answer up to `limit_s`, and always end and collect
    it. Blocking: runs in a thread, all of it, so a cancelled caller can't leave a child
    running without its limit. `stop` (or DMbot closing) ends it early. Returns (kind,
    cause, text): kind is "ok", "error", "failed", "slow", "died" or "stopped"."""
    if stop.is_set() or _CLOSING.is_set():  # nobody wants it any more: no child at all
        return "stopped", "", ""
    try:
        receive, send = _CONTEXT.Pipe(duplex=False)
    except Exception as exc:  # out of file descriptors: plain words
        return "failed", type(exc).__name__, ""
    child = None
    try:
        try:
            child = _CONTEXT.Process(
                target=_child, args=(send, parse, filename, raw), name="dmbot-document", daemon=True
            )
            child.start()  # sends the file to the new process
        except Exception as exc:  # no process (container limits): plain words
            return "failed", type(exc).__name__, ""
        send.close()  # the child's end: EOF here if it dies without answering
        # The limit counts from here, once the new interpreter has started and has the file.
        deadline = time.monotonic() + limit_s
        while not receive.poll(max(0.0, min(_POLL_S, deadline - time.monotonic()))):
            if stop.is_set() or _CLOSING.is_set():
                child.kill()
                return "stopped", "", ""
            if time.monotonic() >= deadline:
                child.kill()  # known stuck: no grace
                return "slow", "", ""
        try:
            # No deadline needed: the answer has started arriving, or the child has gone
            # (only a child stopped mid-answer could hold this).
            answer = receive.recv_bytes(MAX_REPLY_BYTES)
        except (EOFError, OSError):  # it died (memory, killed) or answered too much
            return "died", "", ""
        kind, cause, text = [*answer.split(_SEP, 2), b"", b""][:3]
        return kind.decode(), cause.decode(), text.decode(errors="replace")
    finally:
        receive.close()
        send.close()
        if child is not None and child.pid is not None:
            # A child that dies while still importing can, rarely, stay unreaped until
            # the next one starts (a multiprocessing edge case): harmless.
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
    stop = threading.Event()
    try:
        work = asyncio.get_running_loop().run_in_executor(
            _WAITERS, _run, parse, filename, raw, limit_s, stop
        )
        # If the caller is cancelled, the work ends its child in its thread (within
        # _POLL_S); nobody waits for it, so its result is dropped quietly.
        work.add_done_callback(lambda done: done.cancelled() or done.exception())
        kind, cause, text = await asyncio.shield(work)
    except asyncio.CancelledError:
        stop.set()
        task = asyncio.current_task()
        if task is not None and task.cancelling():  # the caller was cancelled
            raise
        # Only the read was: DMbot is closing and it never got its turn.
        log.info("Stopped reading a %s file: DMbot is closing", suffix)
        raise DocumentError(UNREADABLE) from None
    except Exception:  # anything else at all (DMbot closing, a bug): plain words
        log.exception("Couldn't read a %s file", suffix)
        raise DocumentError(UNREADABLE) from None
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
    elif kind == "stopped":
        log.info("Stopped reading a %s file: DMbot is closing", suffix)
    else:
        log.warning("Couldn't read a %s file: %s", suffix, cause or kind)
    raise DocumentError(UNREADABLE)
