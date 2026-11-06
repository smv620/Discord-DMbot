"""When "Questions for you" reminds a DM (#3435): the backoff, the reset, the silence while
recording and after 🔕 Not now. Pure; no database."""

from __future__ import annotations

import unittest
from itertools import pairwise

from dmbot.questions.timer import MAX_GAP_MIN, Notice, gap_minutes, next_step

T0 = 1_000_000
MIN = 60


def run(notice: Notice, now: int, total: int = 3, **kw: bool) -> tuple[Notice, str]:
    flags = {"answered": False, "grew": False, "recording": False} | kw
    return next_step(notice, total=total, now=now, **flags)


class TheGap(unittest.TestCase):
    def test_it_doubles_from_one_minute_and_stops_at_a_day(self) -> None:
        self.assertEqual([gap_minutes(n) for n in range(7)], [1, 2, 4, 8, 16, 32, 64])
        self.assertEqual(gap_minutes(10), 1024)
        self.assertEqual(gap_minutes(11), MAX_GAP_MIN)
        self.assertEqual(gap_minutes(10_000), MAX_GAP_MIN)


class TheBackoff(unittest.TestCase):
    def test_the_first_notice_then_reminders_one_two_four_eight_minutes_apart(self) -> None:
        notice, action = run(Notice(), T0, grew=True)
        self.assertEqual(action, "first")
        sent = [T0]
        for expected_gap in (1, 2, 4, 8, 16):
            self.assertEqual(run(notice, sent[-1] + expected_gap * MIN - 1)[1], "none")  # early
            at = sent[-1] + expected_gap * MIN
            notice, action = run(notice, at)
            self.assertEqual(action, "remind")
            sent.append(at)
        self.assertEqual([b - a for a, b in pairwise(sent)], [60, 120, 240, 480, 960])

    def test_the_gap_never_passes_a_day_however_long_nobody_answers(self) -> None:
        notice, now = Notice(step=30, due_at=T0), T0
        notice, action = run(notice, now)
        self.assertEqual(action, "remind")
        self.assertEqual(notice.due_at, now + MAX_GAP_MIN * MIN)

    def test_a_night_away_sends_a_handful_of_messages_not_thousands(self) -> None:
        notice, now, sent = Notice(), T0, 0
        for _ in range(8 * 60):  # eight hours, checked every minute
            notice, action = run(notice, now)
            sent += action in ("first", "remind")
            now += MIN
        self.assertLessEqual(sent, 11)

    def test_nothing_is_sent_while_nothing_waits_and_the_timer_clears(self) -> None:
        notice, _ = run(Notice(), T0)
        cleared, action = run(notice, T0 + 5, total=0)
        self.assertEqual((cleared, action), (Notice(), "clear"))
        self.assertEqual(run(cleared, T0 + 10, total=0)[1], "none")
        self.assertEqual(run(Notice(), T0, total=0)[1], "none")


class TheReset(unittest.TestCase):
    def test_an_answer_sets_the_gap_back_to_one_minute(self) -> None:
        notice = Notice(step=4, due_at=T0 + 3600, last_total=3)
        again, action = run(notice, T0, total=2, answered=True)
        self.assertEqual((again.step, again.due_at, action), (0, T0 + MIN, "none"))
        self.assertEqual(run(again, T0 + MIN)[1], "remind")
        self.assertEqual(run(again, T0 + MIN)[0].due_at, T0 + MIN + 2 * MIN)

    def test_the_next_message_after_an_answer_is_a_reminder_not_a_first_notice(self) -> None:
        notice, _ = run(Notice(), T0)
        notice, _ = run(notice, T0 + 10, total=2, answered=True)
        self.assertEqual(run(notice, T0 + 10 + MIN)[1], "remind")

    def test_a_question_arriving_after_nothing_waited_is_a_first_notice_again(self) -> None:
        notice, _ = run(Notice(), T0)
        notice, _ = run(notice, T0 + 100, total=0, answered=True)
        self.assertEqual(run(notice, T0 + 200, total=1, grew=True)[1], "first")


class TheSilences(unittest.TestCase):
    def test_nothing_is_sent_while_recording_and_the_first_notice_comes_after(self) -> None:
        notice, action = run(Notice(), T0, recording=True, grew=True)
        self.assertEqual(action, "none")
        self.assertEqual(notice.due_at, None)  # (the DM has not been told yet)
        self.assertEqual(run(notice, T0 + 3600)[1], "first")  # the round after the session

    def test_a_reminder_that_fell_due_while_recording_is_sent_after_it(self) -> None:
        notice, _ = run(Notice(), T0)
        notice, action = run(notice, T0 + 10 * MIN, recording=True)
        self.assertEqual(action, "none")
        self.assertEqual(run(notice, T0 + 11 * MIN)[1], "remind")

    def test_not_now_stops_reminders_until_a_new_question_arrives(self) -> None:
        notice, _ = run(Notice(), T0)
        muted = Notice(step=notice.step, due_at=notice.due_at, muted=True, last_total=3)
        for later in (MIN, 10 * MIN, 5 * 3600):
            self.assertEqual(run(muted, T0 + later)[1], "none")
        woken, action = run(muted, T0 + 6 * 3600, total=4, grew=True)
        self.assertFalse(woken.muted)
        self.assertEqual((woken.step, woken.due_at), (0, T0 + 6 * 3600 + MIN))
        self.assertEqual(action, "none")
        self.assertEqual(run(woken, T0 + 6 * 3600 + MIN)[1], "remind")

    def test_an_answer_does_not_end_not_now(self) -> None:
        muted = Notice(step=2, due_at=T0, muted=True, last_total=3)
        stays, action = run(muted, T0 + 10, total=2, answered=True)
        self.assertTrue(stays.muted)
        self.assertEqual(action, "none")


class TheWords(unittest.TestCase):
    def test_one_campaign_first_notice_and_reminder(self) -> None:
        from dmbot.questions.messages import Line, notice

        self.assertEqual(
            notice([Line("Frostmaiden", 1, True)]),
            "❓ DMbot has a question for you about **Frostmaiden**.",
        )
        self.assertEqual(
            notice([Line("Frostmaiden", 3, True)]),
            "❓ DMbot has 3 questions for you about **Frostmaiden**.",
        )
        self.assertEqual(
            notice([Line("Frostmaiden", 1, False)]),
            "❓ Still waiting: 1 question about **Frostmaiden**.",
        )
        self.assertEqual(
            notice([Line("Frostmaiden", 12, False)]),
            "❓ Still waiting: 12 questions about **Frostmaiden**.",
        )

    def test_several_campaigns_share_one_message_and_a_name_cannot_change_it(self) -> None:
        from dmbot.questions.messages import Line, notice

        text = notice([Line("One", 2, False), Line("**@everyone** _x_", 1, False)])
        self.assertEqual(text.splitlines()[0], "❓ Still waiting:")
        self.assertIn("• **One**: 2 questions", text)
        self.assertIn("\\*\\*@​everyone\\*\\* \\_x\\_", text)
        self.assertEqual(notice([]), "")

    def test_a_long_campaign_name_is_cut(self) -> None:
        from dmbot.questions.messages import NAME_MAX, Line, notice

        text = notice([Line("W" * 200, 1, True)])
        self.assertLessEqual(text.count("W"), NAME_MAX)
