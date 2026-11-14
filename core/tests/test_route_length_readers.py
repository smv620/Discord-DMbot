"""Every reader of a route's stated length keeps DMbot's read from players (#4255; Supervisor's
ruling): a length with `length_from = 'read'` is the DM's to check, so a player sees only the DM's
length or one the DM kept with ✅ Length is right. This pure test lists every file that names the
length columns, with how it handles a read, so a new reader (the website's door, an answer that adds
lengths up) has to say so. It can't see a reader that passes a whole Entity or Relation on without
naming a column (a serialiser): such a reader must drop a read length for players itself, as
`name_card._rows` does. No database."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"

ALLOWED = {
    "memory/models.py": "the fields on Entity and Relation",
    "memory/store.py": "reads and writes the columns; the shown-to-players choice is the caller's",
    "memory/_changes.py": "the change log's description of the columns (Undo, merges)",
    "memory/states.py": "a place that stops being one loses its length; shows nothing",
    "memory/backup.py": "the whole campaign, DMbot's reads included, so a restore keeps them",
    "memory/backstory_keep.py": "writes DMbot's read from a kept route's quote; shows nothing",
    "migrations/0486_route_length.py": "adds the columns",
    "ui/name_card.py": "a player's card drops a read (`_rows`, and the route place's line)",
    "ui/place_size.py": "the route place's card line; its buttons are the DM's, checked again",
    "ui/connection_edit.py": "the DM's edit page only (`found.secrets`)",
}
NAMES = re.compile(r"\blength_(?:words|from|amount|unit|approx)\b|\broute_length\b")


def _found() -> set[str]:
    return {
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if NAMES.search(p.read_text(encoding="utf-8"))
    }


class EveryReaderKeepsDmbotsReadFromPlayers(unittest.TestCase):
    def test_every_file_naming_the_length_says_how_it_handles_a_read(self) -> None:
        unlisted = _found() - set(ALLOWED)
        self.assertEqual(unlisted, set(), "say how this file treats length_from 'read' (#4255)")

    def test_a_listed_file_still_names_the_length(self) -> None:
        self.assertEqual(set(ALLOWED) - _found(), set())


if __name__ == "__main__":
    unittest.main()
