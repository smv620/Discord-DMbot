import importlib.util
import unittest
from dataclasses import dataclass
from typing import Any

from dmbot.audio.segmenter import Utterance
from dmbot.transcription.base import TranscriberUnavailable
from dmbot.transcription.config import TranscriptionSettings
from dmbot.transcription.whisper_local import (
    LocalWhisperTranscriber,
    SegmentScore,
    keep_segment,
)

HAS_NUMPY = importlib.util.find_spec("numpy") is not None


@dataclass
class FakeSegment:
    text: str
    no_speech_prob: float = 0.01
    avg_logprob: float = -0.2


class FakeModel:
    def __init__(self, segments: list[FakeSegment]) -> None:
        self.segments = segments
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, audio: Any, **kwargs: Any) -> tuple[list[FakeSegment], None]:
        self.calls.append({"samples": len(audio), "dtype": str(audio.dtype), **kwargs})
        return self.segments, None


class KeepSegmentTests(unittest.TestCase):
    def test_keeps_confident_speech(self) -> None:
        self.assertTrue(keep_segment(SegmentScore("I attack", 0.05, -0.3)))

    def test_drops_likely_hallucination(self) -> None:
        self.assertFalse(keep_segment(SegmentScore("Thanks for watching!", 0.9, -1.5)))

    def test_keeps_if_only_one_signal_is_bad(self) -> None:
        self.assertTrue(keep_segment(SegmentScore("quiet but sure", 0.9, -0.2)))
        self.assertTrue(keep_segment(SegmentScore("loud but unsure", 0.1, -1.5)))


@unittest.skipUnless(HAS_NUMPY, "numpy not installed")
class LocalWhisperTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcribes_with_prompt_and_filters(self) -> None:
        t = LocalWhisperTranscriber(TranscriptionSettings())
        model = FakeModel(
            [
                FakeSegment(" I cast"),
                FakeSegment(" ghost", 0.95, -2.0),
                FakeSegment(" Magic Missile. "),
            ]
        )
        t._model = model
        text = await t.transcribe(Utterance(1, 2, 0, 0, bytes(32000)), ["Aria", "Bram"])
        self.assertEqual(text, "I cast Magic Missile.")
        call = model.calls[0]
        self.assertEqual((call["samples"], call["dtype"]), (16000, "float32"))
        self.assertEqual(call["initial_prompt"], "Names: Aria, Bram.")
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
