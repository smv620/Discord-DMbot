"""What the DM screen says about the big clashes (#2660): one plain line each, three at most, the
unsure parts said, and nothing that asks the DM to do anything but decide. Invented words only."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.discord_limits import MESSAGE_MAX
from dmbot.ui import contradiction_notes as notes


def stored(reason: str = "Mira is Oskar's friend, and also his enemy.", **kw: Any) -> Any:
    return SimpleNamespace(
        **{"reason": reason, "times_unknown": False, "involves_guess": False, **kw}
    )


class TheText(unittest.TestCase):
    def test_none_says_nothing(self) -> None:
        self.assertEqual(notes.told_text([]), "")

    def test_one_line_each_and_the_decision_stays_with_the_dm(self) -> None:
        text = notes.told_text([stored(), stored("Harrow is a keep, and a ruin.")])
        self.assertIn("These may not add up", text)
        self.assertEqual(text.count("•"), 2)
        self.assertIn("Mira is Oskar's friend", text)
        self.assertTrue(text.endswith("Nothing was changed. You decide what is true."))

    def test_one_clash_uses_the_singular(self) -> None:
        self.assertIn("This may not add up", notes.told_text([stored()]))

    def test_at_most_three_are_said(self) -> None:
        text = notes.told_text([stored(f"Clash {n}.") for n in range(5)])
        self.assertEqual(text.count("•"), notes.SAY_MAX)
        self.assertNotIn("Clash 3", text)

    def test_unknown_times_and_guesses_are_said(self) -> None:
        text = notes.line(stored(times_unknown=True, involves_guess=True))
        self.assertIn("The game times aren't known.", text)
        self.assertIn("One of them is a guess.", text)
        self.assertNotIn("(", notes.line(stored()))

    def test_a_reason_that_is_missing_still_gets_a_plain_line(self) -> None:
        self.assertIn(notes.NO_REASON, notes.line(stored("  ")))

    def test_the_number_line_names_nothing(self) -> None:
        self.assertIn("1 clash ", notes.waiting_text(1))
        self.assertIn("3 clashes", notes.waiting_text(3))

    def test_it_fits_one_message(self) -> None:
        long = stored("word " * 600)
        self.assertLessEqual(len(notes.told_text([long, long, long])), MESSAGE_MAX)


if __name__ == "__main__":
    unittest.main()
