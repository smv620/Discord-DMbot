# ruff: noqa: RUF001, RUF002, RUF003  (IPA letters look like Latin ones; they are the point here)
"""The speech-sound worker's manager (#2316): a stand-in worker speaks the same protocol, so the
start, the one-at-a-time rule, the idle unload, a crash and a missing model are all checked
without a machine-learning runtime."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from dmbot.said_as.recogniser import FAILED, VOICE_OFF, RecogniserError, WorkerRecogniser, frame

# Answers "kɑzɛθ" for any clip, "crash" makes it die, and it reports how many clips it has heard.
WORKER = textwrap.dedent(
    """
    import json, struct, sys
    mode = sys.argv[1]
    if mode == "noload":
        sys.exit(3)
    sys.stdout.write(json.dumps({"ready": True}) + "\\n"); sys.stdout.flush()
    heard = 0
    while True:
        head = sys.stdin.buffer.read(4)
        if len(head) < 4:
            sys.exit(0)
        (size,) = struct.unpack(">I", head)
        pcm = sys.stdin.buffer.read(size)
        heard += 1
        if mode == "crash":
            sys.exit(1)
        sys.stdout.write(json.dumps({"ipa": "kɑzɛθ", "heard": heard}) + "\\n"); sys.stdout.flush()
    """
)


class Managing(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.script = Path(self.tmp.name) / "worker.py"
        self.script.write_text(WORKER, encoding="utf-8")
        self.model = self.tmp.name  # any folder: the stand-in ignores it
        self.now = [100.0]

    def manager(self, mode: str = "ok", **kw: object) -> WorkerRecogniser:
        found = WorkerRecogniser(
            self.model,
            command=[sys.executable, "-I", str(self.script), mode],
            clock=lambda: self.now[0],
            **kw,  # type: ignore[arg-type]
        )
        self.addAsyncCleanup(found.close)
        return found

    async def test_the_sounds_come_back_and_the_worker_stays_loaded(self) -> None:
        found = self.manager()
        self.assertFalse(found.loaded)
        self.assertEqual(await found.recognise(b"\x00\x01" * 100), "kɑzɛθ")
        self.assertTrue(found.loaded)
        self.assertEqual(await found.recognise(b"\x00\x01" * 100), "kɑzɛθ")  # the same worker

    async def test_clips_are_taken_one_at_a_time(self) -> None:
        found = self.manager()
        answers = await asyncio.gather(*(found.recognise(b"\x00\x00" * 50) for _ in range(4)))
        self.assertEqual(answers, ["kɑzɛθ"] * 4)

    async def test_it_unloads_after_the_idle_time_and_not_before(self) -> None:
        found = self.manager(idle_s=600)
        await found.recognise(b"\x00\x00" * 50)
        self.now[0] += 599
        await found.unload_if_idle()
        self.assertTrue(found.loaded)
        self.now[0] += 2
        await found.unload_if_idle()
        self.assertFalse(found.loaded)
        self.assertEqual(await found.recognise(b"\x00\x00" * 50), "kɑzɛθ")  # a fresh one starts

    async def test_a_worker_that_dies_is_failed_and_the_next_clip_starts_a_new_one(self) -> None:
        found = self.manager("crash")
        with self.assertRaises(RecogniserError) as caught:
            await found.recognise(b"\x00\x00" * 50)
        self.assertEqual(caught.exception.code, FAILED)
        self.assertFalse(found.loaded)

    async def test_a_model_that_will_not_load_or_is_missing_says_the_voice_is_off(self) -> None:
        with self.assertRaises(RecogniserError) as caught:
            await self.manager("noload").recognise(b"\x00\x00" * 50)
        self.assertEqual(caught.exception.code, VOICE_OFF)
        for folder in ("", str(Path(self.tmp.name) / "nowhere")):
            nobody = WorkerRecogniser(folder, command=[sys.executable, str(self.script), "ok"])
            with self.subTest(folder=folder[-8:]), self.assertRaises(RecogniserError) as caught:
                await nobody.recognise(b"\x00\x00")
            self.assertEqual(caught.exception.code, VOICE_OFF)
            self.assertFalse(nobody.loaded)  # nothing was started

    def test_a_request_is_the_length_then_the_audio(self) -> None:
        self.assertEqual(frame(b"abc"), b"\x00\x00\x00\x03abc")
