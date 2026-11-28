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


class TypedIntoAForm(unittest.TestCase):
    """What a DM types into Discord's forms becomes the same body the website sends (#5364 3c)."""

    def test_parts_one_to_a_line(self) -> None:
        got = cs.parts_from_text(
            "Spring 90 season\n\n  Summer 90 SEASON \nFeast of the Moon 3 festival\nHammer 30"
        )
        self.assertEqual(
            got,
            [
                {"name": "Spring", "days": 90, "kind": "season"},
                {"name": "Summer", "days": 90, "kind": "season"},
                {"name": "Feast of the Moon", "days": 3, "kind": "festival"},
                {"name": "Hammer", "days": 30, "kind": "month"},
            ],
        )

    def test_a_line_that_is_not_a_part_says_how_to_write_one(self) -> None:
        for text in ("Hammer", "Hammer thirty", "30", "Hammer 30 week", "Hammer 30 month extra"):
            with self.subTest(text), self.assertRaises(cs.CalendarProblem) as caught:
                cs.parts_from_text(text)
            (words,) = caught.exception.problems
            self.assertTrue(words.startswith("Line 1, “"), words)
            self.assertTrue(words.endswith("like Hammer 30."), words)
        with self.assertRaises(cs.CalendarProblem) as empty:
            cs.parts_from_text("\n  \n")
        self.assertEqual(empty.exception.problems, [cs.PROBLEMS["parts"]])

    def test_the_extra_day(self) -> None:
        self.assertEqual(
            cs.leap_from_text(" Shieldmeet , 4 , Midsummer "),
            {"name": "Shieldmeet", "every": 4, "after": "Midsummer"},
        )
        self.assertIsNone(cs.leap_from_text("  "))
        for text in ("Shieldmeet", "Shieldmeet, four, Midsummer", "a, 4, b, c", "x, 99999, y"):
            with self.subTest(text), self.assertRaises(cs.CalendarProblem):
                cs.leap_from_text(text)

    def test_a_whole_calendar_typed_comes_out_as_the_websites_body(self) -> None:
        made = cs.definition_from_text(
            " Vale ",
            "Spring 90 season\nSummer 90 season",
            "Sunday, Moonday,",
            "AR, after the Reckoning",
            "Extra, 4, Spring",
        )
        self.assertEqual(made["weekdays"], ["Sunday", "Moonday"])
        self.assertEqual(made["yearLabel"], "AR")
        self.assertEqual(made["yearSay"], "after the Reckoning")
        self.assertEqual(made["leap"], {"name": "Extra", "every": 4, "after": "Spring"})
        body = {
            "kind": "custom",
            "def": made,
            "dayOne": cs.day_one_from_text("1201", "Summer", "2", "Moonday"),
        }
        got = cs.from_body(body)
        self.assertEqual(cs.line_of(got), "Day 1 is Summer 2nd, 1201 AR")
        self.assertEqual(got.words(0, spoken=True), "Summer 2nd, 1201 after the Reckoning")

    def test_a_label_with_no_phrase_and_no_weekdays(self) -> None:
        made = cs.definition_from_text("Vale", "Spring 90 season", "", "AR", "")
        self.assertEqual((made["weekdays"], made["leap"], "yearSay" in made), ([], None, False))

    def test_day_one_needs_whole_numbers(self) -> None:
        self.assertEqual(
            cs.day_one_from_text(" 1201 ", " Summer ", " 2 ", " Moonday "),
            {"year": 1201, "part": "Summer", "day": 2, "weekday": "Moonday"},
        )
        self.assertEqual(
            cs.day_one_from_text("1201", "Midwinter", ""),
            {"year": 1201, "part": "Midwinter", "day": None},
        )
        for year, day in (
            ("twelve", "2"),
            ("1201", "two"),
            ("-1", "2"),
            ("", "2"),
            ("1201", "1.5"),
        ):
            with self.subTest((year, day)), self.assertRaises(cs.CalendarProblem) as caught:
                cs.day_one_from_text(year, "Summer", day)
            self.assertEqual(
                caught.exception.problems,
                [cs.PROBLEMS["year_number" if not year.strip().isdecimal() else "day_number"]],
            )


class BeforeSaving(unittest.TestCase):
    own = cs.from_body(custom(day=2))
    harptos = cs.from_body(
        {"kind": "harptos", "dayOne": {"year": 1492, "part": "Flamerule", "day": 14}}
    )
    days = cal.Calendar()

    def test_nothing_to_say_when_nothing_changes(self) -> None:
        for calendar in (self.own, self.harptos, self.days):
            self.assertEqual(cs.warnings_for(calendar, calendar), [])

    def test_leaving_a_calendar_of_its_own_says_it_is_removed(self) -> None:
        for new in (self.days, self.harptos):
            self.assertEqual(cs.warnings_for(self.own, new), [cs.REMOVED, cs.VOICES, cs.COUNTED])

    def test_saving_one_over_another_says_it_replaces_it(self) -> None:
        other = cs.from_body(custom(day=3))
        self.assertEqual(cs.warnings_for(self.own, other), [cs.REPLACES, cs.VOICES, cs.COUNTED])

    def test_a_new_calendar_over_days_or_harptos_only_warns_about_voices_and_counted_days(
        self,
    ) -> None:
        self.assertEqual(cs.warnings_for(self.days, self.own), [cs.VOICES, cs.COUNTED])
        self.assertEqual(cs.warnings_for(self.harptos, self.own), [cs.VOICES, cs.COUNTED])
        self.assertEqual(cs.warnings_for(self.own, self.days)[0], cs.REMOVED)


class TheFormsInTheirOwnWords(unittest.TestCase):
    """The Expert's four points on "My own" (#5364 3c): read a part line from the right, name the
    line that is wrong, fill the forms from a calendar that exists, and say the extra day in
    words."""

    def test_a_part_line_is_read_from_the_right(self) -> None:
        got = cs.parts_from_text("Month 3 30\nFeast of the Moon 1 festival\nYear 2024 5 season")
        self.assertEqual(
            got,
            [
                {"name": "Month 3", "days": 30, "kind": "month"},
                {"name": "Feast of the Moon", "days": 1, "kind": "festival"},
                {"name": "Year 2024", "days": 5, "kind": "season"},
            ],
        )

    def test_a_line_that_cannot_be_read_is_named_with_its_number(self) -> None:
        with self.assertRaises(cs.CalendarProblem) as caught:
            cs.parts_from_text("Hammer 30\n\nnonsense here\nSummer 90")
        (words,) = caught.exception.problems
        self.assertEqual(
            words, "Line 3, “nonsense here”: write a name, then its days, like Hammer 30."
        )
        with self.assertRaises(cs.CalendarProblem) as long:
            cs.parts_from_text("x" * 200)
        self.assertLess(len(long.exception.problems[0]), len(cs.PROBLEMS["parts_text"]) + 90)

    def test_a_calendar_that_exists_comes_back_as_the_forms_boxes(self) -> None:
        layout = lay.Layout(
            "Vale",
            (
                lay.Part("Spring", 90, lay.SEASON),
                lay.Part("Feast of the Moon", 1, lay.FESTIVAL),
                lay.Part("Autumn", 90, lay.SEASON),
            ),
            lay.Leap(4, "Extra", "Spring"),
            ("Sunday", "Moonday"),
            "AR",
            "after the Reckoning",
        )
        boxes = cs.text_of(layout)
        self.assertEqual(
            boxes["parts"], "Spring 90 season\nFeast of the Moon 1 festival\nAutumn 90 season"
        )
        self.assertEqual(boxes["label"], "AR, after the Reckoning")
        self.assertEqual(boxes["leap"], "Extra, 4, Spring")
        again = cs.definition_from_text(
            boxes["name"], boxes["parts"], boxes["weekdays"], boxes["label"], boxes["leap"]
        )
        self.assertEqual(lay.Layout.from_json(again), layout)
        plain = lay.four_seasons(90)
        boxes = cs.text_of(plain)
        self.assertEqual(
            lay.Layout.from_json(
                cs.definition_from_text(boxes["name"], boxes["parts"], "", "", "")
            ),
            plain,
        )

    def test_the_extra_day_is_said_in_words_once_it_applies(self) -> None:
        layout = lay.Layout("Vale", lay.four_seasons(90).parts, lay.Leap(4, "Extra", "Spring"))
        calendar = cs.from_body(
            {
                "kind": "custom",
                "def": layout.to_json(),
                "dayOne": {"year": 1201, "part": "Summer", "day": 2},
            }
        )
        self.assertEqual(cs.leap_line(calendar), "Extra comes every 4 years, after Spring.")
        self.assertIsNone(cs.leap_line(cs.from_body(custom(day=2))))
        self.assertIsNone(cs.leap_line(cal.Calendar()))
