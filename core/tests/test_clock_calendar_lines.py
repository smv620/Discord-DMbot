"""The clock's lines in the campaign's calendar (#5364 slice 4a-2): the pinned message, the players'
line, the split party, "where are we", rest and combat notes, timers, and how DMbot finds its own
clock line again. A campaign with no calendar of its own sees exactly the lines it always saw.
Pure: no database, no Discord traffic."""

from __future__ import annotations

import unittest
from dataclasses import replace

from dmbot.dm_screen import clock as ui
from dmbot.timebot import calendar as cal
from dmbot.timebot import clock as game
from dmbot.timebot import effects, split
from dmbot.timebot import layout as lay
from dmbot.timebot import where_are_we as where
from dmbot.timebot.calendar import Calendar
from dmbot.timebot.clock import MINUTES_PER_DAY as DAY
from dmbot.timebot.clock import Clock
from dmbot.timebot.store import Stored

AFTERNOON = 4 * DAY + 14 * 60 + 30  # day 5, 14:30
FLAME = Calendar(cal.HARPTOS, cal.day_one(1492, "Flamerule", 14))
VALE_LAYOUT = lay.Layout(
    "Vale",
    lay.four_seasons(90).parts,
    weekdays=("Sunday", "Moonday", "Fireday"),
    year_label="AR",
)
VALE = Calendar(
    cal.CUSTOM, layout=VALE_LAYOUT, layout_start=VALE_LAYOUT.start_on(1201, "Summer", 2, "Moonday")
)


class WhatItAlwaysSaid(unittest.TestCase):
    """No calendar: the clock's own words, line for line."""

    def test_the_lines_are_the_clocks_own(self) -> None:
        for minute in (0, 9 * 60, AFTERNOON, 30 * DAY + 23 * 60 + 20):
            self.assertEqual(cal.label(minute), game.label(minute))
            self.assertEqual(cal.short_label(minute), game.short_label(minute))
            self.assertEqual(
                cal.exact_label(minute), f"Day {game.day_of(minute)}, {game.clock_time(minute)}"
            )
            self.assertEqual(cal.label(minute, Calendar()), game.label(minute))

    def test_where_are_we_and_the_split_line_are_what_they_were(self) -> None:
        self.assertEqual(where.when(AFTERNOON), "Day 5, afternoon (14:30)")
        group = split.Group(1, "Tower", Clock(AFTERNOON, 0))
        self.assertEqual(split.dm_line((group,)), "Tower: Day 5, 14:30")
        self.assertEqual(split.players_lines((group,)), ["Tower: Day 5, afternoon"])


class InTheCampaignsCalendar(unittest.TestCase):
    def test_harptos_says_its_date_in_every_line(self) -> None:
        self.assertEqual(cal.short_label(AFTERNOON, FLAME), "Flamerule 18th, 1492 DR, afternoon")
        self.assertEqual(cal.label(AFTERNOON, FLAME), "Flamerule 18th, 1492 DR, afternoon (14:30)")
        self.assertEqual(cal.exact_label(AFTERNOON, FLAME), "Flamerule 18th, 1492 DR, 14:30")

    def test_a_calendar_of_its_own_with_the_weekday_on_the_dms_full_line_only(self) -> None:
        self.assertEqual(cal.short_label(AFTERNOON, VALE), "Summer 6th, 1201 AR, afternoon")
        self.assertEqual(cal.exact_label(AFTERNOON, VALE), "Summer 6th, 1201 AR, 14:30")
        # day 5 of the clock is the 5th day after Day 1 (a Moonday): weekday (1 + 4) % 3 = Fireday
        self.assertEqual(
            cal.label(AFTERNOON, VALE), "Fireday, Summer 6th, 1201 AR, afternoon (14:30)"
        )

    def test_the_date_part_is_always_calendar_words(self) -> None:
        for calendar in (FLAME, VALE):
            self.assertIn(calendar.words(AFTERNOON), cal.short_label(AFTERNOON, calendar))
            self.assertIn(calendar.words(AFTERNOON), cal.exact_label(AFTERNOON, calendar))

    def test_the_pinned_message_the_players_line_and_the_split_party(self) -> None:
        stored = Stored(Clock(AFTERNOON, 0), None, None, calendar=VALE)
        self.assertEqual(
            ui.clock_text(stored.clock, None, stored),
            "🕰️ **Fireday, Summer 6th, 1201 AR, afternoon (14:30)**",
        )
        self.assertEqual(
            ui.players_view(stored), ["Summer 6th, 1201 AR, afternoon"]
        )  # (no weekday, no exact time)
        group = split.Group(1, "Tower", Clock(AFTERNOON, 0))
        self.assertEqual(split.dm_line((group,), VALE), "Tower: Summer 6th, 1201 AR, 14:30")
        two = (group, split.Group(2, "Camp", Clock(AFTERNOON + 90, 0)))
        parted = replace(stored, groups=two, picked=1)
        self.assertEqual(
            ui.players_view(parted),
            [
                ui.SPLIT_HEAD,
                "Tower: Summer 6th, 1201 AR, afternoon",
                "Camp: Summer 6th, 1201 AR, late afternoon",
            ],
        )
        self.assertIn("Tower: Summer 6th, 1201 AR, 14:30 · Camp: Summer 6th, 1201 AR, 16:00",
                      ui.clock_text(stored.clock, None, parted))  # fmt: skip

    def test_where_are_we(self) -> None:
        stored = Stored(Clock(AFTERNOON, 0), None, None, calendar=VALE)
        self.assertEqual(
            where.line(stored), "🕰️ Where we are: Summer 6th, 1201 AR, afternoon (14:30)."
        )

    def test_notes_for_rests_combat_timers_and_the_tired_check(self) -> None:
        later = Clock(AFTERNOON, 0)
        self.assertEqual(
            ui.rest_note("dawn", later, calendar=VALE),
            "🌅 Skipped to dawn. Now Summer 6th, 1201 AR, afternoon.",
        )
        self.assertEqual(ui.rest_note("dawn", later), "🌅 Skipped to dawn. Now Day 5, afternoon.")
        self.assertIn(
            "Now Summer 6th, 1201 AR, afternoon.", ui.combat_note("cend", Clock(0, 0), later, VALE)
        )
        self.assertIn(
            "(Summer 2nd, 1201 AR, near midnight)", ui.tired_line(later, VALE)
        )  # (its last long rest)
        self.assertEqual(
            ui.players_line("long", Clock(0, 0), later, VALE),
            "Long rest done. Summer 6th, 1201 AR, afternoon.",
        )
        timer = effects.Effect(1, "Bless", None, 600, False, AFTERNOON + 60, False)
        self.assertEqual(
            timer.line(AFTERNOON, calendar=VALE),
            "⏳ Bless: until Summer 6th, 1201 AR, late afternoon",
        )
        self.assertEqual(timer.line(AFTERNOON), "⏳ Bless: until Day 5, late afternoon")


class FindingItsOwnLineAgain(unittest.TestCase):
    """DMbot recognises its clock line by shape, so a date in any calendar must be found."""

    def test_a_plain_line_is_found_whatever_the_date_looks_like(self) -> None:
        for line in (
            "Day 5, afternoon",
            "Summer 6th, 1201 AR, afternoon",
            "Flamerule 18th, 1492 DR, late afternoon",
            "Midwinter, 1492 DR, near midnight",
            "Fireday, Summer 6th, 1201 AR, dawn",
        ):
            with self.subTest(line):
                self.assertTrue(ui.is_plain(f"-# 🕰️ {line}"), line)
                self.assertEqual(ui.view_of(f"-# 🕰️ {line}"), line)

    def test_a_view_inside_a_sentence_is_found_and_the_sentence_is_not_plain(self) -> None:
        said = "-# 🕰️ Long rest done. Summer 6th, 1201 AR, dawn."
        self.assertEqual(ui.view_of(said), "Summer 6th, 1201 AR, dawn")
        self.assertFalse(ui.is_plain(said))
        said = "-# 🕰️ A new day begins. Day 6, morning."
        self.assertEqual(ui.view_of(said), "Day 6, morning")
        self.assertFalse(ui.is_plain(said))

    def test_other_lines_are_not_a_view(self) -> None:
        for line in ("Hello there", "Summer 6th, 1201 AR", "Day 5, sometime"):
            self.assertFalse(ui.is_plain(f"-# 🕰️ {line}"), line)
