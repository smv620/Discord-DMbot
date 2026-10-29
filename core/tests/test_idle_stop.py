"""The automatic stop's clock (#2034): bots never count, silence reaches a warning and then a stop,
speech, a person coming back or Keep listening start it over, Off turns off the silence stop only,
and a fresh watch (a restart) starts from the resume."""

from __future__ import annotations

import unittest

from dmbot.idle_stop import EMPTY_S, QUIET, WARN_S, IdleWatch, Verdict

MIN = 60


class TheEmptyChannel(unittest.TestCase):
    def test_nobody_for_five_minutes_warns_and_five_more_stops(self) -> None:
        watch = IdleWatch(0)
        self.assertEqual(watch.poll(60, people=False, quiet_minutes=30).action, "none")
        self.assertEqual(
            watch.poll(EMPTY_S, people=False, quiet_minutes=30), Verdict("warn", "empty")
        )
        self.assertEqual(watch.poll(EMPTY_S + 60, people=False, quiet_minutes=30).action, "none")
        stop = watch.poll(EMPTY_S + WARN_S, people=False, quiet_minutes=30)
        self.assertEqual(stop, Verdict("stop", "empty"))

    def test_a_channel_with_only_bots_is_a_channel_with_nobody(self) -> None:
        # the caller counts people with `not member.bot`; a bot-only channel is people=False
        watch = IdleWatch(0)
        self.assertEqual(watch.poll(EMPTY_S, people=False, quiet_minutes=30).action, "warn")

    def test_a_person_coming_back_cancels_the_warning_and_the_wait_starts_over(self) -> None:
        watch = IdleWatch(0)
        watch.poll(EMPTY_S, people=False, quiet_minutes=30)
        self.assertEqual(watch.poll(EMPTY_S + 30, people=True, quiet_minutes=30).action, "none")
        # gone again: five full minutes before the next warning
        self.assertEqual(watch.poll(EMPTY_S + 60, people=False, quiet_minutes=30).action, "none")
        later = EMPTY_S + 30 + EMPTY_S
        self.assertEqual(watch.poll(later, people=False, quiet_minutes=30).action, "warn")

    def test_the_empty_stop_ignores_the_setting(self) -> None:
        watch = IdleWatch(0)
        self.assertEqual(watch.poll(EMPTY_S, people=False, quiet_minutes=0).action, "warn")


class TheQuietTable(unittest.TestCase):
    def test_thirty_quiet_minutes_warn_and_five_more_stop(self) -> None:
        watch = IdleWatch(0)
        self.assertEqual(watch.poll(29 * MIN, people=True, quiet_minutes=30).action, "none")
        self.assertEqual(
            watch.poll(30 * MIN, people=True, quiet_minutes=30), Verdict("warn", QUIET)
        )
        self.assertEqual(watch.poll(34 * MIN, people=True, quiet_minutes=30).action, "none")
        self.assertEqual(
            watch.poll(35 * MIN, people=True, quiet_minutes=30), Verdict("stop", QUIET)
        )

    def test_speech_starts_the_wait_again_and_cancels_a_warning(self) -> None:
        watch = IdleWatch(0)
        watch.poll(30 * MIN, people=True, quiet_minutes=30)  # warned
        watch.activity(31 * MIN)
        self.assertEqual(watch.poll(31 * MIN, people=True, quiet_minutes=30).action, "none")
        self.assertEqual(watch.poll(60 * MIN, people=True, quiet_minutes=30).action, "none")
        self.assertEqual(watch.poll(61 * MIN, people=True, quiet_minutes=30).action, "warn")

    def test_new_speech_in_the_totals_counts_and_the_same_totals_do_not(self) -> None:
        watch = IdleWatch(0)
        watch.saw_speech(10 * MIN, 5.0)
        watch.saw_speech(20 * MIN, 5.0)  # nothing new
        self.assertEqual(watch.poll(40 * MIN, people=True, quiet_minutes=30).action, "warn")
        watch.saw_speech(41 * MIN, 9.0)
        self.assertEqual(watch.poll(41 * MIN, people=True, quiet_minutes=30).action, "none")

    def test_keep_listening_resets_both_clocks(self) -> None:
        watch = IdleWatch(0)
        watch.poll(30 * MIN, people=True, quiet_minutes=30)
        watch.keep(33 * MIN)
        self.assertEqual(watch.poll(36 * MIN, people=True, quiet_minutes=30).action, "none")
        self.assertEqual(watch.poll(63 * MIN, people=True, quiet_minutes=30).action, "warn")
        empty = IdleWatch(0)
        empty.poll(EMPTY_S, people=False, quiet_minutes=30)
        empty.keep(EMPTY_S + 60)
        self.assertEqual(empty.poll(EMPTY_S + 120, people=False, quiet_minutes=30).action, "none")

    def test_off_turns_off_only_the_silence_stop(self) -> None:
        watch = IdleWatch(0)
        self.assertEqual(watch.poll(10 * 60 * MIN, people=True, quiet_minutes=0).action, "none")

    def test_a_longer_limit_waits_longer(self) -> None:
        watch = IdleWatch(0)
        self.assertEqual(watch.poll(60 * MIN, people=True, quiet_minutes=90).action, "none")
        self.assertEqual(watch.poll(90 * MIN, people=True, quiet_minutes=90).action, "warn")

    def test_a_fresh_watch_after_a_restart_starts_from_the_resume(self) -> None:
        old = IdleWatch(0)
        self.assertEqual(old.poll(3 * 60 * MIN, people=True, quiet_minutes=30).action, "warn")
        resumed = IdleWatch(3 * 60 * MIN)  # a restart is not silence
        self.assertEqual(
            resumed.poll(3 * 60 * MIN + 60, people=True, quiet_minutes=30).action, "none"
        )

    def test_a_warning_that_could_not_be_shown_does_not_count(self) -> None:
        watch = IdleWatch(0)
        self.assertEqual(watch.poll(30 * MIN, people=True, quiet_minutes=30).action, "warn")
        watch.unwarn()
        self.assertEqual(watch.poll(31 * MIN, people=True, quiet_minutes=30).action, "warn")

    def test_the_stop_names_what_the_warning_named(self) -> None:
        watch = IdleWatch(0)
        watch.poll(30 * MIN, people=True, quiet_minutes=30)  # warned for silence
        stop = watch.poll(30 * MIN + WARN_S, people=False, quiet_minutes=30)
        self.assertEqual(stop, Verdict("stop", QUIET))

    def test_an_empty_channel_wins_over_a_quiet_one(self) -> None:
        watch = IdleWatch(0)
        self.assertEqual(watch.poll(40 * MIN, people=False, quiet_minutes=30).reason, "empty")


if __name__ == "__main__":
    unittest.main()
