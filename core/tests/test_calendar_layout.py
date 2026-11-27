"""A calendar's layout (#5364 slice 1): its checks, printing, counting and the one date reader, and
Harptos as a layout that must print and read exactly as today's Harptos code does (the golden
tests). Pure: no database, no Discord. Invented calendars only."""

from __future__ import annotations

import unittest

from dmbot.memory import time_limits as tl
from dmbot.timebot import calendar as cal
from dmbot.timebot import layout as lay
from dmbot.timebot.clock import MINUTES_PER_DAY as DAY
from dmbot.timebot.clock import day_of

H = lay.HARPTOS
STARTS = (
    ("Hammer", 1, 1492), ("Flamerule", 14, 1492), ("Midsummer", None, 1492),
    ("Shieldmeet", None, 1492), ("Nightal", 30, 1491), ("Feast of the Moon", None, 1493),
)  # fmt: skip


def both(year: int, name: str, day: int | None) -> tuple[cal.DayOne, lay.Start]:
    """The same Day 1 in today's Harptos code and in the layout."""
    old = cal.day_one(year, name, day)
    return old, H.start_on(year, name, day)


class HarptosIsTheSameAsToday(unittest.TestCase):
    """Nothing users see changes when Harptos moves onto the layout."""

    def test_the_year_is_the_same(self) -> None:
        self.assertEqual([p.name for p in H.parts if p.kind == lay.MONTH], list(cal.MONTHS))
        self.assertEqual([p.name for p in H.parts if p.kind == lay.FESTIVAL], list(cal.FESTIVALS))
        for year in range(1488, 1500):
            self.assertEqual(H.year_days(year), cal.year_days(year), year)

    def test_every_day_prints_the_same(self) -> None:
        for name, day, year in STARTS:
            old, new = both(year, name, day)
            self.assertEqual((new.year, new.ordinal), (old.year, old.ordinal))
            for number in range(1, 1600):
                self.assertEqual(
                    H.words(number, new), cal.harptos(number, old), (name, day, year, number)
                )

    def test_every_date_counts_the_same_way_back(self) -> None:
        for name, day, year in STARTS:
            old, new = both(year, name, day)
            for number in range(1, 1200, 7):
                d = H.date_of(number, new)
                festival = d.kind == lay.FESTIVAL
                got = H.day_number_of(d.year, d.part, None if festival else d.day, new)
                self.assertEqual(got, number)
                self.assertEqual(
                    got, cal.day_number_of(d.year, d.part, None if festival else d.day, old)
                )

    def test_dates_the_calendar_has_not_got_are_refused_as_before(self) -> None:
        for args in ((1493, "Shieldmeet", None), (1492, "Hammer", 31), (1492, "Midsummer", 1)):
            with self.subTest(args), self.assertRaises(ValueError):
                H.ordinal_of(*args)
            with self.assertRaises(ValueError):
                cal.day_one(*args)

    def test_the_reader_reads_every_phrase_as_the_time_limit_reader_does(self) -> None:
        old, start = both(1492, "Flamerule", 1)
        phrases = (
            "by the 14th of Flamerule", "before Flamerule 14", "Flamerule 14th, 1492 DR",
            "before Midsummer", "by the Feast of the Moon", "by Hammer 3", "by Flamerule 14",
            "by Flamerule 20", "by Flamerule 21", "before Shieldmeet", "Shieldmeet, 1493 DR",
            "by Midsummer, 1500 gold", "Flamerule 14, 3 guards", "Flamerule 14, 1493.",
            "Flamerule 14th, 1510 DR", "before the new moon", "in three days",
            "Flamerule 14 or Flamerule 20", "Flamerule 14 or Midsummer",
            "by Midsummer or the 14th of Flamerule", "the 1st of Midsummer",
            "Flamerule 14th, 1491 DR", "by Flamerule 31", "the 2nd day of Eleint",
            "midsummer", "FLAMERULE 14TH", "Highharvestide, 1494 DR", "the 30th of Nightal",
        )  # fmt: skip
        nows = [0, 10 * 60, 100 * DAY, 120 * DAY + 600, 300 * DAY, 400 * DAY, 800 * DAY]
        for words in phrases:
            for now in nows:
                was = tl._harptos_day(words, old, now)
                self.assertEqual(
                    H.parse(words, start, today=day_of(now), direction=lay.NEXT), was, (words, now)
                )


class ReadingDates(unittest.TestCase):
    """The owner's example: counted on the campaign's own calendar."""

    summer = lay.four_seasons(90)
    start = summer.start_on(1201, "Spring", 1)

    def test_it_prints_the_dates_of_a_plain_four_seasons_year(self) -> None:
        self.assertEqual(self.summer.words(1, self.start), "Spring 1st, 1201")
        self.assertEqual(self.summer.words(91 + 1, self.start), "Summer 2nd, 1201")
        self.assertEqual(self.summer.words(361, self.start), "Spring 1st, 1202")

    def test_the_second_day_of_summer_is_counted_by_code(self) -> None:
        for said in ("the 2nd day of Summer", "the 2nd of Summer", "Summer 2nd", "Summer 2"):
            self.assertEqual(
                self.summer.parse(said, self.start, today=120, direction=lay.LAST), 92, said
            )

    def test_a_date_with_no_year_takes_its_direction(self) -> None:
        # learned on game day 100 (the 10th day of summer): "the 2nd of summer" was 92, last
        self.assertEqual(
            self.summer.parse("Summer 2nd", self.start, today=100, direction=lay.LAST), 92
        )
        # a limit set on day 100 means the next one, a year on
        self.assertEqual(
            self.summer.parse("Summer 2nd", self.start, today=100, direction=lay.NEXT), 452
        )
        # today is the day itself: it is the last coming, never the next
        self.assertEqual(
            self.summer.parse("Summer 2nd", self.start, today=92, direction=lay.LAST), 92
        )
        self.assertEqual(
            self.summer.parse("Summer 2nd", self.start, today=92, direction=lay.NEXT), 452
        )

    def test_a_last_coming_never_reaches_before_day_one(self) -> None:
        self.assertIsNone(self.summer.parse("Winter 5th", self.start, today=40, direction=lay.LAST))

    def test_a_year_in_the_words_is_counted_whatever_the_direction(self) -> None:
        for direction in (lay.NEXT, lay.LAST):
            self.assertEqual(
                self.summer.parse("Summer 2nd, 1202", self.start, today=1, direction=direction), 452
            )

    def test_day_n_is_read_in_every_calendar(self) -> None:
        self.assertEqual(self.summer.parse("by Day 12", self.start, today=1), 12)
        self.assertEqual(H.parse("by Day 12", H.start_on(1492, "Hammer", 1), today=1), 12)
        for words in ("Day 0", "Day 3 or Day 4", "Day 3 or Summer 2nd", "day 100000"):
            self.assertIsNone(self.summer.parse(words, self.start, today=1), words)

    def test_a_bare_month_or_season_name_is_no_date(self) -> None:
        for words in ("Summer is coming", "a man named Autumn", "in Winter"):
            self.assertIsNone(self.summer.parse(words, self.start, today=1), words)

    def test_two_dates_a_name_it_has_not_got_and_dates_before_day_one_are_none(self) -> None:
        for words in ("Summer 2nd or Autumn 3rd", "Flamerule 14", "Summer 91st", "Spring 0"):
            self.assertIsNone(self.summer.parse(words, self.start, today=1), words)
        later = self.summer.start_on(1201, "Summer", 10)
        self.assertIsNone(self.summer.parse("Summer 2nd, 1201", later, today=1))

    def test_a_year_label_is_what_makes_a_number_a_year(self) -> None:
        ar = lay.Layout("Vale", lay.four_seasons(30).parts, year_label="AR")
        start = ar.start_on(1201, "Spring", 1)
        self.assertEqual(ar.words(31, start), "Summer 1st, 1201 AR")
        self.assertEqual(ar.parse("Summer 1st, 1202 AR", start, today=1), 151)
        self.assertEqual(ar.parse("Summer 1st, 3 guards", start, today=1), 31)  # (no year: "3")


class FestivalsAndWeekdays(unittest.TestCase):
    vale = lay.Layout(
        "Vale",
        (
            lay.Part("Thaw", 30), lay.Part("Moonfeast", 3, lay.FESTIVAL), lay.Part("Reaping", 30),
            lay.Part("Longnight", 1, lay.FESTIVAL),
        ),
        weekdays=("Sunday", "Moonday", "Fireday"),
        year_label="AR",
    )  # fmt: skip

    def test_a_several_day_festival_counts_its_days_and_stands_alone_as_its_first(self) -> None:
        start = self.vale.start_on(1201, "Thaw", 1)
        self.assertEqual(self.vale.words(31, start), "Moonfeast 1st, 1201 AR")
        self.assertEqual(self.vale.words(33, start), "Moonfeast 3rd, 1201 AR")
        self.assertEqual(self.vale.words(64, start), "Longnight, 1201 AR")
        self.assertEqual(self.vale.parse("before Moonfeast", start, today=1), 31)
        self.assertEqual(self.vale.parse("the 3rd day of Moonfeast", start, today=1), 33)
        self.assertEqual(self.vale.parse("Moonfeast 2", start, today=1), 32)
        self.assertEqual(self.vale.parse("before Longnight", start, today=1), 64)
        self.assertIsNone(self.vale.parse("Moonfeast 4th", start, today=1))
        self.assertIsNone(self.vale.parse("the 1st of Longnight", start, today=1))

    def test_weeks_run_on_through_festivals_and_show_only_when_asked(self) -> None:
        start = self.vale.start_on(1201, "Thaw", 1, weekday="Moonday")
        self.assertEqual([self.vale.weekday_of(n, start) for n in (1, 2, 3, 4)],
                         ["Moonday", "Fireday", "Sunday", "Moonday"])  # fmt: skip
        self.assertEqual(self.vale.weekday_of(31, start), "Moonday" if 30 % 3 == 0 else None)
        self.assertEqual(self.vale.words(2, start), "Thaw 2nd, 1201 AR")
        self.assertEqual(self.vale.words(2, start, weekday=True), "Fireday, Thaw 2nd, 1201 AR")

    def test_a_calendar_with_no_weekdays_has_none(self) -> None:
        plain = lay.four_seasons(30)
        start = plain.start_on(1, "Spring", 1)
        self.assertIsNone(plain.weekday_of(5, start))
        self.assertEqual(plain.words(5, start, weekday=True), plain.words(5, start))

    def test_the_leap_day_follows_its_part_and_comes_every_n_years(self) -> None:
        leap = lay.Layout(
            "Leapy", (lay.Part("Dry", 10, lay.SEASON), lay.Part("Wet", 10, lay.SEASON)),
            lay.Leap(3, "Extra", "Dry"),
        )  # fmt: skip
        self.assertEqual([leap.year_days(y) for y in (1, 2, 3, 6)], [20, 20, 21, 21])
        start = leap.start_on(2, "Dry", 10)
        self.assertEqual(leap.words(1, start), "Dry 10th, 2")
        self.assertEqual(leap.words(2, start), "Wet 1st, 2")
        self.assertEqual(leap.words(12, start), "Dry 1st, 3")  # (year 3 begins on day 12)
        self.assertEqual(leap.words(22, start), "Extra, 3")  # (right after Dry 10th)
        self.assertEqual(leap.words(23, start), "Wet 1st, 3")
        self.assertEqual(leap.parse("before Extra", start, today=1), 22)
        # the next one after day 22 is in year 6 (every 3rd year): years 4 and 5 have 20 days
        self.assertEqual(leap.parse("before Extra", start, today=22, direction=lay.NEXT), 83)
        self.assertEqual(leap.parse("before Extra", start, today=83, direction=lay.LAST), 83)
        self.assertEqual(leap.parse("before Extra", start, today=82, direction=lay.LAST), 22)
        with self.assertRaises(ValueError):
            leap.ordinal_of(4, "Extra")  # (no Extra in year 4)


class TheChecks(unittest.TestCase):
    """A mistake stays small: a bad definition is refused when it is made."""

    def made(self, **kw: object) -> lay.Layout:
        base: dict[str, object] = {"name": "Vale", "parts": (lay.Part("Thaw", 30),)}
        return lay.Layout(**{**base, **kw})  # type: ignore[arg-type]

    def refused(self, code: str, **kw: object) -> None:
        with self.assertRaises(ValueError) as caught:
            self.made(**kw)
        self.assertEqual(str(caught.exception), code, kw)

    def test_a_plain_definition_is_taken(self) -> None:
        self.assertEqual(self.made().year_days(1), 30)

    def test_parts_and_days(self) -> None:
        self.refused("parts", parts=())
        self.refused("parts", parts=tuple(lay.Part(f"M{n}x", 1) for n in range(41)))
        self.refused("days", parts=(lay.Part("Thaw", 0),))
        self.refused("days", parts=(lay.Part("Thaw", 1001),))
        self.refused("days", parts=(lay.Part("Thaw", True),))
        self.refused("parts", parts=(lay.Part("Thaw", 5, "week"),))
        self.made(parts=tuple(lay.Part(f"M{n}x", 1) for n in range(40)))

    def test_months_or_seasons_never_both(self) -> None:
        self.refused("parts", parts=(lay.Part("Thaw", 5), lay.Part("Summer", 5, lay.SEASON)))
        self.made(parts=(lay.Part("Thaw", 5), lay.Part("Feast", 1, lay.FESTIVAL)))

    def test_names(self) -> None:
        for bad in ("", " Thaw", "T" * 31, "12", "3rd", "day", "The", "a\nb"):
            with self.subTest(bad):
                self.refused("name", parts=(lay.Part(bad, 5),))
        self.refused("name", parts=(lay.Part("Thaw", 5), lay.Part("thaw", 5)))
        self.refused("name", name="")
        self.made(parts=(lay.Part("Feast of the Moon", 5),))

    def test_a_weekday_may_not_share_a_name_with_a_part(self) -> None:
        self.refused("name", weekdays=("Thaw", "Moonday"))
        self.refused("weekdays", weekdays=("Moonday",))
        self.refused("weekdays", weekdays=tuple(f"Day{chr(97 + n)}x" for n in range(21)))

    def test_the_leap_day_and_the_label(self) -> None:
        self.refused("leap", leap=lay.Leap(0, "Extra", "Thaw"))
        self.refused("leap", leap=lay.Leap(4, "Extra", "Nope"))
        self.refused("name", leap=lay.Leap(4, "Thaw", "Thaw"))
        self.refused("days", parts=(lay.Part("Thaw", 1000),), leap=lay.Leap(4, "Extra", "Thaw"))
        self.refused("label", year_label="A R")
        self.refused("label", year_label="1201")
        self.refused("label", year_label="ABCDEFGHI")

    def test_day_one_must_be_a_day_the_calendar_has(self) -> None:
        vale = self.made(weekdays=("Moonday", "Fireday"))
        for args in ((1, "Nope", 1), (1, "Thaw", 31), (0, "Thaw", 1), (10000, "Thaw", 1)):
            with self.subTest(args), self.assertRaises(ValueError):
                vale.start_on(*args)
        with self.assertRaises(ValueError):
            vale.start_on(1, "Thaw", 1, weekday="Sunday")
        for bad in (lay.Start(1, 30), lay.Start(1, -1), lay.Start(1, 0, 2), lay.Start(True, 0)):
            with self.subTest(bad), self.assertRaises(ValueError):
                vale.check_start(bad)

    def test_the_calendars_names_are_listed_for_the_name_finders(self) -> None:
        self.assertIn("Midsummer", H.names)
        self.assertIn("Shieldmeet", H.names)
        self.assertIn("Moonday", self.made(weekdays=("Moonday", "Fireday")).names)

    def test_a_date_before_day_one_or_past_the_clocks_end_does_not_exist(self) -> None:
        start = H.start_on(1492, "Hammer", 1)
        for number in (0, -1, 100_000, True):
            with self.subTest(number), self.assertRaises(ValueError):
                H.words(number, start)
        with self.assertRaises(ValueError):
            H.parse("Hammer 1", start, today=1, direction="sideways")
