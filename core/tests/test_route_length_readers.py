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
    "devtools/graph_key_check.py": "scores a backup against the sample key; shows players nothing",
    "memory/models.py": "the fields on Entity and Relation",
    "memory/store.py": "reads and writes the columns; the shown-to-players choice is the caller's",
    "memory/_changes.py": "the change log's description of the columns (Undo, merges)",
    "memory/states.py": "a place that stops being one loses its length; shows nothing",
    "memory/backup.py": "the whole campaign, DMbot's reads included, so a restore keeps them",
    "memory/backstory_keep.py": "writes DMbot's read from a kept route's quote; shows nothing",
    "memory/session_chain.py": "finds a journey's length words in the DM's lines; shows nothing",
    "migrations/0486_route_length.py": "adds the columns",
    "ui/name_card.py": "a player's card drops a read (`_rows`, and the route place's line)",
    "ui/place_size.py": "the route place's card line; its buttons are the DM's, checked again",
    "ui/connection_edit.py": "the DM's edit page only (`found.secrets`)",
    "maps/disagree.py": "only names `route_length` in its docstring: a pure check, shows nothing",
    "maps/said_phrases.py": "parses words the DM just said with `route_length.parse`; reads no "
    "stored length, shows nothing (#4670 4c-2)",
    "maps/store.py": "a stated length beats a measured one (4b), and a kept road's length is "
    "checked against the DM's maps for the DM alone (#4670 4c); a route's legs drop a read "
    "length for players (`_road_route`, #4260 10d)",
    "maps/legs.py": "adds up the lengths it is given; which lengths a player may have is the "
    "caller's (`store._road_route` drops a read one for players, #4260 10d)",
    "maps/legs_words.py": "says the lengths it is given; shows players only what `_road_route` "
    "gave a player's answer (#4260 10d)",
    "migrations/0543_world_roads_door.py": "a DM-only web door (NULL for anyone else): the roads "
    "whose read length a kept map disagrees with, for World notes' Roads group (#4677); a read "
    "length is shown to the DM alone and a length the DM kept is never listed",
    "migrations/0589_web_route_length.py": "a web door any member may call: it leaves out a length "
    "DMbot only read for anyone who is not a DM of the campaign, so players see one only once the "
    "DM set or kept it (#4392)",
    "web/route_lengths.py": "attaches the door's lengths to a graph page: nothing DMbot only read "
    "reaches a player (the door's rule), and the `read` flag is the DM's view only (#4392)",
    "memory/world_notes.py": "World notes, the DM's list only: lists DMbot's read of a length a "
    "map disagrees with, and ✅ makes it the DM's (#4670 4c)",
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
