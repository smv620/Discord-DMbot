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
    "migrations/0472_campaign_maps.py",
    "migrations/0480_forget_maps.py",  # account deletion: the uploader's maps, the DM's id (#3274)
    "migrations/0457_map_facts.py",  # (its comment: where `map_id` was left for this table)
    "campaigns/tables.py",
    "lineage.py",
    "pictures/store.py",
    "memory/_changes.py",  # (the change log and its Undo, #4346: existence checks, never content)
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
