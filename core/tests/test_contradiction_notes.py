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
    def one(self, items: list[Any]) -> str:
        (text,) = notes.told_parts(items)
        return text

    def test_none_says_nothing(self) -> None:
        self.assertEqual(notes.told_parts([]), [])

    def test_one_line_each_and_the_decision_stays_with_the_dm(self) -> None:
        text = self.one([stored(), stored("Harrow is a keep, and a ruin.")])
        self.assertIn("These may not add up", text)
        self.assertEqual(text.count("•"), 2)
        self.assertIn("Mira is Oskar's friend", text)
        self.assertTrue(text.endswith("Nothing was changed. You decide what is true."))

    def test_one_clash_uses_the_singular(self) -> None:
        self.assertIn("This may not add up", self.one([stored()]))

    def test_at_most_three_are_said(self) -> None:
        text = self.one([stored(f"Clash {n}.") for n in range(5)])
        self.assertEqual(text.count("•"), notes.SAY_MAX)
        self.assertNotIn("Clash 3", text)

    def test_unknown_times_and_guesses_are_said(self) -> None:
        text = notes.line(stored(times_unknown=True, involves_guess=True))
        self.assertIn("The game times aren't known.", text)
        self.assertIn("One of them is a guess.", text)
        self.assertNotIn("(", notes.line(stored()))

    def test_a_reason_that_is_missing_still_gets_a_plain_line(self) -> None:
        self.assertIn(notes.NO_REASON, notes.line(stored("  ")))

    def test_three_long_reasons_are_split_into_messages_that_fit_and_lose_no_word(self) -> None:
        reasons = [f"Reason {n}. " + "word " * 380 for n in range(3)]  # ~1,900 characters each
        parts = notes.told_parts([stored(r) for r in reasons])
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= MESSAGE_MAX for p in parts))
        joined = "\n".join(parts)
        self.assertEqual(" ".join(joined.split()).count(" word"), 3 * 380 - 3 + 3)
        self.assertNotIn("wor d", " ".join(joined.split()))  # never split inside a word
        self.assertEqual(joined.count("word"), 3 * 380)  # nothing was cut
        self.assertTrue(joined.endswith("Nothing was changed. You decide what is true."))

    def test_the_closed_messages_note_has_a_count_only_on_a_private_screen(self) -> None:
        self.assertIn("1 clash ", notes.waiting_text(1, private=True))
        self.assertIn("3 clashes", notes.waiting_text(3, private=True))
        for count in (1, 3):
            said = notes.waiting_text(count, private=False)
            self.assertEqual(
                said, "DMbot has something for you. Open your private messages from DMbot."
            )
            self.assertFalse(any(c.isdigit() for c in said))


if __name__ == "__main__":
    unittest.main()
