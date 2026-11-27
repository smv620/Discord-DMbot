"""A recap's date in the campaign's calendar (#5364 slice 4a-3): the heading the table reads, the
line the voice opens with, and how a campaign with no calendar still reads "Day N". Pure."""

from __future__ import annotations

import unittest

from dmbot.recaps import draft
from dmbot.recaps.audio import script as sc
from dmbot.timebot import calendar as cal
from dmbot.timebot import layout as lay
from dmbot.timebot.calendar import Calendar

SEASONS = lay.Layout("Vale", lay.four_seasons(90).parts, year_label="AR")
VALE = Calendar(cal.CUSTOM, layout=SEASONS, layout_start=SEASONS.start_on(1201, "Summer", 2))
FLAME = Calendar(cal.HARPTOS, cal.day_one(1492, "Flamerule", 14))


class DayWords(unittest.TestCase):
    def test_the_day_in_each_calendar(self) -> None:
        self.assertEqual(cal.day_words(5), "Day 5")
        self.assertEqual(cal.day_words(5, Calendar()), "Day 5")
        self.assertEqual(cal.day_words(5, FLAME), "Flamerule 18th, 1492 DR")
        self.assertEqual(cal.day_words(5, VALE), "Summer 6th, 1201 AR")
        self.assertEqual(cal.day_words(1, VALE), "Summer 2nd, 1201 AR")

    def test_a_day_that_is_not_known_has_no_words(self) -> None:
        for day in (None, 0, -3):
            self.assertIsNone(cal.day_words(day, VALE))


class TheHeading(unittest.TestCase):
    def test_what_it_always_said(self) -> None:
        self.assertEqual(draft.heading("The Quay", 5), "**Session Summary: The Quay (Day 5)**")
        self.assertEqual(draft.heading("The Quay", None), "**Session Summary: The Quay**")
        self.assertEqual(draft.heading("The Quay", 5, Calendar()), draft.heading("The Quay", 5))

    def test_in_the_campaigns_calendar(self) -> None:
        self.assertEqual(
            draft.heading("The Quay", 5, VALE),
            "**Session Summary: The Quay (Summer 6th, 1201 AR)**",
        )
        self.assertEqual(
            draft.heading("The Quay", 5, FLAME),
            "**Session Summary: The Quay (Flamerule 18th, 1492 DR)**",
        )
        self.assertEqual(draft.heading("The Quay", None, VALE), "**Session Summary: The Quay**")


class TheVoicesFirstLine(unittest.TestCase):
    def test_what_it_always_said_and_the_campaigns_calendar(self) -> None:
        self.assertEqual(sc.title_line("The Quay", 5), "Session Summary: The Quay, Day 5")
        self.assertEqual(
            sc.title_line("The Quay", 5, VALE), "Session Summary: The Quay, Summer 6th, 1201 AR"
        )
        self.assertEqual(sc.title_line("The Quay", None, VALE), "Session Summary: The Quay")

    def test_the_script_opens_with_it_and_changes_when_the_calendar_does(self) -> None:
        plain = sc.build("The Quay", 5, "They sailed.")
        vale = sc.build("The Quay", 5, "They sailed.", calendar=VALE)
        self.assertEqual(plain.paragraphs[0].spoken, "Session Summary: The Quay, Day 5")
        self.assertEqual(
            vale.paragraphs[0].spoken, "Session Summary: The Quay, Summer 6th, 1201 AR"
        )
        self.assertEqual(  # (only the title line's words differ)
            [x.spoken for x in plain.paragraphs[1:]], [x.spoken for x in vale.paragraphs[1:]]
        )
