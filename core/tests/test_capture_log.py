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
        self.assertIn("**P1's voice is cutting out for DMbot** (60% got through)", text)
        self.assertIn("rejoin voice", text)  # what to do about it
        self.assertNotIn("P2", text)  # only the person with gaps

    def test_the_dm_is_not_told_again_unless_it_gets_worse(self) -> None:
        log = CaptureLog()

        def check(received: int, now: float) -> str | None:
            log.add_utterance(utt(1, 1.0))
            log.add_health(1, received, 100)
            return log.render(str, now)

        self.assertIsNotNone(check(80, 0))
        self.assertIsNone(check(80, 15))  # same again: quiet
        self.assertIsNone(check(75, 30))  # a little worse: quiet
        self.assertIsNotNone(check(70, 45))  # clearly worse
        self.assertIsNone(check(70, 60))
        self.assertIsNotNone(check(70, 45 + 600))  # ten minutes on: once more

    def test_borderline_audio_stays_in_the_logs(self) -> None:
        log = CaptureLog()
        log.add_utterance(utt(1, 1.0))
        log.add_health(1, 92, 100)
        self.assertIn("(audio gaps)", log.log_line() or "")
        self.assertIsNone(log.render(str))

    def test_several_people_in_one_warning(self) -> None:
        log = CaptureLog()
        for user, got in ((1, 80), (2, 85)):
            log.add_utterance(utt(user, 1.0))
            log.add_health(user, got, 100)
        text = log.render(lambda uid: f"P{uid}")
        assert text is not None
        self.assertIn("Voices cutting out for DMbot:** P1 80%, P2 85%", text)

    def test_health_alone_is_not_reported(self) -> None:
        log = CaptureLog()
        log.add_health(1, 50, 50)
        self.assertIsNone(log.render(str))

    def test_health_waits_for_its_speech(self) -> None:
        # #120: ears reports health when a stream ends; the speech only arrives once
        # transcribed. A check in between must not throw the counts away.
        log = CaptureLog()
        log.add_health(1, 50, 100)
        self.assertIsNone(log.log_line())
        self.assertIsNone(log.render(str))
        log.add_utterance(utt(1, 2.0))
        self.assertIn("user 1: 1 x speech, 2.0 s, audio 50%", log.log_line() or "")
        self.assertIn("(50% got through)", log.render(lambda uid: f"P{uid}") or "")

    def test_only_speakers_in_a_check_are_cleared(self) -> None:
        log = CaptureLog()
        log.add_utterance(utt(1, 1.0))
        log.add_health(1, 60, 100)
        self.assertIsNotNone(log.render(str))
        log.add_health(2, 40, 100)  # health only: not in the next check yet
        self.assertIsNone(log.render(str))
        self.assertIsNone(log.log_line())  # speaker 1 was cleared by the first check
        log.add_utterance(utt(2, 1.0))
        self.assertEqual(
            log.log_line(),
            "Capture check: 1 speaker(s); user 2: 1 x speech, 1.0 s, audio 40% (audio gaps)",
        )

    def test_an_early_speaker_is_kept_and_a_complete_one_cleared(self) -> None:
        log = CaptureLog()
        log.add_health(1, 70, 100)  # early: speech still being transcribed
        log.add_utterance(utt(2, 1.0))
        log.add_health(2, 80, 100)  # complete
        text = log.render(lambda uid: f"P{uid}")
        self.assertEqual(text and text.split(" (")[0], "⚠️ **P2's voice is cutting out for DMbot**")
        log.add_utterance(utt(1, 1.0))
        self.assertEqual(
            log.log_line(),
            "Capture check: 1 speaker(s); user 1: 1 x speech, 1.0 s, audio 70% (audio gaps)",
        )
        self.assertIn(
            "**P1's voice is cutting out for DMbot** (70% got through)",
            log.render(lambda uid: f"P{uid}") or "",
        )
        self.assertIsNone(log.log_line())

    def test_speech_without_health_is_still_cleared(self) -> None:
        log = CaptureLog()
        log.add_utterance(utt(1, 1.0))
        self.assertIsNone(log.render(str))
        self.assertIsNone(log.log_line())


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
        log.add_health(1, 30, 50)
        text = log.render(str)
        assert text is not None
        self.assertIn("(80% got through)", text)

    def test_render_shows_rounded_down_and_flag(self) -> None:
        log = CaptureLog()
        log.add_utterance(utt(1, 1.0))
        log.add_health(1, 896, 1000)
        text = log.render(str)
        assert text is not None
        self.assertIn("(89% got through)", text)


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
        # log_line doesn't reset; render does.
        self.assertEqual(log.log_line(), line)
        log.render(str)
        self.assertIsNone(log.log_line())

    def test_nothing_captured(self) -> None:
        log = CaptureLog()
        self.assertIsNone(log.log_line())
        log.add_health(1, 50, 50)
        self.assertIsNone(log.log_line())
