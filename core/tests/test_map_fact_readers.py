"""Who reads `memory_map_facts` (#4256; design `docs/design/mapbot_layers.md` §8.4 and §13.4):
every reader goes through one function, `map_fact_store.facts_for`, which applies the players'
rule. This list is reviewed by hand, as `test_relation_readers.py` does for connections: a new file
that names the table fails here until someone decides what it is and writes why."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"

ALLOWED = {
    "memory/map_fact_store.py": "the one reader (`facts_for`, the players' rule) and the writes",
    "memory/map_facts.py": "only names the table in its docstring: the word lists, pure",
    "memory/_changes.py": "the change log's description of the table (Undo, merges)",
    "memory/backup.py": "the memory backup section: the DM's whole campaign, never a player's view",
    "campaigns/tables.py": "only names the table, on the backup section's list",
    "lineage.py": "only names the table, on the list that carries the build's stamp (#3739)",
    "migrations/0457_map_facts.py": "creates the table",
}
NAMES = re.compile(r"memory_map_facts|\bMAP_FACTS\b")


class OneReader(unittest.TestCase):
    def test_only_the_listed_files_touch_the_table(self) -> None:
        found = {
            str(p.relative_to(SRC))
            for p in SRC.rglob("*.py")
            if NAMES.search(p.read_text(encoding="utf-8"))
        }
        self.assertEqual(
            found - set(ALLOWED),
            set(),
            "a new reader of a place's land, climate or ruler must go through "
            "map_fact_store.facts_for (or be added here with why)",
        )
        self.assertEqual(set(ALLOWED) - found, set(), "a listed file no longer needs to be here")

    def test_the_one_reader_filters_for_players(self) -> None:
        text = (SRC / "memory" / "map_fact_store.py").read_text(encoding="utf-8")
        # (writes read inside their own change with `w.select`; only `facts_for` reads for a viewer)
        viewer_reads = re.findall(r"scope\.select\(\s*MAP_FACTS", text)
        self.assertEqual(len(viewer_reads), 1, "one read for viewers")
        # every part of the players' rule (§8.3, and the safe side on #4256): the behaviour itself
        # is tested against a database in test_map_facts.py; this keeps a refactor from dropping one
        for part in (
            "f.table_knows",  # the DM showed it
            "f.status == mf.CONFIRMED",  # the DM kept it (a value DMbot read is the DM's)
            "f.place_id in known",  # the place is shown to them
            "f.value in known",  # and so is the group that rules it
        ):
            self.assertIn(part, text, part)
