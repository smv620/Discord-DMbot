import unittest

from dmbot.config import ConfigError, load_settings
from dmbot.transcription.config import (
    TranscriptionConfigError,
    TranscriptionSettings,
    load_transcription_settings,
)


class TranscriptionConfigTests(unittest.TestCase):
    def test_defaults_to_local_whisper(self) -> None:
        s = load_transcription_settings({})
        self.assertEqual(s, TranscriptionSettings())
        self.assertEqual((s.engine, s.whisper_model, s.language), ("whisper-local", "small", "en"))
        self.assertEqual((s.whisper_compute_type, s.whisper_beam_size), ("auto", 1))

    def test_language_auto(self) -> None:
        self.assertEqual(load_transcription_settings({"TRANSCRIBE_LANGUAGE": "auto"}).language, "")

    def test_beam_size_validated(self) -> None:
        self.assertEqual(
            load_transcription_settings({"WHISPER_BEAM_SIZE": "3"}).whisper_beam_size, 3
        )
        for bad in ("0", "x", "-1"):
            with self.assertRaises(TranscriptionConfigError):
                load_transcription_settings({"WHISPER_BEAM_SIZE": bad})

    def test_rejects_unknown_engine(self) -> None:
        with self.assertRaisesRegex(TranscriptionConfigError, "TRANSCRIBER must be one of"):
            load_transcription_settings({"TRANSCRIBER": "magic"})

    def test_cloud_needs_key(self) -> None:
        with self.assertRaisesRegex(TranscriptionConfigError, "CLOUD_STT_API_KEY"):
            load_transcription_settings({"TRANSCRIBER": "cloud"})

    def test_cloud_requires_https(self) -> None:
        with self.assertRaisesRegex(TranscriptionConfigError, "https"):
            load_transcription_settings(
                {"TRANSCRIBER": "cloud", "CLOUD_STT_API_KEY": "k", "CLOUD_STT_URL": "http://x"}
            )

    def test_cloud_settings(self) -> None:
        s = load_transcription_settings(
            {"TRANSCRIBER": "cloud", "CLOUD_STT_API_KEY": "k", "CLOUD_STT_MODEL": "m"}
        )
        self.assertEqual((s.engine, s.cloud_api_key, s.cloud_model), ("cloud", "k", "m"))

    def test_deepgram_needs_key(self) -> None:
        with self.assertRaisesRegex(TranscriptionConfigError, "DEEPGRAM_API_KEY"):
            load_transcription_settings({"TRANSCRIBER": "deepgram"})

    def test_deepgram_requires_https(self) -> None:
        with self.assertRaisesRegex(TranscriptionConfigError, "https"):
            load_transcription_settings(
                {
                    "TRANSCRIBER": "deepgram",
                    "DEEPGRAM_API_KEY": "k",
                    "DEEPGRAM_LISTEN_URL": "http://x",
                }
            )

    def test_deepgram_settings_and_defaults(self) -> None:
        s = load_transcription_settings({"TRANSCRIBER": "deepgram", "DEEPGRAM_API_KEY": "k"})
        self.assertEqual(
            (s.engine, s.deepgram_api_key, s.deepgram_model), ("deepgram", "k", "nova-3")
        )
        self.assertEqual(s.deepgram_url, "https://api.deepgram.com/v1/listen")
        blank = load_transcription_settings(
            {
                "TRANSCRIBER": "deepgram",
                "DEEPGRAM_API_KEY": "k",
                "DEEPGRAM_MODEL": " ",
                "DEEPGRAM_LISTEN_URL": "",
            }
        )
        self.assertEqual(
            (blank.deepgram_model, blank.deepgram_url), (s.deepgram_model, s.deepgram_url)
        )

    def test_the_bakeoff_key_alone_doesnt_switch_engines(self) -> None:
        s = load_transcription_settings({"DEEPGRAM_API_KEY": "k"})
        self.assertEqual(s.engine, "whisper-local")
        self.assertFalse(s.sends_audio_out)

    def test_errors_surface_through_main_settings(self) -> None:
        with self.assertRaises(ConfigError):
            load_settings(
                {
                    "DISCORD_TOKEN": "t",
                    "EARS_SHARED_SECRET": "s",
                    "DATABASE_URL": "postgresql://x",
                    "TRANSCRIBER": "x",
                }
            )
