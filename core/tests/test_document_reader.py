"""PDF and Word files are read in their own process, with a hard time limit (#251).

The parsers below run in a child process, which imports them by name: they must stay
top-level functions of this module."""

import asyncio
import io
import multiprocessing
import os
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from dmbot.memory import document_reader
from dmbot.memory.document_reader import TOO_SLOW, read_document
from dmbot.memory.name_documents import UNREADABLE, DocumentError
from tests.test_memory_name_documents import docx


def hangs(filename: str, raw: bytes) -> str:
    time.sleep(60)
    return "never"


def refuses(filename: str, raw: bytes) -> str:
    raise DocumentError("That PDF is locked with a password.")


def breaks(filename: str, raw: bytes) -> str:
    raise RecursionError("deep")


def dies(filename: str, raw: bytes) -> str:
    os._exit(3)


def memory_limit(filename: str, raw: bytes) -> str:
    import resource

    return str(resource.getrlimit(resource.RLIMIT_AS)[0])


def greedy(filename: str, raw: bytes) -> str:
    return str(len(bytearray(300 * 1024 * 1024)))  # over the 256 MB cap


def niceness(filename: str, raw: bytes) -> str:
    return str(os.nice(0))


def broken_map(filename: str, raw: bytes) -> str:
    return "Belleros \ud800 Ulfgar"  # a lone surrogate, as a damaged PDF can give


def too_long(filename: str, raw: bytes) -> str:
    return "x" * (document_reader.MAX_REPLY_BYTES + 1)


def where(filename: str, raw: bytes) -> str:
    return str(os.getpid())


def secrets_seen(filename: str, raw: bytes) -> str:
    return str(sorted(os.environ))


def blank_pdf() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class NoProcess:
    """A process that can't start (a container out of processes)."""

    pid = None

    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    def start(self) -> None:
        raise OSError("no more processes")


class NoPipe:
    """Out of file descriptors: not even a pipe."""

    @staticmethod
    def Pipe(*_: Any, **__: Any) -> Any:
        raise OSError("too many open files")


class ReadDocument(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        self.assertEqual(multiprocessing.active_children(), [])  # never left running

    async def test_a_word_file_is_read_in_another_process(self) -> None:
        text = await read_document("npcs.docx", docx("Belleros", "Ulfgar"))
        self.assertEqual(text.split(), ["Belleros", "Ulfgar"])
        pid = await read_document("npcs.pdf", b"%PDF", parse=where)
        self.assertNotEqual(int(pid), os.getpid())

    async def test_a_file_that_takes_too_long_is_stopped(self) -> None:
        started = time.monotonic()
        with self.assertRaises(DocumentError) as caught, self.assertLogs(level="WARNING"):
            await read_document("slow.pdf", b"%PDF", parse=hangs, limit_s=1)
        self.assertEqual(str(caught.exception), TOO_SLOW)
        self.assertLess(time.monotonic() - started, 10)

    async def test_plain_words_come_back_as_they_are(self) -> None:
        with self.assertRaisesRegex(DocumentError, "locked with a password"):
            await read_document("locked.pdf", b"%PDF", parse=refuses)

    async def test_any_other_failure_is_unreadable(self) -> None:
        for parse in (breaks, dies):
            with (
                self.subTest(parse.__name__),
                self.assertRaises(DocumentError) as caught,
                self.assertLogs(level="WARNING"),
            ):
                await read_document("bad.docx", b"PK", parse=parse)
            self.assertEqual(str(caught.exception), UNREADABLE)
        with (
            self.assertLogs(document_reader.log, "WARNING") as logs,
            self.assertRaises(DocumentError),
        ):
            await read_document("bad.docx", b"not a zip")  # text_of's own: logged here
        self.assertIn("Couldn't read a .docx file", logs.output[0])

    @unittest.skipUnless(sys.platform == "linux", "the memory limit is Linux only")
    async def test_a_file_cannot_take_all_the_memory(self) -> None:
        limit = await read_document("huge.PDF", b"%PDF", parse=memory_limit)  # any case
        self.assertEqual(int(limit), document_reader.MEMORY_BYTES)
        started = time.monotonic()
        with (
            self.assertRaises(DocumentError) as caught,
            self.assertLogs(document_reader.log, "WARNING") as logs,
        ):
            await read_document("bomb.pdf", b"%PDF", parse=greedy)  # 300 MB: refused
        self.assertEqual(str(caught.exception), UNREADABLE)
        self.assertIn("MemoryError", logs.output[0])
        self.assertLess(time.monotonic() - started, 5)

    @unittest.skipUnless(hasattr(os, "nice"), "Unix only")
    async def test_the_reader_runs_behind_the_bot(self) -> None:
        self.assertGreaterEqual(int(await read_document("a.pdf", b"%PDF", parse=niceness)), 10)

    async def test_a_broken_character_comes_back_replaced(self) -> None:
        # Not "couldn't open": the names around it still come through.
        self.assertEqual(
            await read_document("a.pdf", b"%PDF", parse=broken_map), "Belleros ? Ulfgar"
        )

    async def test_a_reply_too_long_is_unreadable_not_cut_short(self) -> None:
        with (
            self.assertRaises(DocumentError) as caught,
            self.assertLogs(document_reader.log, "WARNING") as logs,
        ):
            await read_document("long.pdf", b"%PDF", parse=too_long)
        self.assertEqual(str(caught.exception), UNREADABLE)
        self.assertIn("ended its process", logs.output[0])

    async def test_a_real_pdf_is_read_with_pypdf_over_there(self) -> None:
        with self.assertRaisesRegex(DocumentError, "pictures of pages"):  # no text in it
            await read_document("blank.pdf", blank_pdf())

    async def test_the_reader_never_sees_the_bots_keys(self) -> None:
        with patch.dict(os.environ, {"DISCORD_TOKEN": "x"}):
            self.assertEqual(await read_document("a.pdf", b"%PDF", parse=secrets_seen), "[]")

    async def test_a_cancelled_read_ends_its_reader_at_once(self) -> None:
        real_run = document_reader._run
        for wait in (0, 0.001, 0.5):  # queued, starting, and reading
            with self.subTest(wait=wait):
                done = threading.Event()

                def run(*args: Any, done: threading.Event = done) -> Any:
                    try:
                        return real_run(*args)
                    finally:
                        done.set()

                started = time.monotonic()
                with patch.object(document_reader, "_run", run):
                    reading = asyncio.create_task(
                        read_document("slow.pdf", b"x" * 10_000_000, parse=hangs, limit_s=30)
                    )
                    await asyncio.sleep(wait)
                    reading.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await reading
                    # The read's thread has ended its child (tearDown checks none is left),
                    # long before the 30 s limit would have.
                    self.assertTrue(await asyncio.to_thread(done.wait, 10))
                self.assertLess(time.monotonic() - started, 10)

    async def test_closing_dmbot_ends_reads_at_once(self) -> None:
        with (
            patch.object(document_reader, "_WAITERS", ThreadPoolExecutor(4)),
            patch.object(document_reader, "_CLOSING", threading.Event()),
        ):
            started = time.monotonic()
            reading = asyncio.create_task(
                read_document("slow.pdf", b"%PDF", parse=hangs, limit_s=30)
            )
            await asyncio.sleep(0.5)
            with self.assertLogs(document_reader.log, "INFO"):
                document_reader.shutdown()
                with self.assertRaises(DocumentError) as caught:
                    await reading
            self.assertEqual(str(caught.exception), UNREADABLE)
            self.assertLess(time.monotonic() - started, 5)  # not the 30 s limit
            with self.assertLogs(document_reader.log, "ERROR"), self.assertRaises(DocumentError):
                await read_document("late.pdf", b"%PDF")  # after closing: plain words too

    async def test_a_reader_that_cannot_open_a_pipe_is_plain_words(self) -> None:
        with (
            patch.object(document_reader, "_CONTEXT", NoPipe),
            self.assertRaises(DocumentError) as caught,
            self.assertLogs(document_reader.log, "WARNING") as logs,
        ):
            await read_document("npcs.pdf", b"%PDF")
        self.assertEqual(str(caught.exception), UNREADABLE)
        self.assertIn("OSError", logs.output[0])

    async def test_any_unexpected_failure_is_plain_words(self) -> None:
        def broken(*_: Any) -> Any:
            raise RuntimeError("a bug")

        with (
            patch.object(document_reader, "_run", broken),
            self.assertRaises(DocumentError) as caught,
            self.assertLogs(document_reader.log, "ERROR"),
        ):
            await read_document("npcs.pdf", b"%PDF")
        self.assertEqual(str(caught.exception), UNREADABLE)

    async def test_a_reader_that_cannot_start_is_plain_words(self) -> None:
        context = SimpleNamespace(Pipe=multiprocessing.Pipe, Process=NoProcess)
        with (
            patch.object(document_reader, "_CONTEXT", context),
            self.assertRaises(DocumentError) as caught,
            self.assertLogs(document_reader.log, "WARNING") as logs,
        ):
            await read_document("npcs.pdf", b"%PDF")
        self.assertEqual(str(caught.exception), UNREADABLE)
        self.assertIn("OSError", logs.output[0])

    async def test_other_files_are_read_here(self) -> None:
        # Text and web pages: quick, and web pages have their own time limit.
        self.assertEqual(await read_document("npcs.txt", b"x", parse=where), str(os.getpid()))
        self.assertEqual(await read_document("Belleros.txt", b"Belleros"), "Belleros")


if __name__ == "__main__":
    unittest.main()
