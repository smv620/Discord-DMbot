"""The hours meter's pure rules (#437 part 2): months, minutes, start checks, warnings."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from dmbot import hours
from dmbot.entitlements import NO_ACCESS, Access, Entitlement


def ts(year: int, month: int, day: int, hour: int = 0) -> int:
    return int(datetime(year, month, day, hour, tzinfo=UTC).timestamp())


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


class Owed(unittest.TestCase):
    def test_the_whole_session_less_what_is_recorded(self) -> None:
        self.assertEqual(hours.minutes_owed(0, 125, 0), 3)
        self.assertEqual(hours.minutes_owed(0, 125, 2), 1)

    def test_a_repeat_adds_nothing(self) -> None:
        self.assertEqual(hours.minutes_owed(0, 125, 3), 0)
        self.assertEqual(hours.minutes_owed(0, 120, 3), 0)

    def test_never_negative(self) -> None:
        self.assertEqual(hours.minutes_owed(0, 10, 5), 0)


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

    def test_a_december_start_wraps_the_year_and_feb_29_is_clamped(self) -> None:
        dec = ts(2026, 12, 15, 9)
        self.assertEqual(hours.month_from_anchor(dec, ts(2027, 1, 20)).start, ts(2027, 1, 15, 9))
        leap = ts(2024, 1, 31, 8)  # 2028 is a leap year too
        self.assertEqual(
            hours.month_from_anchor(leap, ts(2024, 2, 29, 9)).start, ts(2024, 2, 29, 8)
        )

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


class Refusals(unittest.TestCase):
    END = ts(2026, 4, 14, 10)
    SITE = "https://dmbot.example"

    def test_a_start_that_may_go_ahead_has_no_refusal(self) -> None:
        self.assertIsNone(hours.refusal("ok", is_owner=True))

    def test_an_ended_plan_sends_the_owner_to_the_site(self) -> None:
        self.assertEqual(
            hours.refusal("no_plan", is_owner=True, site_url=self.SITE),
            "Your plan has ended. Pick one here: https://dmbot.example/account",
        )

    def test_without_a_site_address_it_says_where_in_words(self) -> None:
        self.assertEqual(
            hours.refusal("no_plan", is_owner=True),
            "Your plan has ended. Pick one on DMbot's website",
        )

    def test_used_up_hours_name_the_day_they_come_back(self) -> None:
        text = hours.refusal("out_of_hours", is_owner=True, site_url=self.SITE, month_end=self.END)
        self.assertEqual(
            text,
            "You've used all your hours this month. They come back on the 14th. To play now, "
            "add 10 hours or change your plan here: https://dmbot.example/account",
        )

    def test_the_day_is_the_renewal_day_not_the_day_before(self) -> None:
        # A month ending 14 April 00:00 UTC renews on the 14th.
        text = hours.refusal("out_of_hours", is_owner=True, month_end=ts(2026, 4, 14))
        self.assertIn("come back on the 14th", text or "")

    def test_anyone_else_is_never_told_why(self) -> None:
        for verdict in ("no_plan", "out_of_hours"):
            text = hours.refusal(
                verdict,  # type: ignore[arg-type]
                is_owner=False,
                site_url=self.SITE,
                month_end=self.END,
            )
            self.assertEqual(text, hours.START_BLOCKED)
            for word in ("plan", "hours", "14th", "dmbot.example"):
                self.assertNotIn(word, text or "")

    def test_ordinals(self) -> None:
        days = (1, 2, 3, 4, 11, 12, 13, 14, 21, 22, 23, 28, 30, 31)
        want = [
            "1st",
            "2nd",
            "3rd",
            "4th",
            "11th",
            "12th",
            "13th",
            "14th",
            "21st",
            "22nd",
            "23rd",
            "28th",
            "30th",
            "31st",
        ]
        self.assertEqual([hours.ordinal(d) for d in days], want)

    def test_the_first_warning_says_what_an_hour_is(self) -> None:
        self.assertEqual(
            hours.warning_text(4 * 60 + 10, 80),
            "⏳ About 4 hours left this month. (Hours are DMbot's listening time.)",
        )

    def test_the_second_says_where_to_add_more(self) -> None:
        self.assertEqual(
            hours.warning_text(60, 90, self.SITE),
            "⏳ About 1 hour left this month. To add more, go to https://dmbot.example/account",
        )
        self.assertIn("DMbot's website", hours.warning_text(60, 90))


class CapActions(unittest.TestCase):
    CAP = 600  # a 10-hour plan, in minutes

    def act(self, used: int, grace: int | None = None, session: int = 5) -> str:
        return hours.cap_action(paid(10), used, grace, session)

    def test_under_the_cap_nothing_happens(self) -> None:
        self.assertEqual(self.act(self.CAP - 1), "none")

    def test_reaching_the_cap_gives_the_first_session_the_grace(self) -> None:
        self.assertEqual(self.act(self.CAP), "start_grace")
        self.assertEqual(self.act(self.CAP + 119), "start_grace")

    def test_the_session_with_the_grace_carries_on_until_it_is_spent(self) -> None:
        self.assertEqual(self.act(self.CAP + 30, grace=5), "in_grace")
        self.assertEqual(self.act(self.CAP + 119, grace=5), "in_grace")
        self.assertEqual(self.act(self.CAP + 120, grace=5), "stop")

    def test_another_session_gets_no_grace_the_same_month(self) -> None:
        self.assertEqual(self.act(self.CAP, grace=99, session=5), "stop")

    def test_a_jump_past_the_whole_grace_stops_at_once(self) -> None:
        self.assertEqual(self.act(self.CAP + 500), "stop")

    def test_no_cap_never_acts(self) -> None:
        free = Access("free", "Free access", None, None, True)
        self.assertEqual(hours.cap_action(free, 10**6, None, 5), "none")

    def test_the_texts_are_plain_and_send_people_to_the_site(self) -> None:
        self.assertIn("up to 2 more hours", hours.grace_started_text("https://x.example"))
        self.assertIn("https://x.example/account", hours.stopped_text("https://x.example"))
        self.assertIn("DMbot's website", hours.stopped_text())


class Standing(unittest.TestCase):
    def test_percent_and_left(self) -> None:
        s = hours.standing(paid(10), 300)
        self.assertEqual((s.cap_minutes, s.left_minutes, s.percent_used), (600, 300, 50))

    def test_over_the_cap_is_capped_at_100(self) -> None:
        self.assertEqual(hours.standing(paid(10), 9999).percent_used, 100)


if __name__ == "__main__":
    unittest.main()
