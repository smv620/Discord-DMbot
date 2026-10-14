"""Spell slots on the card, in the sheet view and in FightBot's prompt (#1176)."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import fight_prompt, sheet_checks, sheet_view

SNAPSHOT: dict[str, Any] = {
    "v": 1,
    "source": "dndbeyond",
    "name": "Lyra",
    "slots": [{"level": 1, "max": 4, "used": 2}, {"level": 2, "max": 2, "used": 2}],
    "spells": ["Hunter's Mark"],
}


class Slots(unittest.TestCase):
    def test_the_line_says_how_many_are_left(self) -> None:
        self.assertEqual(
            sheet_checks.slot_line(SNAPSHOT), "level 1: 2 of 4 left, level 2: 0 of 2 left"
        )
        self.assertEqual(sheet_checks.slot_line({}), "")
        self.assertEqual(sheet_checks.slot_line({"slots": "x"}), "")

    def test_the_card_shows_them_only_when_there_are_some(self) -> None:
        ids = [c.part for c in sheet_checks.checks(SNAPSHOT)]
        self.assertIn("slots", ids)
        self.assertNotIn("slots", [c.part for c in sheet_checks.checks({**SNAPSHOT, "slots": []})])

    def test_the_prompt_gets_the_counts_and_may_say_them(self) -> None:
        facts = fight_prompt.sheet_facts(SNAPSHOT)
        self.assertIn("Spell slots left: 2 level 1 slots", facts)
        self.assertNotIn("level 2", facts)  # spent: nothing to repeat
        self.assertNotIn("of 4", facts)
        self.assertIn("level 1 slots", fight_prompt.PROMPT)

    def test_using_a_slot_asks_for_a_new_reminder(self) -> None:
        later = {**SNAPSHOT, "slots": [{"level": 1, "max": 4, "used": 3}]}
        self.assertNotEqual(
            fight_prompt.fingerprint(SNAPSHOT, 2), fight_prompt.fingerprint(later, 2)
        )

    def test_the_sheet_view_lists_them_with_the_features(self) -> None:
        self.assertIn("Spell slots: level 1: 2 of 4 left", sheet_view._show_features(SNAPSHOT))


if __name__ == "__main__":
    unittest.main()
