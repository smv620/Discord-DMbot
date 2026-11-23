"""Names a cap leaves out are counted, never silent (#5055 A, #5056 point 2; Supervisor's ruling):
the lore read's one-document limit, and the notices' words. Pure. Invented names."""

from __future__ import annotations

import unittest
from collections import Counter

from dmbot.memory import background as bg
from dmbot.ui.name_background import names_over_cap_line
from dmbot.ui.names import names_cut_note


def _names(n: int) -> list[bg.Found]:
    return [bg.Found(f"Torvan Kell{i:03d}", "npc", (), (), "") for i in range(n)]


class TheLoreCap(unittest.TestCase):
    def test_names_past_the_cap_are_counted_and_the_first_kept(self) -> None:
        dropped: Counter[str] = Counter()
        got = bg.merge([_names(7)], cap=5, dropped=dropped)
        self.assertEqual([f.name for f in got], [f.name for f in _names(5)])
        self.assertEqual(dropped[bg.NAMES_OVER_CAP], 2)

    def test_nothing_is_counted_under_the_cap(self) -> None:
        dropped: Counter[str] = Counter()
        bg.merge([_names(5)], cap=5, dropped=dropped)
        self.assertNotIn(bg.NAMES_OVER_CAP, dropped)

    def test_a_folded_disguise_takes_no_slot_and_is_not_counted_as_cut(self) -> None:
        dropped: Counter[str] = Counter()
        names = [
            bg.Found("Vecnor", "npc", (), ("Brother Aldric",), ""),
            bg.Found("Brother Aldric", "npc", (), (), ""),
            bg.Found("Mira Holt", "npc", (), (), ""),
        ]
        got = bg.merge([names], cap=2, dropped=dropped)
        self.assertEqual([f.name for f in got], ["Vecnor", "Mira Holt"])
        self.assertNotIn(bg.NAMES_OVER_CAP, dropped)


class TheWords(unittest.TestCase):
    def test_the_upload_line_says_how_many_and_what_to_do(self) -> None:
        self.assertEqual(names_over_cap_line({}), "")
        line = names_over_cap_line({bg.NAMES_OVER_CAP: 12})
        self.assertIn(f"lists up to {bg.MAX_NAMES} names from one document", line)
        self.assertIn("12 more names were left out", line)
        self.assertIn("split the document", line)
        one = names_over_cap_line({bg.NAMES_OVER_CAP: 1})
        self.assertIn("1 more name was left out", one)

    def test_the_import_line_is_a_count_and_says_what_to_do(self) -> None:
        self.assertIn("up to 10 new names from each session", names_cut_note(3))
        self.assertIn("As many as 1 more name was left out", names_cut_note(1))
        self.assertIn("As many as 3 more names were left out", names_cut_note(3))
        self.assertIn("➕ Add a name", names_cut_note(3))


if __name__ == "__main__":
    unittest.main()
