# ruff: noqa: RUF003  (IPA letters look like Latin ones; they are the point here)
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
from typing import Any

from dmbot.said_as.recogniser import FAILED, VOICE_OFF, RecogniserError, WorkerRecogniser, frame

# Answers "kɑzɛθ" for any clip, "crash" makes it die, and it reports how many clips it has heard.
WORKER = textwrap.dedent(
    """
    import json, struct, sys, time
    mode = sys.argv[1]
    if mode == "noload":
        sys.exit(3)
    sys.stdout.write(json.dumps({"ready": True}) + "\\n"); sys.stdout.flush()
    heard = 0
    while True:
        head = sys.stdin.buffer.read(8)
        if len(head) < 8:
            sys.exit(0)
        number, size = struct.unpack(">II", head)
        pcm = sys.stdin.buffer.read(size)
        heard += 1
        if mode == "crash":
            sys.exit(1)
        if mode == "slow" and heard == 1:
            time.sleep(0.5)
        n = number + 1 if mode == "stale" else number
        sys.stdout.write(json.dumps({"n": n, "ipa": "kɑzɛθ"}) + "\\n"); sys.stdout.flush()
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

    async def test_a_clip_stopped_half_way_does_not_answer_the_next_one(self) -> None:
        found = self.manager("slow")
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(found.recognise(b"\x00\x00" * 50), 0.1)
        self.assertFalse(found.loaded)  # the worker went with the cancelled clip
        self.assertEqual(await found.recognise(b"\x00\x00" * 50), "kɑzɛθ")  # a fresh one

    async def test_a_reply_for_another_request_is_never_taken(self) -> None:
        with self.assertRaises(RecogniserError) as caught:
            await self.manager("stale").recognise(b"\x00\x00" * 50)
        self.assertEqual(caught.exception.code, FAILED)

    async def test_close_stops_the_worker(self) -> None:
        found = self.manager()
        await found.recognise(b"\x00\x00" * 50)
        await found.close()
        self.assertFalse(found.loaded)

    def test_a_request_is_its_number_then_the_length_then_the_audio(self) -> None:
        self.assertEqual(frame(7, b"abc"), b"\x00\x00\x00\x07\x00\x00\x00\x03abc")


class TheWorkerProgram(unittest.TestCase):
    """`worker.main` with the runtime stood in for: the ready line, the numbered answers, the
    refusals, and that nothing but the protocol's lines reaches the pipe."""

    def run_main(self, requests: bytes) -> tuple[int, list[dict[str, Any]]]:
        import io
        import json
        from unittest.mock import patch

        from dmbot.said_as import worker

        out = io.StringIO()
        stdin = type("S", (), {"buffer": io.BytesIO(requests)})()
        with (
            patch.object(worker, "load", return_value=(None, None, None)),
            patch.object(worker, "recognise", return_value="kɑzɛθ" * 400),
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", out),
        ):
            code = worker.main(["worker", "/model"])
        return code, [json.loads(line) for line in out.getvalue().splitlines()]

    def test_it_says_ready_then_answers_each_request_by_number(self) -> None:
        code, lines = self.run_main(frame(1, b"\x00\x00") + frame(2, b"\x00\x00" * 5))
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], {"ready": True})
        self.assertEqual([line["n"] for line in lines[1:]], [1, 2])
        self.assertLessEqual(len(lines[1]["ipa"]), 1000)  # a long answer is cut

    def test_an_oversize_request_ends_it_and_a_cut_one_ends_quietly(self) -> None:
        from dmbot.said_as import worker

        big = b"\x00\x00\x00\x01" + (worker.MAX_BYTES + 1).to_bytes(4, "big")
        self.assertEqual(self.run_main(big)[0], 4)
        short = b"\x00\x00\x00\x01\x00\x00\x00\x09ab"
        self.assertEqual(self.run_main(short)[0], 0)

    def test_the_wrong_number_of_arguments_is_refused(self) -> None:
        from dmbot.said_as import worker

        self.assertEqual(worker.main(["worker"]), 2)
