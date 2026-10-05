import asyncio
import importlib.util
import threading
import unittest
from dataclasses import dataclass
from typing import Any

from dmbot.audio.segmenter import Utterance
from dmbot.transcription.base import TranscriberUnavailable
from dmbot.transcription.config import TranscriptionSettings
from dmbot.transcription.whisper_local import (
    MAX_NEW_TOKENS,
    MAX_PROMPT_TOKENS,
    MIN_NEW_TOKENS,
    WHISPER_MAX_LENGTH,
    LocalWhisperTranscriber,
    SegmentScore,
    keep_segment,
    max_new_tokens,
)

HAS_NUMPY = importlib.util.find_spec("numpy") is not None


@dataclass
class FakeSegment:
    text: str
    no_speech_prob: float = 0.01
    avg_logprob: float = -0.2
    compression_ratio: float = 1.5


class FakeModel:
    def __init__(self, segments: list[FakeSegment]) -> None:
        self.segments = segments
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, audio: Any, **kwargs: Any) -> tuple[list[FakeSegment], None]:
        self.calls.append({"samples": len(audio), "dtype": str(audio.dtype), **kwargs})
        return self.segments, None


class DecodingBoundsTests(unittest.TestCase):
    def test_token_budget_follows_clip_length(self) -> None:
        # #137: a 1 s clip may not run to Whisper's full ~448 tokens.
        self.assertEqual(max_new_tokens(0.2), MIN_NEW_TOKENS)
        self.assertEqual(max_new_tokens(1.0), MIN_NEW_TOKENS)
        self.assertEqual(max_new_tokens(10.0), 120)
        self.assertEqual(max_new_tokens(15.0), 180)  # DMbot's longest piece of speech
        self.assertEqual(max_new_tokens(60.0), MAX_NEW_TOKENS)
        # Generous: 15 s of fast speech (~5 words/s, ~7 tokens/s) still fits.
        self.assertGreater(max_new_tokens(15.0), 15 * 7)

    def test_prompt_and_output_fit_whisper_limit(self) -> None:
        # faster-whisper raises if prompt + output tokens exceed 448.
        self.assertLessEqual(MAX_PROMPT_TOKENS + MAX_NEW_TOKENS, WHISPER_MAX_LENGTH)


class KeepSegmentTests(unittest.TestCase):
    def test_keeps_confident_speech(self) -> None:
        self.assertTrue(keep_segment(SegmentScore("I attack", 0.05, -0.3)))

    def test_drops_likely_hallucination(self) -> None:
        self.assertFalse(keep_segment(SegmentScore("Thanks for watching!", 0.9, -1.5)))

    def test_keeps_if_only_one_signal_is_bad(self) -> None:
        self.assertTrue(keep_segment(SegmentScore("quiet but sure", 0.9, -0.2)))
        self.assertTrue(keep_segment(SegmentScore("loud but unsure", 0.1, -1.5)))

    def test_drops_repeating_text(self) -> None:
        # #137: with one temperature there's no retry, so loops are dropped here.
        looping = "the the the the the the the the the the"
        self.assertFalse(keep_segment(SegmentScore(looping, 0.05, -0.3, 3.1)))
        self.assertTrue(keep_segment(SegmentScore("I attack", 0.05, -0.3, 1.2)))


class WorkerThreadTests(unittest.IsolatedAsyncioTestCase):
    async def test_skipped_clip_still_decoding_blocks_the_next(self) -> None:
        # #137: cancelling a clip releases the lock, but its thread runs on. The next
        # clip must wait for it rather than decode alongside it.
        t = LocalWhisperTranscriber(TranscriptionSettings())
        release = threading.Event()
        started: list[str] = []

        def work(name: str) -> str:
            started.append(name)
            if name == "first":
                release.wait(5)
            return name

        first = asyncio.ensure_future(t._in_worker(work, "first"))
        await asyncio.sleep(0.05)
        first.cancel()  # what the pipeline's time budget does
        second = asyncio.ensure_future(t._in_worker(work, "second"))
        await asyncio.sleep(0.1)
        self.assertEqual(started, ["first"])
        release.set()
        self.assertEqual(await second, "second")
        self.assertEqual(started, ["first", "second"])
        await t.close()


@unittest.skipUnless(HAS_NUMPY, "numpy not installed")
class LocalWhisperTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcribes_with_prompt_and_filters(self) -> None:
        t = LocalWhisperTranscriber(TranscriptionSettings())
        model = FakeModel(
            [
                FakeSegment(" I cast"),
                FakeSegment(" ghost", 0.95, -2.0),
                FakeSegment(" the the the the the", compression_ratio=3.0),
                FakeSegment(" Magic Missile. "),
            ]
        )
        t._model = model
        text = await t.transcribe(Utterance(1, 2, 0, 0, bytes(32000)), ["Aria", "Bram"])
        self.assertEqual(text, "I cast Magic Missile.")
        call = model.calls[0]
        self.assertEqual((call["samples"], call["dtype"]), (16000, "float32"))
        self.assertEqual(call["initial_prompt"], "Names: Aria, Bram.")
        # #137: one greedy pass, with output length bounded by the clip's length.
        self.assertEqual(call["temperature"], 0.0)
        self.assertEqual(call["max_new_tokens"], max_new_tokens(1.0))
        self.assertEqual(call["language"], "en")
        self.assertTrue(call["vad_filter"])
        self.assertEqual(call["beam_size"], 1)

    async def test_auto_language_passes_none(self) -> None:
        t = LocalWhisperTranscriber(TranscriptionSettings(language=""))
        model = FakeModel([FakeSegment("bonjour")])
        t._model = model
        await t.transcribe(Utterance(1, 2, 0, 0, bytes(3200)), [])
        self.assertIsNone(model.calls[0]["language"])

    async def test_nothing_usable(self) -> None:
        t = LocalWhisperTranscriber(TranscriptionSettings())
        t._model = FakeModel([])
        self.assertIsNone(await t.transcribe(Utterance(1, 2, 0, 0, bytes(320)), []))

    async def test_missing_package_message(self) -> None:
        if importlib.util.find_spec("faster_whisper") is not None:
            self.skipTest("faster-whisper is installed")
        t = LocalWhisperTranscriber(TranscriptionSettings())
        with self.assertRaisesRegex(TranscriberUnavailable, r"\[whisper\]"):
            await t.warm_up()
