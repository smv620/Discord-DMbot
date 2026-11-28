"""A calendar as a setting (#5364 slice 3): the body Discord's settings and the website send, the
calendar it makes, the preview line, and plain words for what is wrong. Pure."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.timebot import calendar as cal
from dmbot.timebot import calendar_setting as cs
from dmbot.timebot import layout as lay

SEASONS = lay.four_seasons(90).to_json()
WEEK = lay.Layout(
    "Vale",
    (*lay.four_seasons(90).parts, lay.Part("Longnight", 1, lay.FESTIVAL)),
    weekdays=("Sunday", "Moonday", "Fireday"),
    year_label="AR",
).to_json()


def custom(**one: Any) -> dict[str, Any]:
    return {"kind": "custom", "def": SEASONS, "dayOne": {"year": 1201, "part": "Summer", **one}}


class MakingTheCalendar(unittest.TestCase):
    def test_days_needs_nothing(self) -> None:
        self.assertEqual(cs.from_body({"kind": "days"}), cal.Calendar())
        self.assertEqual(cs.from_body({"kind": "days", "def": 5, "dayOne": "x"}), cal.Calendar())

    def test_harptos_on_its_own_dates(self) -> None:
        got = cs.from_body(
            {"kind": "harptos", "dayOne": {"year": 1492, "part": "Flamerule", "day": 14}}
        )
        self.assertEqual(got, cal.Calendar(cal.HARPTOS, cal.day_one(1492, "Flamerule", 14)))
        fest = cs.from_body({"kind": "harptos", "dayOne": {"year": 1492, "part": "Midsummer"}})
        self.assertEqual(fest.words(0), "Midsummer, 1492 DR")

    def test_a_calendar_of_its_own(self) -> None:
        got = cs.from_body(custom(day=2))
        self.assertEqual(got.kind, cal.CUSTOM)
        self.assertEqual(got.words(0), "Summer 2nd, 1201")
        self.assertEqual(got.words(DAY := 1440), "Summer 3rd, 1201")
        self.assertEqual(DAY, 1440)

    def test_the_weekday_of_day_one(self) -> None:
        body = {
            "kind": "custom",
            "def": WEEK,
            "dayOne": {"year": 1201, "part": "Summer", "day": 2, "weekday": "Fireday"},
        }
        got = cs.from_body(body)
        assert got.layout is not None and got.layout_start is not None
        self.assertEqual(got.layout.weekday_of(1, got.layout_start), "Fireday")

    def test_round_trip_through_the_setting_the_page_reads(self) -> None:
        for body in (
            custom(day=2),
            {"kind": "harptos", "dayOne": {"year": 1492, "part": "Midsummer"}},
        ):
            made = cs.from_body(body)
            one = cs.day_one_of(made)
            assert one is not None
            again = cs.from_body({**body, "dayOne": one})
            self.assertEqual(again, made)
            self.assertEqual(cs.line_of(again), cs.line_of(made))

    def test_day_one_of_a_calendar_with_weekdays_and_a_festival(self) -> None:
        body = {
            "kind": "custom",
            "def": WEEK,
            "dayOne": {"year": 1201, "part": "Longnight", "weekday": "Moonday"},
        }
        got = cs.from_body(body)
        self.assertEqual(
            cs.day_one_of(got),
            {"year": 1201, "part": "Longnight", "day": None, "weekday": "Moonday"},
        )
        self.assertEqual(cs.line_of(got), "Day 1 is Longnight, 1201 AR")


class ThePreview(unittest.TestCase):
    def test_today_in_the_calendar_or_nothing(self) -> None:
        got = cs.from_body(custom(day=2))
        self.assertEqual(cs.today_of(got, 10 * 1440), "Today is Summer 12th, 1201")
        self.assertIsNone(cs.today_of(got, None))
        self.assertIsNone(cs.today_of(cal.Calendar(), 5 * 1440))

    def test_days_has_no_line_and_the_others_say_day_one(self) -> None:
        self.assertIsNone(cs.line_of(cal.Calendar()))
        self.assertIsNone(cs.day_one_of(cal.Calendar()))
        self.assertEqual(cs.line_of(cs.from_body(custom(day=2))), "Day 1 is Summer 2nd, 1201")


class RefusingWithWords(unittest.TestCase):
    def problem(self, body: object) -> list[str]:
        with self.assertRaises(cs.CalendarProblem) as caught:
            cs.from_body(body)
        return caught.exception.problems

    def test_what_is_not_a_setting(self) -> None:
        odd: tuple[object, ...] = (None, [], "days")
        for body in odd:
            with self.subTest(str(body)):
                self.assertEqual(self.problem(body), [cs.PROBLEMS["layout"]])
        for kindless in ({}, {"kind": "gregorian"}, {"kind": 5}):
            with self.subTest(str(kindless)):
                self.assertEqual(self.problem(kindless), [cs.PROBLEMS["kind"]])

    def test_day_one_must_be_asked_for_and_whole(self) -> None:
        for body in (
            {"kind": "custom", "def": SEASONS},
            {"kind": "custom", "def": SEASONS, "dayOne": {"part": "Summer"}},
            {"kind": "harptos", "dayOne": {"year": "1492", "part": "Hammer"}},
            {"kind": "harptos", "dayOne": {"year": 1492, "part": 5}},
            custom(day=1.5),
            custom(day=True),
            {"kind": "custom", "def": SEASONS, "dayOne": {"year": True, "part": "Summer"}},
        ):
            with self.subTest(str(body)[:60]):
                self.assertEqual(self.problem(body), [cs.PROBLEMS["start"]])

    def test_a_part_a_day_or_a_weekday_it_has_not_got(self) -> None:
        self.assertEqual(
            self.problem(custom(day=2) | {"dayOne": {"year": 1201, "part": "Nope", "day": 2}}),
            [cs.PROBLEMS["part"]],
        )
        self.assertEqual(self.problem(custom(day=91)), [cs.PROBLEMS["day"]])
        self.assertEqual(self.problem(custom(day=2, weekday="Moonday")), [cs.PROBLEMS["weekday"]])
        self.assertEqual(
            self.problem(custom(day=2) | {"dayOne": {"year": 0, "part": "Summer", "day": 2}}),
            [cs.PROBLEMS["date"]],
        )
        self.assertEqual(
            self.problem({"kind": "harptos", "dayOne": {"year": 1493, "part": "Shieldmeet"}}),
            [cs.PROBLEMS["part"]],
        )

    def test_a_bad_definition_says_what_to_change(self) -> None:
        bad = dict(SEASONS)
        bad["parts"] = []
        self.assertEqual(self.problem({**custom(day=2), "def": bad}), [cs.PROBLEMS["parts"]])
        bad = {**SEASONS, "yearLabel": "1201"}
        self.assertEqual(self.problem({**custom(day=2), "def": bad}), [cs.PROBLEMS["label"]])
        bad = {**SEASONS, "weekdays": ["Only"]}
        self.assertEqual(self.problem({**custom(day=2), "def": bad}), [cs.PROBLEMS["weekdays"]])
        self.assertEqual(
            self.problem({**custom(day=2), "def": {"junk": 1}}), [cs.PROBLEMS["layout"]]
        )

    def test_every_refusal_code_the_layout_makes_has_words(self) -> None:
        codes = {
            "name",
            "parts",
            "days",
            "leap",
            "weekdays",
            "label",
            "date",
            "day",
            "part",
            "weekday",
            "start",
            "layout",
            "kind",
        }
        self.assertLessEqual(codes, set(cs.PROBLEMS))
        for words in cs.PROBLEMS.values():
            self.assertTrue(words.endswith("."))
            self.assertLess(len(words), 200)


class MoreBodies(unittest.TestCase):
    """The settings body's edges: Harptos day one, a festival's day, the spoken year label, and
    extra keys (#5373, follow-up of #5364)."""

    festivals = lay.Layout(
        "Vale",
        (
            lay.Part("Moon+Sun", 30, lay.SEASON),
            lay.Part("Dry", 30, lay.SEASON),
            lay.Part("Moonfeast", 3, lay.FESTIVAL),
        ),
        year_label="AR",
    )
    spoken = lay.Layout(
        "Vale", lay.four_seasons(90).parts, year_label="AR", year_say="after the Reckoning"
    )

    def test_day_one_on_the_leap_day_of_harptos(self) -> None:
        body = {"kind": "harptos", "dayOne": {"year": 1492, "part": "Shieldmeet"}}
        self.assertEqual(cs.line_of(cs.from_body(body)), "Day 1 is Shieldmeet, 1492 DR")
        self.assertEqual(
            cs.day_one_of(cs.from_body(body)),
            {"year": 1492, "part": "Shieldmeet", "day": None, "weekday": None},
        )

    def test_day_one_on_a_day_of_a_several_day_festival_keeps_that_day(self) -> None:
        body = {
            "kind": "custom",
            "def": self.festivals.to_json(),
            "dayOne": {"year": 1201, "part": "Moonfeast", "day": 2},
        }
        self.assertEqual(cs.line_of(cs.from_body(body)), "Day 1 is Moonfeast 2nd, 1201 AR")
        self.assertEqual(
            cs.day_one_of(cs.from_body(body)),
            {"year": 1201, "part": "Moonfeast", "day": 2, "weekday": None},
        )

    def test_the_voices_year_label_is_kept_and_only_the_voice_uses_it(self) -> None:
        body = {
            "kind": "custom",
            "def": self.spoken.to_json(),
            "dayOne": {"year": 1201, "part": "Summer", "day": 2},
        }
        got = cs.from_body(body)
        self.assertEqual(got.words(0), "Summer 2nd, 1201 AR")
        self.assertEqual(got.words(0, spoken=True), "Summer 2nd, 1201 after the Reckoning")

    def test_extra_keys_in_the_body_are_ignored_but_not_in_the_definition(self) -> None:
        self.assertEqual(cs.from_body({"kind": "days", "extra": 1}), cal.Calendar())
        body = {
            "kind": "custom",
            "def": {**lay.four_seasons(90).to_json(), "extra": 1},
            "dayOne": {"year": 1201, "part": "Summer", "day": 2},
        }
        with self.assertRaises(cs.CalendarProblem) as caught:
            cs.from_body(body)
        self.assertEqual(caught.exception.problems, [cs.PROBLEMS["layout"]])
