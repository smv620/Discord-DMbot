import io
import unittest
import wave

from dmbot.audio.segmenter import Utterance
from dmbot.transcription.base import (
    MAX_HINT_CHARS,
    PlaceholderTranscriber,
    build_prompt,
    clean_text,
    to_wav,
)
from dmbot.transcription.cloud import CloudTranscriber
from dmbot.transcription.config import TranscriptionSettings
from dmbot.transcription.factory import build_transcriber
from dmbot.transcription.whisper_local import LocalWhisperTranscriber


class PromptTests(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertIsNone(build_prompt([]))
        self.assertIsNone(build_prompt(["  ", ""]))

    def test_dedupes_and_normalises(self) -> None:
        self.assertEqual(
            build_prompt(["Strahd", "strahd", " Ireena  Kolyana "]),
            "Names: Strahd, Ireena Kolyana.",
        )

    def test_caps_length(self) -> None:
        prompt = build_prompt([f"Name{i:04d}" for i in range(500)])
        assert prompt is not None
        self.assertLessEqual(len(prompt), MAX_HINT_CHARS + len("Names: ."))


class HelperTests(unittest.TestCase):
    def test_to_wav(self) -> None:
        data = to_wav(bytes(3200))
        with wave.open(io.BytesIO(data)) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 16000))
            self.assertEqual(w.getnframes(), 1600)

    def test_clean_text(self) -> None:
        self.assertEqual(clean_text("  I  cast\nfireball "), "I cast fireball")
        self.assertIsNone(clean_text("   "))


class FactoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_engines(self) -> None:
        self.assertIsInstance(
            build_transcriber(TranscriptionSettings(engine="none")), PlaceholderTranscriber
        )
        self.assertIsInstance(build_transcriber(TranscriptionSettings()), LocalWhisperTranscriber)
        cloud = build_transcriber(TranscriptionSettings(engine="cloud", cloud_api_key="k"))
        self.assertIsInstance(cloud, CloudTranscriber)
        await cloud.close()

    async def test_placeholder(self) -> None:
        t = PlaceholderTranscriber()
        self.assertIsNone(await t.transcribe(Utterance(1, 2, 0, 0, b""), []))
        await t.close()
