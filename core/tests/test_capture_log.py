import unittest

from dmbot.audio.segmenter import Utterance
from dmbot.capture_log import CaptureLog


def utt(user: int, seconds: float) -> Utterance:
    return Utterance(1, user, 0, 0, bytes(int(seconds * 32000)))


class CaptureLogTests(unittest.TestCase):
    def test_empty_renders_nothing(self) -> None:
        self.assertIsNone(CaptureLog().render(str))

    def test_summary_and_reset(self) -> None:
        log = CaptureLog()
        log.add_utterance(utt(1, 2.0), None)
        log.add_utterance(utt(1, 1.0), "I cast magic missile")
        log.add_health(1, 147, 150)
        text = log.render(lambda uid: f"P{uid}")
        assert text is not None
        self.assertIn("**P1** — 2 × speech, 3.0 s, audio 98%", text)
        self.assertIn("› I cast magic missile", text)
        self.assertIsNone(log.render(str))

    def test_flags_audio_gaps(self) -> None:
        log = CaptureLog()
        log.add_utterance(utt(1, 1.0), None)
        log.add_health(1, 30, 50)
        text = log.render(str)
        assert text is not None
        self.assertIn("audio 60% ⚠️ audio gaps", text)

    def test_health_alone_is_not_reported(self) -> None:
        log = CaptureLog()
        log.add_health(1, 50, 50)
        self.assertIsNone(log.render(str))
