"""The campaign's calendar (#3091 part 3): game days in words, Harptos across month and year
boundaries and festival days. Pure."""

from __future__ import annotations

import unittest

from dmbot.timebot import calendar as cal
from dmbot.timebot.clock import MINUTES_PER_DAY


class TheYear(unittest.TestCase):
    def test_it_has_twelve_months_five_festival_days_and_365_days(self) -> None:
        self.assertEqual((len(cal.MONTHS), len(cal.FESTIVALS), cal.YEAR_DAYS), (12, 5, 365))
        self.assertEqual(sum(cal.year_days(y) for y in range(1489, 1493)), 365 * 4 + 1)

    def test_day_one_starts_on_a_month_day_or_a_festival_and_refuses_what_is_not_there(
        self,
    ) -> None:
        self.assertEqual(cal.day_one(1492, "Hammer").ordinal, 0)
        self.assertEqual(cal.day_one(1492, "flamerule", 14).ordinal, 30 + 1 + 90 + 1 + 60 + 13)
        self.assertEqual(cal.day_one(1492, "Midsummer").year, 1492)
        for args in (("Nope", 1), ("Hammer", 31), ("Hammer", 0)):
            with self.subTest(args), self.assertRaises(ValueError):
                cal.day_one(1492, *args)
        with self.assertRaises(ValueError):
            cal.DayOne(0, 0)


class Inputs(unittest.TestCase):
    def test_a_day_one_takes_exactly_ints(self) -> None:
        for year, ordinal in ((True, 3), (1492, True), (1492.0, 3), (1492, 3.0), ("1492", 3)):
            with self.subTest((year, ordinal)), self.assertRaises(ValueError):
                cal.DayOne(year, ordinal)  # type: ignore[arg-type]

    def test_a_float_or_bool_day_or_year_is_refused_not_rounded(self) -> None:
        for year, day in ((1492, 14.5), (1492, True), (1492.0, 14), (True, 14)):
            with self.subTest((year, day)), self.assertRaises(ValueError):
                cal.day_one(year, "Flamerule", day)  # type: ignore[arg-type]

    def test_the_day_defaults_to_none_a_month_starts_on_its_first_and_a_festival_takes_none(
        self,
    ) -> None:
        self.assertEqual(cal.day_one(1492, "Flamerule"), cal.day_one(1492, "Flamerule", 1))
        cal.day_one(1492, "Midsummer")
        for day in (1, 14, 0):
            with self.subTest(day), self.assertRaises(ValueError):
                cal.day_one(1492, "Midsummer", day)  # (it was silently ignored before)

    def test_a_negative_minute_is_no_time_in_either_mode(self) -> None:
        start = cal.day_one(1492, "Flamerule", 14)
        for kind, given in ((cal.DAYS, None), (cal.HARPTOS, start)):
            for minute in (-1, -MINUTES_PER_DAY, True, 1.5):
                with self.subTest((kind, minute)), self.assertRaises(ValueError):
                    cal.date_words(minute, kind, given)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            cal.Calendar(cal.HARPTOS, start).words(-1)


class HarptosDates(unittest.TestCase):
    def test_day_one_reads_as_its_own_date(self) -> None:
        start = cal.day_one(1492, "Flamerule", 14)
        self.assertEqual(cal.harptos(1, start), "Flamerule 14th, 1492 DR")

    def test_a_month_boundary_goes_through_the_festival_day_after_it(self) -> None:
        start = cal.day_one(1493, "Flamerule", 29)  # (1493 is not a leap year)
        self.assertEqual(
            [cal.harptos(n, start) for n in (1, 2, 3, 4, 5)],
            [
                "Flamerule 29th, 1493 DR",
                "Flamerule 30th, 1493 DR",
                "Midsummer, 1493 DR",
                "Eleasis 1st, 1493 DR",
                "Eleasis 2nd, 1493 DR",
            ],
        )

    def test_shieldmeet_follows_midsummer_in_a_year_divisible_by_four_and_only_then(self) -> None:
        leap = cal.day_one(1492, "Flamerule", 30)
        self.assertEqual(
            [cal.harptos(n, leap) for n in (1, 2, 3, 4)],
            [
                "Flamerule 30th, 1492 DR",
                "Midsummer, 1492 DR",
                "Shieldmeet, 1492 DR",
                "Eleasis 1st, 1492 DR",
            ],
        )
        self.assertEqual(cal.year_days(1492), 366)
        self.assertEqual(cal.year_days(1493), 365)
        self.assertEqual(cal.day_one(1492, "Shieldmeet").ordinal, 30 + 1 + 90 + 1 + 90 + 1 + 1 - 1)
        with self.assertRaises(ValueError):
            cal.day_one(1493, "Shieldmeet")
        with self.assertRaises(ValueError):
            cal.DayOne(1493, 365)  # (a common year has no 366th day)
        cal.DayOne(1492, 365)

    def test_a_leap_year_is_366_days_long_when_the_year_turns(self) -> None:
        start = cal.day_one(1492, "Nightal", 30)
        self.assertEqual(cal.harptos(2, start), "Hammer 1st, 1493 DR")
        end_of_1491 = cal.day_one(1491, "Nightal", 30)
        self.assertEqual(cal.harptos(2 + 365, end_of_1491), "Nightal 30th, 1492 DR")  # 1492 is long
        self.assertEqual(cal.harptos(2 + 366, end_of_1491), "Hammer 1st, 1493 DR")

    def test_a_month_with_no_festival_after_it_goes_straight_on(self) -> None:
        start = cal.day_one(1492, "Alturiak", 30)
        self.assertEqual(cal.harptos(2, start), "Ches 1st, 1492 DR")

    def test_the_year_turns_after_nightal_30(self) -> None:
        start = cal.day_one(1492, "Nightal", 30)
        self.assertEqual(cal.harptos(1, start), "Nightal 30th, 1492 DR")
        self.assertEqual(cal.harptos(2, start), "Hammer 1st, 1493 DR")
        self.assertEqual(cal.harptos(367, start), "Hammer 1st, 1494 DR")  # (a 365-day year)

    def test_the_suffixes_are_right_for_the_teens_and_the_twenties(self) -> None:
        start = cal.day_one(1492, "Hammer", 1)
        got = {n: cal.harptos(n, start).split(",")[0] for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23)}
        self.assertEqual(
            got,
            {1: "Hammer 1st", 2: "Hammer 2nd", 3: "Hammer 3rd", 4: "Hammer 4th",
             11: "Hammer 11th", 12: "Hammer 12th", 13: "Hammer 13th",
             21: "Hammer 21st", 22: "Hammer 22nd", 23: "Hammer 23rd"},
        )  # fmt: skip

    def test_a_day_before_day_one_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            cal.harptos(0, cal.day_one(1492, "Hammer"))


class DateWords(unittest.TestCase):
    def test_without_a_setting_it_is_plain_day_n(self) -> None:
        self.assertEqual(cal.date_words(11 * MINUTES_PER_DAY + 5), "Day 12")
        start = cal.day_one(1492, "Flamerule", 14)
        self.assertEqual(cal.date_words(0, cal.HARPTOS), "Day 1")  # (no Day 1 date: still Day N)
        self.assertEqual(cal.date_words(0, cal.DAYS, start), "Day 1")

    def test_with_harptos_a_game_minute_reads_as_its_date_whatever_the_hour(self) -> None:
        start = cal.day_one(1492, "Flamerule", 14)
        self.assertEqual(cal.date_words(0, cal.HARPTOS, start), "Flamerule 14th, 1492 DR")
        self.assertEqual(
            cal.date_words(cal.minute_of(2, 23, 59), cal.HARPTOS, start), "Flamerule 15th, 1492 DR"
        )
