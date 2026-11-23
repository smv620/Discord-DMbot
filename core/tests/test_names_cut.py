"""Names a cap leaves out are counted, never silent (#5055 A, #5056 point 2; Supervisor's ruling):
the lore read's one-document limit, and the notices' words. Pure. Invented names."""

from __future__ import annotations

import contextlib
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

from dmbot.ai import AIError
from dmbot.memory import background as bg
from dmbot.memory import lore_read as lr
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


class TheHeldPictureFilters(unittest.TestCase):
    """#5056 point 4: a held picture name passes `merge`'s filters, taken whole."""

    def test_an_ordinary_word_a_known_name_and_a_generic_item_are_left_out(self) -> None:
        dropped: Counter[str] = Counter()
        self.assertFalse(bg.passes(bg.Found("Captain", "npc", (), (), ""), ()))
        self.assertFalse(bg.passes(bg.Found("Torvan Kell", "npc", (), (), ""), {"torvan kell"}))
        self.assertFalse(bg.passes(bg.Found("dagger", "item", (), (), ""), (), dropped=dropped))
        self.assertEqual(dropped["item_not_unique"], 1)
        self.assertTrue(bg.passes(bg.Found("Wand of Conducting", "item", (), (), ""), ()))
        # (a list-shaped name is never split: it is one name to check, as before)
        self.assertTrue(bg.passes(bg.Found("Aldmoor, Quillgate, Fenwick", "place", (), (), ""), ()))


class TheUnreadSlice(unittest.IsolatedAsyncioTestCase):
    async def test_a_slice_that_fails_counts_its_names(self) -> None:
        """#5056 point 3: a one-line slice whose shaping keeps failing is lost, and its name is
        counted, never silent."""
        ai = SimpleNamespace(complete=AsyncMock(side_effect=AIError("down")), model="m")
        stats = lr.Stats()
        found, joined, cut = await lr._shape(
            ai, "Korvath rides.", ["Korvath"], [], {}, contextlib.nullcontext(), stats
        )
        self.assertEqual((found, joined, cut), ([], [], True))
        self.assertEqual(stats.dropped[bg.NAMES_UNREAD], 1)
        line = names_over_cap_line(stats.dropped)
        self.assertIn("1 name couldn't be read in full, so it was left out", line)
        self.assertIn("Upload the document again", line)

    async def test_a_two_line_slice_that_always_fails_counts_each_name_once(self) -> None:
        # (halved and tried again first: the two halves each count their one name, no more)
        ai = SimpleNamespace(complete=AsyncMock(side_effect=AIError("down")), model="m")
        stats = lr.Stats()
        await lr._shape(
            ai, "Korvath and Belmira.", ["Korvath", "Belmira"], [], {},
            contextlib.nullcontext(), stats,
        )  # fmt: skip
        self.assertEqual(stats.dropped[bg.NAMES_UNREAD], 2)


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
        self.assertIn("As many as 1 more name is waiting", names_cut_note(1))
        self.assertIn("As many as 3 more names are waiting", names_cut_note(3))
        self.assertIn("📝 More names in ❓ Questions for you", names_cut_note(3))


if __name__ == "__main__":
    unittest.main()
