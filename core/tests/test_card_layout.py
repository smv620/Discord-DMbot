"""The name card always builds (#2339): Discord refuses a row wider than five, and a select takes
a whole row, so the "Show all" menu must find a row with nothing on it."""

from __future__ import annotations

import itertools
import unittest

from dmbot.ui import name_card

CUT = [("connections", "Connections (9)"), ("names", "Names (7)")]
# What a card can really carry together: one kind's own buttons, and any of the shared ones.
KINDS: tuple[dict[str, bool], ...] = (
    {},
    {"item": True},
    {"clue": True},
    {"standing": True},
    {"deed": True},
    {"thread": True},
    {"thread": True, "promise": True},
)
SHARED = ("others", "sheet", "led", "book", "say")


class Layout(unittest.TestCase):
    def build(self, **kw: object) -> name_card.NameCard:
        return name_card.NameCard("c" * 32, "e" * 32, cut=CUT, **kw)  # type: ignore[arg-type]

    def test_every_real_mix_of_undo_buttons_and_switches_builds(self) -> None:
        undos = [dict(zip(("undo_connection", "undo_spelling", "finish_past"), vals, strict=True))
                 for vals in itertools.product((None, 1), repeat=3)]  # fmt: skip
        for undo, kind in itertools.product(undos, KINDS):
            for r in range(len(SHARED) + 1):
                for on in itertools.combinations(SHARED, r):
                    flags: dict[str, object] = {**kind, **{f: True for f in on}}
                    flags.update({k: v for k, v in undo.items() if v is not None})
                    self.build(**flags)  # must not raise

    def test_the_menu_is_kept_when_a_row_is_free(self) -> None:
        card = self.build(undo_connection=1, undo_spelling=1, finish_past=1)
        self.assertIn(card.show_all, card.children)

    def test_the_owners_card_builds(self) -> None:
        """#2339 live: a character with every undo and the shared buttons, and cut sections."""
        card = self.build(
            undo_connection=1, undo_spelling=1, finish_past=1, standing=True, led=True,
            book=True, say=True, sheet=True, others=True,
        )  # fmt: skip
        self.assertTrue(card.children)


if __name__ == "__main__":
    unittest.main()
