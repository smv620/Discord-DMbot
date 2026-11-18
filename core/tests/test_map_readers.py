"""One reader of `campaign_maps` (design rule 4, #4344): no file outside the store reads or writes
the table, so the audience rule and `may_use_map` cannot be gone around. The allow-list is the
places that must name it: its migration, the registrations, and the picture store, whose delete
takes a picture's map with it."""

from __future__ import annotations

import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "dmbot"
ALLOWED = {
    "maps/store.py",
    "migrations/0488_map_close_read.py",  # (the sweep's marks, #4349)
    "migrations/0472_campaign_maps.py",
    "migrations/0480_forget_maps.py",  # account deletion: the uploader's maps, the DM's id (#3274)
    "migrations/0534_forget_map_reads.py",  # (the same function, with the deletion rule, #4693)
    "migrations/0536_forget_map_version.py",  # (the same function: the version and re-point, #4711)
    "migrations/0457_map_facts.py",  # (its comment: where `map_id` was left for this table)
    "campaigns/tables.py",
    "lineage.py",
    "pictures/store.py",
    "migrations/0530_map_features.py",  # (its pins' foreign key, and two columns, #4670)
    "migrations/0531_map_pins_web.py",  # (a DM-only web door, read only, #4672)
    "migrations/0543_world_roads_door.py",  # (a DM-only web door, read only, #4677)
    "images.py",  # (names the scale's column in a comment only, #4670)
    "memory/_changes.py",  # (the change log and its Undo, #4346: existence checks, never content)
    # The Maps page's read door (#4347): a definer function that applies the player rule inside
    # the database (its shown-and-usable test is `may_use_map`'s, tested in test_web_maps.py),
    # because the website's role has no right on the table. Words, ids and the open code only.
    "migrations/0487_maps_doors.py",
}


class OneReader(unittest.TestCase):
    def test_only_the_map_store_touches_the_table(self) -> None:
        found = {
            str(p.relative_to(SRC))
            for p in SRC.rglob("*.py")
            if "campaign_maps" in p.read_text(encoding="utf-8")
        }
        self.assertEqual(found - ALLOWED, set())
        self.assertIn("maps/store.py", found)  # (so an empty search cannot pass)


class OneAnswer(unittest.TestCase):
    def test_there_is_one_may_use_map(self) -> None:
        # (#4349, MapBot 6b-2: `maps.store.may_use_map` is THE answer; `map_use` is gone, and a
        # second definition anywhere fails here)
        found = [
            str(p.relative_to(SRC))
            for p in SRC.rglob("*.py")
            for line in p.read_text(encoding="utf-8").splitlines()
            if line.lstrip().startswith("def may_use_map(")
        ]
        self.assertEqual(found, ["maps/store.py"])
