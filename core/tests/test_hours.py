"""The hours meter's pure rules (#437 part 2): months, minutes, start checks, warnings."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from dmbot import hours
from dmbot.entitlements import NO_ACCESS, Access, Entitlement


def ts(*args: int) -> int:
    return int(datetime(*args, tzinfo=UTC).timestamp())


def paid(hours_cap: int | None = 18) -> Access:
    return Access("paid", "Table", hours_cap, 1, True)


def plan(start: int, end: int) -> Entitlement:
    return Entitlement(1, "table", "active", 18, 0, 1, start, end, None, None, start)


class Minutes(unittest.TestCase):
    def test_rounds_up_to_the_minute(self) -> None:
        self.assertEqual(hours.minutes_used(0, 1), 1)
        self.assertEqual(hours.minutes_used(0, 60), 1)
        self.assertEqual(hours.minutes_used(0, 61), 2)
        self.assertEqual(hours.minutes_used(0, 0), 0)

    def test_a_clock_that_went_backwards_is_nothing(self) -> None:
        self.assertEqual(hours.minutes_used(100, 40), 0)


class Months(unittest.TestCase):
    def test_a_grant_month_starts_on_its_day(self) -> None:
        anchor = ts(2026, 1, 14, 10)
        month = hours.month_from_anchor(anchor, ts(2026, 3, 20))
        self.assertEqual((month.start, month.end), (ts(2026, 3, 14, 10), ts(2026, 4, 14, 10)))

    def test_before_the_day_this_month_it_is_still_last_months_window(self) -> None:
        anchor = ts(2026, 1, 14, 10)
        month = hours.month_from_anchor(anchor, ts(2026, 3, 10))
        self.assertEqual((month.start, month.end), (ts(2026, 2, 14, 10), ts(2026, 3, 14, 10)))

    def test_a_31st_start_falls_on_the_last_day_of_a_short_month(self) -> None:
        anchor = ts(2026, 1, 31, 8)
        month = hours.month_from_anchor(anchor, ts(2026, 2, 28, 9))
        self.assertEqual((month.start, month.end), (ts(2026, 2, 28, 8), ts(2026, 3, 31, 8)))

    def test_months_never_overlap_or_leave_a_gap(self) -> None:
        anchor = ts(2026, 1, 31, 8)
        now = anchor
        for _ in range(40):
            month = hours.month_from_anchor(anchor, now)
            self.assertLessEqual(month.start, now)
            self.assertLess(now, month.end)
            self.assertEqual(hours.month_from_anchor(anchor, month.end).start, month.end)
            now = month.end

    def test_now_before_the_anchor_is_the_first_month(self) -> None:
        anchor = ts(2026, 5, 3)
        self.assertEqual(hours.month_from_anchor(anchor, ts(2026, 4, 1)).start, anchor)

    def test_a_paid_plans_month_is_its_billing_period(self) -> None:
        now = ts(2026, 3, 20)
        p = plan(ts(2026, 3, 1), ts(2026, 4, 1))
        month = hours.month_for(paid(), p, None, now)
        self.assertEqual(month, hours.Month(p.period_start, p.period_end))

    def test_a_grant_overlapping_a_paid_plan_uses_the_paid_plans_month(self) -> None:
        now = ts(2026, 3, 20)
        p = plan(ts(2026, 3, 1), ts(2026, 4, 1))
        grant = Access("grant", "Free access", 87, 5, True)
        month = hours.month_for(grant, p, ts(2026, 1, 14), now)
        self.assertEqual(month, hours.Month(p.period_start, p.period_end))

    def test_a_grant_alone_runs_from_the_day_it_started(self) -> None:
        now = ts(2026, 3, 20)
        grant = Access("grant", "Free access", 87, 5, True)
        month = hours.month_for(grant, None, ts(2026, 1, 14), now)
        self.assertEqual(month, hours.month_from_anchor(ts(2026, 1, 14), now))

    def test_no_meter_for_free_list_unlimited_or_no_plan(self) -> None:
        now = ts(2026, 3, 20)
        free = Access("free", "Free access", None, None, True)
        unlimited = Access("grant", "Free access", None, None, True)
        self.assertIsNone(hours.month_for(free, None, None, now))
        self.assertIsNone(hours.month_for(unlimited, None, ts(2026, 1, 1), now))
        self.assertIsNone(hours.month_for(NO_ACCESS, None, None, now))


class Starts(unittest.TestCase):
    def test_hours_left_means_ok(self) -> None:
        self.assertEqual(hours.start_verdict(paid(18), 18 * 60 - 1), "ok")

    def test_no_hours_left_is_refused(self) -> None:
        self.assertEqual(hours.start_verdict(paid(18), 18 * 60), "out_of_hours")
        self.assertEqual(hours.start_verdict(paid(18), 99999), "out_of_hours")

    def test_no_plan_is_refused(self) -> None:
        self.assertEqual(hours.start_verdict(NO_ACCESS, 0), "no_plan")

    def test_no_cap_never_runs_out(self) -> None:
        free = Access("free", "Free access", None, None, True)
        self.assertEqual(hours.start_verdict(free, 10**9), "ok")


class Warnings(unittest.TestCase):
    cap = 10 * 60  # a 10-hour plan, in minutes

    def test_crossing_80_percent(self) -> None:
        self.assertEqual(hours.warning_crossed(paid(10), 470, 481), 80)

    def test_exactly_reaching_a_mark_counts(self) -> None:
        self.assertEqual(hours.warning_crossed(paid(10), 470, 480), 80)

    def test_already_past_a_mark_stays_quiet(self) -> None:
        self.assertIsNone(hours.warning_crossed(paid(10), 481, 500))

    def test_one_long_stretch_reports_only_the_highest_mark(self) -> None:
        self.assertEqual(hours.warning_crossed(paid(10), 400, 560), 90)

    def test_nothing_crossed_is_none(self) -> None:
        self.assertIsNone(hours.warning_crossed(paid(10), 10, 20))

    def test_no_cap_no_warning(self) -> None:
        free = Access("free", "Free access", None, None, True)
        self.assertIsNone(hours.warning_crossed(free, 0, 10**6))


class Words(unittest.TestCase):
    def test_plain_amounts(self) -> None:
        self.assertEqual(hours.hours_left_words(4 * 60 + 10), "about 4 hours")
        self.assertEqual(hours.hours_left_words(90), "about 1½ hours")
        self.assertEqual(hours.hours_left_words(60), "about 1 hour")
        self.assertEqual(hours.hours_left_words(35), "about half an hour")
        self.assertEqual(hours.hours_left_words(20), "less than half an hour")
        self.assertEqual(hours.hours_left_words(0), "less than half an hour")


class Standing(unittest.TestCase):
    def test_percent_and_left(self) -> None:
        s = hours.standing(paid(10), 300)
        self.assertEqual((s.cap_minutes, s.left_minutes, s.percent_used), (600, 300, 50))

    def test_over_the_cap_is_capped_at_100(self) -> None:
        self.assertEqual(hours.standing(paid(10), 9999).percent_used, 100)


if __name__ == "__main__":
    unittest.main()
