import unittest

from dmbot.audio.segmenter import Utterance
from dmbot.capture_log import CaptureLog, audio_health


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


class AudioHealthTests(unittest.TestCase):
    def test_flagged_never_shows_95_or_more(self) -> None:
        # #44: 94.6% used to show as "audio 95% ⚠️ audio gaps".
        self.assertEqual(audio_health(946, 1000), (94, True))
        for expected in range(1, 400):
            for received in range(expected + 1):
                percent, flagged = audio_health(received, expected)
                self.assertEqual(flagged, percent < 95, (received, expected))

    def test_threshold_is_exactly_95(self) -> None:
        self.assertEqual(audio_health(95, 100), (95, False))
        self.assertEqual(audio_health(19, 20), (95, False))
        self.assertEqual(audio_health(949, 1000), (94, True))
        self.assertEqual(audio_health(100, 100), (100, False))

    def test_summed_health_stays_capped(self) -> None:
        # One interval can hold several reports; the total can't pass 100%.
        log = CaptureLog()
        log.add_utterance(utt(1, 1.0), None)
        log.add_health(1, 52, 50)
        log.add_health(1, 50, 50)
        text = log.render(str)
        assert text is not None
        self.assertIn("audio 100%", text)
        self.assertNotIn("⚠️", text)
        self.assertEqual(audio_health(120, 100), (100, False))
        self.assertEqual(audio_health(-1, 100), (0, True))

    def test_render_shows_rounded_down_and_flag(self) -> None:
        log = CaptureLog()
        log.add_utterance(utt(1, 1.0), None)
        log.add_health(1, 946, 1000)
        text = log.render(str)
        assert text is not None
        self.assertIn("audio 94% ⚠️ audio gaps", text)
