import unittest
from collections.abc import Sequence

from dmbot.audio.segmenter import Utterance
from dmbot.capture_log import (
    HEALTH_WAIT_CHECKS,
    HEALTH_WAIT_MAX_CHECKS,
    CaptureLog,
    audio_health,
)


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
        log.add_health(1, 1470, 1500)
        self.assertIsNone(log.render(lambda uid: f"P{uid}"))
        self.assertIsNone(log.log_line())  # and it was reset

    def test_gaps_are_a_plain_warning(self) -> None:
        log = CaptureLog()
        log.add_utterance(utt(1, 1.0))
        log.add_health(1, 300, 500)
        log.add_utterance(utt(2, 3.0))
        log.add_health(2, 500, 500)
        text = log.render(lambda uid: f"P{uid}")
        assert text is not None
        self.assertIn("**P1's voice is cutting out for DMbot** (60% got through)", text)
        self.assertIn("rejoin voice", text)  # what to do about it
        self.assertNotIn("P2", text)  # only the person with gaps

    def test_the_dm_is_not_told_again_unless_it_gets_worse(self) -> None:
        log = CaptureLog()

        def check(received: int, now: float) -> str | None:
            log.add_utterance(utt(1, 1.0))
            log.add_health(1, received * 10, 1000, now)
            return log.render(str, now)

        # Checks more than a window apart, so each judges its own minute.
        self.assertIsNotNone(check(80, 0))
        self.assertIsNone(check(80, 70))  # same again: quiet
        self.assertIsNone(check(75, 140))  # a little worse: quiet
        self.assertIsNotNone(check(70, 210))  # clearly worse
        self.assertIsNone(check(70, 280))
        self.assertIsNotNone(check(70, 210 + 600))  # ten minutes on: once more

    def test_borderline_audio_stays_in_the_logs(self) -> None:
        log = CaptureLog()
        log.add_utterance(utt(1, 1.0))
        log.add_health(1, 920, 1000)
        self.assertIn("(audio gaps)", log.log_line() or "")
        self.assertIsNone(log.render(str))

    def test_several_people_in_one_warning(self) -> None:
        log = CaptureLog()
        for user, got in ((1, 80), (2, 85)):
            log.add_utterance(utt(user, 1.0))
            log.add_health(user, got * 10, 1000)
        text = log.render(lambda uid: f"P{uid}")
        assert text is not None
        self.assertIn("Voices cutting out for DMbot:** P1 80%, P2 85%", text)

    def test_health_alone_is_not_reported(self) -> None:
        log = CaptureLog()
        log.add_health(1, 500, 500)
        self.assertIsNone(log.render(str))

    def test_health_waits_for_its_speech(self) -> None:
        # #120: ears reports health when a stream ends; the speech only arrives once
        # transcribed. A check in between must not throw the counts away.
        log = CaptureLog()
        log.add_health(1, 500, 1000)
        self.assertIsNone(log.log_line())
        self.assertIsNone(log.render(str))
        log.add_utterance(utt(1, 2.0))
        self.assertIn("user 1: 1 x speech, 2.0 s, audio 50%", log.log_line() or "")
        self.assertIn("(50% got through)", log.render(lambda uid: f"P{uid}") or "")

    def test_only_speakers_in_a_check_are_cleared(self) -> None:
        log = CaptureLog()
        log.add_utterance(utt(1, 1.0))
        log.add_health(1, 600, 1000)
        self.assertIsNotNone(log.render(str))
        log.add_health(2, 400, 1000)  # health only: not in the next check yet
        self.assertIsNone(log.render(str))
        self.assertIsNone(log.log_line())  # speaker 1 was cleared by the first check
        log.add_utterance(utt(2, 1.0))
        self.assertEqual(
            log.log_line(),
            "Capture check: 1 speaker(s); user 2: 1 x speech, 1.0 s, audio 40% (audio gaps)",
        )

    def test_an_early_speaker_is_kept_and_a_complete_one_cleared(self) -> None:
        log = CaptureLog()
        log.add_health(1, 700, 1000)  # early: speech still being transcribed
        log.add_utterance(utt(2, 1.0))
        log.add_health(2, 800, 1000)  # complete
        text = log.render(lambda uid: f"P{uid}")
        assert text is not None
        self.assertTrue(text.startswith("⚠️ **P2's voice is cutting out for DMbot**"), text)
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
        log.add_health(1, 520, 500)
        log.add_health(1, 500, 500)
        self.assertIsNone(log.render(str))
        self.assertEqual(audio_health(120, 100), (100, False))
        self.assertEqual(audio_health(-1, 100), (0, True))

    def test_over_count_cannot_hide_a_gap(self) -> None:
        # 60/50 and 40/50: the second clip lost 20%, which must still show.
        log = CaptureLog()
        log.add_utterance(utt(1, 1.0))
        log.add_health(1, 600, 500)
        log.add_health(1, 300, 500)
        text = log.render(str)
        assert text is not None
        self.assertIn("(80% got through)", text)

    def test_render_shows_rounded_down_and_flag(self) -> None:
        log = CaptureLog()
        log.add_utterance(utt(1, 1.0))
        log.add_health(1, 8960, 10000)
        text = log.render(str)
        assert text is not None
        self.assertIn("(89% got through)", text)


class LogLineTests(unittest.TestCase):
    def test_ids_and_numbers_only(self) -> None:
        log = CaptureLog()
        log.add_utterance(utt(22, 2.0))
        log.add_utterance(utt(11, 1.0))
        log.add_health(11, 9460, 10000)
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
        log.add_health(1, 500, 500)
        self.assertIsNone(log.log_line())

    def test_health_from_several_streams_adds_up_while_it_waits(self) -> None:
        log = CaptureLog()
        log.add_health(1, 500, 1000)
        self.assertIsNone(log.render(str))
        log.add_health(1, 1200, 1000)  # capped at 100 per report
        self.assertIsNone(log.render(str))
        log.add_utterance(utt(1, 1.0))
        self.assertIn("audio 75%", log.log_line() or "")

    def test_health_whose_speech_never_comes_is_dropped(self) -> None:
        # The person opted out mid-speech, or the speech queue was full: their gaps
        # mustn't turn up in a clean check later on.
        log = CaptureLog()
        log.add_health(1, 100, 1000)
        for _ in range(HEALTH_WAIT_CHECKS):
            self.assertIsNone(log.render(str))
        log.add_utterance(utt(1, 1.0))
        self.assertIn("audio 10%", log.log_line() or "")  # still waiting: kept
        log.render(str)
        log.add_health(1, 100, 1000)
        for _ in range(HEALTH_WAIT_CHECKS + 1):
            self.assertIsNone(log.render(str))
        log.add_utterance(utt(1, 1.0))
        log.add_health(1, 1000, 1000)
        self.assertIn("audio 100%", log.log_line() or "")  # waited too long: dropped

    def test_one_check_clears_several_speakers(self) -> None:
        log = CaptureLog()
        for user in (1, 2, 3):
            log.add_utterance(utt(user, float(user)))
            log.add_health(user, 500, 1000)
        text = log.render(lambda uid: f"P{uid}")
        self.assertIn("P3 50%, P2 50%, P1 50%", text or "")
        self.assertIsNone(log.log_line())

    def test_health_is_paired_per_speaker_not_per_piece(self) -> None:
        # Known limit: a check takes all of a speaker's health so far, including a piece
        # still being transcribed; that piece then arrives with no %. Nothing is lost.
        log = CaptureLog()
        log.add_utterance(utt(1, 1.0))
        log.add_health(1, 1000, 1000)
        log.add_health(1, 500, 1000)  # the next piece, not transcribed yet
        self.assertIn("audio 75%", log.log_line() or "")
        log.render(str)
        log.add_utterance(utt(1, 1.0))
        self.assertEqual(log.log_line(), "Capture check: 1 speaker(s); user 1: 1 x speech, 1.0 s")

    def test_the_wait_counts_from_the_latest_health(self) -> None:
        log = CaptureLog()
        log.add_health(1, 500, 1000)  # before check 1
        log.render(str)
        log.render(str)
        log.add_health(1, 200, 1000)  # before check 3, after waiting 2 checks
        for _ in range(HEALTH_WAIT_CHECKS):  # checks 3-6
            self.assertIsNone(log.render(str))
        log.add_utterance(utt(1, 1.0))
        self.assertIn("audio 35%", log.log_line() or "")  # both reports still there

    def test_the_wait_ends_after_the_latest_health(self) -> None:
        log = CaptureLog()
        log.add_health(1, 500, 1000)
        log.render(str)
        log.add_health(1, 200, 1000)
        for _ in range(HEALTH_WAIT_CHECKS + 1):
            log.render(str)
        log.add_utterance(utt(1, 1.0))
        self.assertEqual(log.log_line(), "Capture check: 1 speaker(s); user 1: 1 x speech, 1.0 s")

    def test_health_that_keeps_coming_without_speech_is_dropped_in_the_end(self) -> None:
        # A long overload: ears reports every check, the speech is dropped each time.
        log = CaptureLog()
        for check in range(HEALTH_WAIT_MAX_CHECKS):
            log.add_health(1, 100 if check == 0 else 10, 100)  # the first report stands out
            log.render(str)
        log.add_health(1, 10, 100)
        log.add_utterance(utt(1, 1.0))
        # At the limit all are still kept, so the first report lifts it to 20%; 10% if lost.
        self.assertIn("audio 20%", log.log_line() or "")
        log.render(str)
        for _ in range(HEALTH_WAIT_MAX_CHECKS + 1):
            log.add_health(1, 10, 100)
            log.render(str)
        log.add_health(1, 100, 100)
        log.add_utterance(utt(1, 1.0))
        self.assertIn("audio 100%", log.log_line() or "")  # past it: the old gaps are gone


class NoiseIsNoAlarm(unittest.TestCase):
    """#671: the DM screen's ⚠️ counts only speech worth writing down, over enough of it."""

    def warning(self, pieces: Sequence[tuple[float, int, int]], now: float) -> str | None:
        log = CaptureLog()
        log.add_utterance(utt(1, 1.0))
        for at, received, expected in pieces:
            log.add_health(1, received, expected, at)
        return log.render(lambda uid: f"P{uid}", now)

    def test_dev1s_tv_numbers_say_nothing(self) -> None:
        # dev1's Test A, a TV in the room: two pieces of the DM, then two patchy TV bursts.
        # The bursts count (both are long enough); the 2 s floor keeps them quiet: 33 frames
        # lost is 0.66 s.
        log = CaptureLog()
        log.add_utterance(utt(1, 2.3))
        for at, received, expected in ((7, 52, 52), (21, 53, 53), (60, 155, 169), (61, 4, 23)):
            log.add_health(1, received, expected, at)
        self.assertEqual(len(log._window[1]), 4)  # all four count
        self.assertIn("(audio gaps)", log.log_line() or "")  # the log keeps the raw numbers
        self.assertIsNone(log.render(str, 62))

    def test_real_loss_in_a_minute_warns(self) -> None:
        pieces = [(at, 255, 300) for at in (0, 15, 30, 45, 59)]  # 85%, 225 lost: 4.5 s
        text = self.warning(pieces, 60)
        self.assertIn("**P1's voice is cutting out for DMbot** (85% got through)", text or "")

    def test_a_steady_92_percent_stays_quiet(self) -> None:
        # A phone at 92% for 30 s loses 2.4 s, but 90-94% rarely costs real words.
        pieces = [(at, 276, 300) for at in (0, 10, 20, 29, 30)]
        self.assertIsNone(self.warning(pieces, 30))

    def test_ten_tv_bursts_in_a_minute_warn_at_stage_1(self) -> None:
        # Documents stage 1: ten 4/23 bursts lose 3.8 s, so this warns. #699 (stage 2)
        # checks the lines first and stays quiet when they read fine.
        pieces = [(at, 4, 23) for at in range(0, 50, 5)]
        self.assertIsNotNone(self.warning(pieces, 50))

    def test_the_same_loss_spread_over_more_than_a_minute_doesnt(self) -> None:
        pieces = [(0, 270, 345), (70, 270, 345)]  # 1.5 s lost each, 70 s apart
        self.assertIsNone(self.warning(pieces, 71))

    def test_blips_never_count(self) -> None:
        pieces = [(at, 1, 12) for at in range(0, 50, 2)]  # 25 blips under 0.25 s: 5.5 s "lost"
        self.assertIsNone(self.warning(pieces, 50))

    def test_short_answers_count_and_add_up(self) -> None:
        # "yes", "ok": 0.5 s each, half got through. One never alarms; eight in a minute do.
        log = CaptureLog()
        texts = []
        for i in range(8):
            log.add_utterance(utt(1, 0.25))
            log.add_health(1, 12, 25, i * 7)
            texts.append(log.render(str, i * 7))
        self.assertEqual([t is not None for t in texts], [False] * 7 + [True])  # 8 x 0.26 s

    def test_a_little_loss_at_a_low_percent_doesnt(self) -> None:
        self.assertIsNone(self.warning([(0, 30, 100)], 1))  # 70% but only 1.4 s lost

    def test_a_short_total_loss_is_left_to_ears_own_warning(self) -> None:
        # #631: 0/37 counts here, but 0.74 s lost is under the 2 s floor; ears' "warning"
        # status (tests/test_sessions.py) warns at once instead.
        self.assertIsNone(self.warning([(0, 0, 37)], 1))

    def test_nearly_all_lost_warns(self) -> None:
        self.assertIsNotNone(self.warning([(0, 2, 500)], 1))  # 10 s lost, 2 frames heard

    def test_the_boundary_of_long_enough_to_write_down(self) -> None:
        # Eight pieces with nothing heard: 0.24 s each don't count; 0.26 s each do (2.08 s).
        self.assertIsNone(self.warning([(i, 0, 12) for i in range(8)], 8))
        self.assertIsNotNone(self.warning([(i, 0, 13) for i in range(8)], 8))

    def test_checks_every_15_s_warn_once_as_a_minute_fills(self) -> None:
        log = CaptureLog()

        def check(at: float, received: int, expected: int) -> str | None:
            log.add_utterance(utt(1, 1.0))
            log.add_health(1, received, expected, at)
            return log.render(str, at)

        self.assertIsNone(check(0, 270, 330))  # 1.2 s lost: not yet
        self.assertIsNotNone(check(15, 270, 330))  # 2.4 s in the minute: warn
        self.assertIsNone(check(30, 270, 330))  # same again: quiet
        self.assertIsNone(check(45, 300, 300))  # recovering: quiet

    def test_given_up_speech_leaves_the_window_too(self) -> None:
        log = CaptureLog()
        log.add_health(1, 100, 400, 0)  # 6 s lost, but its speech never comes
        for i in range(HEALTH_WAIT_MAX_CHECKS + 1):
            log.render(str, i)
        log.add_utterance(utt(1, 1.0))
        log.add_health(1, 300, 300, 10)
        self.assertIsNone(log.render(str, 10))  # the old gap doesn't land here
