"""The card's three lines (#5050): no database, no AI."""

from __future__ import annotations

import unittest

from dmbot.memory import card_view
from dmbot.memory.card_summaries import Mention, Summary

START = 1_700_000_000
NUMBERS = {START: 1, START + 86_400: 2}


def summary(**kw: object) -> Summary:
    base: dict[str, object] = {
        "who": "A halfling innkeeper.", "facts_key": "k", "made_at": 5, "first": None,
        "last": None, "scanned_at": 6,
    }  # fmt: skip
    return Summary(**{**base, **kw})  # type: ignore[arg-type]


class TheLines(unittest.TestCase):
    def test_a_character_the_job_has_not_reached_reads_still_writing(self) -> None:
        got = card_view.view(None, "Shafina", NUMBERS)
        self.assertEqual(got.lines(), ["DMbot is still writing this."])

    def test_all_three_lines_say_the_scene(self) -> None:
        first = Mention(START, 2, "the party reached the inn")
        last = Mention(START + 86_400, 1, "")
        got = card_view.view(summary(first=first, last=last), "Shafina", NUMBERS)
        self.assertEqual(got.who, "A halfling innkeeper.")
        self.assertTrue(got.first and got.first.startswith("First met: Session 1, "))
        self.assertIn("The party reached the inn.", got.first or "")
        self.assertTrue(got.last and got.last.startswith("Last mentioned: Session 2, "))
        self.assertIn("still writing what happened", got.last or "")

    def test_never_mentioned_says_so_once(self) -> None:
        got = card_view.view(summary(), "Shafina", NUMBERS)
        self.assertEqual(got.lines(), ["A halfling innkeeper.", "Not mentioned at the table yet."])

    def test_mentions_not_worked_out_yet_say_nothing_about_them(self) -> None:
        got = card_view.view(summary(scanned_at=None), "Shafina", NUMBERS)
        self.assertEqual(got.lines(), ["A halfling innkeeper."])

    def test_a_deleted_session_leaves_its_line_out(self) -> None:
        gone = Mention(START - 5, 1, "x")
        got = card_view.view(summary(first=gone, last=Mention(START, 1, "later")), "S", NUMBERS)
        self.assertIsNone(got.first)
        self.assertTrue(got.last)

    def test_a_hidden_entry_says_how_it_is_written(self) -> None:
        got = card_view.view(summary(who=None), "Shafina", NUMBERS)
        self.assertEqual(got.who, "DMbot writes this line only from what your players may know.")


class WhicheverCameFirst(unittest.TestCase):
    def test_lore_read_before_the_first_session_is_the_first_line(self) -> None:
        first = Mention(START + 100, 2, "the party reached the inn")
        got = card_view.view(summary(first=first), "Shafina", NUMBERS, ("Notes", "Inn", START))
        self.assertEqual(got.first, "First met: in your lore, *Notes*, under 'Inn'.")

    def test_lore_read_after_the_first_session_leaves_the_session(self) -> None:
        first = Mention(START, 2, "the party reached the inn")
        got = card_view.view(summary(first=first), "Shafina", NUMBERS, ("Notes", "", START + 5))
        self.assertTrue(got.first and got.first.startswith("First met: Session 1, "))

    def test_only_in_the_lore_says_so_and_not_mentioned(self) -> None:
        got = card_view.view(summary(), "Shafina", NUMBERS, ("Notes", "", START))
        self.assertEqual(
            got.lines(),
            [
                "A halfling innkeeper.",
                "First met: in your lore, *Notes*.",
                "Not mentioned at the table yet.",
            ],
        )
