import unittest

from dmbot.audio.segmenter import Utterance
from dmbot.capture_log import CaptureLog, audio_health


def utt(user: int, seconds: float) -> Utterance:
    return Utterance(1, user, 0, 0, bytes(int(seconds * 32000)))


class CaptureLogTests(unittest.TestCase):
    def test_empty_renders_nothing(self) -> None:
        self.assertIsNone(CaptureLog().render(str))

    def test_all_fine_says_nothing_to_the_dm(self) -> None:
        # #134: no "all fine" checks (and never what was said) in the DM screen.
        log = CaptureLog()
        log.add_utterance(utt(1, 2.0))
        log.add_utterance(utt(1, 1.0))
        log.add_health(1, 147, 150)
        self.assertIsNone(log.render(lambda uid: f"P{uid}"))
        self.assertIsNone(log.log_line())  # and it was reset

    def test_gaps_are_a_plain_warning(self) -> None:
        log = CaptureLog()
        log.add_utterance(utt(1, 1.0))
        log.add_health(1, 30, 50)
        log.add_utterance(utt(2, 3.0))
        log.add_health(2, 50, 50)
        text = log.render(lambda uid: f"P{uid}")
        assert text is not None
        self.assertIn("didn't reach DMbot", text)
        self.assertIn("**P1** 60%", text)
        self.assertNotIn("P2", text)  # only the person with gaps

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
        log.add_utterance(utt(1, 1.0))
        log.add_health(1, 52, 50)
        log.add_health(1, 50, 50)
        self.assertIsNone(log.render(str))
        self.assertEqual(audio_health(120, 100), (100, False))
        self.assertEqual(audio_health(-1, 100), (0, True))

    def test_over_count_cannot_hide_a_gap(self) -> None:
        # 60/50 and 40/50: the second clip lost 20%, which must still show.
        log = CaptureLog()
        log.add_utterance(utt(1, 1.0))
        log.add_health(1, 60, 50)
        log.add_health(1, 40, 50)
        text = log.render(str)
        assert text is not None
        self.assertIn("**1** 90%", text)

    def test_render_shows_rounded_down_and_flag(self) -> None:
        log = CaptureLog()
        log.add_utterance(utt(1, 1.0))
        log.add_health(1, 946, 1000)
        text = log.render(str)
        assert text is not None
        self.assertIn("**1** 94%", text)


class LogLineTests(unittest.TestCase):
    def test_ids_and_numbers_only(self) -> None:
        log = CaptureLog()
        log.add_utterance(utt(22, 2.0))
        log.add_utterance(utt(11, 1.0))
        log.add_health(11, 946, 1000)
        line = log.log_line()
        self.assertEqual(
            line,
            "Capture check: 2 speaker(s); user 11: 1 x speech, 1.0 s, audio 94% (audio gaps); "
            "user 22: 1 x speech, 2.0 s",
        )
        # log_line doesn't reset; render still shows the same check.
        self.assertIsNotNone(log.render(str))
        self.assertIsNone(log.log_line())

    def test_nothing_captured(self) -> None:
        log = CaptureLog()
        self.assertIsNone(log.log_line())
        log.add_health(1, 50, 50)
        self.assertIsNone(log.log_line())
