"""Who reads `memory_map_facts` (#4256; design `docs/design/mapbot_layers.md` §8.4 and §13.4):
every reader goes through one function, `map_fact_store.facts_for`, which applies the players'
rule. This list is reviewed by hand, as `test_relation_readers.py` does for connections: a new file
that names the table fails here until someone decides what it is and writes why."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

from dmbot.memory import map_facts as mf

SRC = Path(__file__).resolve().parents[1] / "src" / "dmbot"

ALLOWED = {
    "memory/map_fact_store.py": "the one reader (`facts_for`, the players' rule) and the writes",
    "memory/map_facts.py": "only names the table in its docstring: the word lists, pure",
    "memory/_changes.py": "the change log's description of the table (Undo, merges)",
    "memory/backup.py": "the memory backup section: the DM's whole campaign, never a player's view",
    "campaigns/tables.py": "only names the table, on the backup section's list",
    "lineage.py": "only names the table, on the list that carries the build's stamp (#3739)",
    "migrations/0457_map_facts.py": "creates the table",
    "migrations/0492_culture_population.py": "adds the culture and who-lives-here layers (#4257)",
    "devtools/sample_run/questions.py": "dev1's part 2 (#3545): what the DM did with each read row,"
    " every status, counts only, nothing reaches a player",
    "migrations/0496_named_cultures.py": "adds `culture_id`; typed cultures become named (#4257)",
    "migrations/0500_route_condition.py": "adds the condition layer and a road's `relation_id`",
    "maps/store.py": "`remove_map` applies the map deletion rule (§7.5) through the log",
    "pictures/store.py": "a picture deleted with its map applies the same rule (§7.5, #4259)",
    "migrations/0472_campaign_maps.py": "adds the `map_id` column; nothing reads it (#4344)",
    "migrations/0466_place_world_table_door.py": "confirmed, shown rows; gate and for_players",
    "migrations/0591_web_road_condition.py": "a web door any member may call: a player gets only a "
    "confirmed, shown condition (the rule of `for_players`, again in SQL; #4451)",
    "migrations/0463_place_world_door.py": "a DM-only door: NULL for anyone else (#4287; #4311)",
    "migrations/0590_web_place_culture.py": "adds a culture's name to both place doors (the DM's "
    "and the table's, which still gives only confirmed, shown rows; #4412)",
    "migrations/0530_map_features.py": "a pin's foreign key to a row; nothing reads it (#4670)",
    "migrations/0531_map_pins_web.py": "a DM-only web door, read only: an area pin's words (#4672)",
    "migrations/0481_world_notes_door.py": "World notes, DM-only: NULL for anyone else (#4285)",
    "migrations/0598_web_world_notes_cultures.py": "World notes, still DM-only: names a named "
    "culture on its line (#5062)",
    "migrations/0593_web_world_notes_roads.py": "World notes, still DM-only: a known road's "
    "read condition too (#5003)",
    "migrations/0534_forget_map_reads.py": "account deletion: deletes and unpoints rows (#4693)",
    "migrations/0536_forget_map_version.py": "account deletion: re-points and bumps (#4711)",
    "memory/weather_marks.py": "joins a climate row's id only, to mark a weather note sent (#4748)",
    "migrations/0542_weather_notes_sent.py": "a weather mark's foreign key to a row (#4748)",
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
        # (writes read inside their own change with `w.select`; any other `.select(MAP_FACTS` is a
        # reader: `facts_for`, the one a player is ever given, `conditions_for`, a route's condition
        # with what it ended (#4258), which applies the same rule, and `dm_read_notes`, the DM's
        # World notes (#4285), which refuses anyone else)
        readers = re.findall(
            r"async def (\w+)\((?:(?!\nasync def ).)*?(?<!\bw)\.select\(\s*MAP_FACTS", text, re.S
        )
        self.assertEqual(
            sorted(readers), ["conditions_for", "dm_read_notes", "facts_for"], "the three readers"
        )
        self.assertIn(
            "mf.for_players([f for f, _ in facts], known)",
            text.split("async def conditions_for", 1)[1],
        )
        self.assertIn("if not viewer.dm:", text.split("async def dm_read_notes", 1)[1][:900])
        # the players' rule is written once, in `map_facts.for_players` (#4287: the website's
        # player view calls it too), and the one reader uses it
        self.assertIn("mf.for_players(facts, known)", text)
        text = (SRC / "memory" / "map_facts.py").read_text(encoding="utf-8")
        # every part of the players' rule (§8.3, and the safe side on #4256): the behaviour itself
        # is tested against a database in test_map_facts.py; this keeps a refactor from dropping one
        for part in (
            "f.table_knows",  # the DM showed it
            "f.status == CONFIRMED",  # the DM kept it (a value DMbot read is the DM's)
            "f.place_id in known",  # the place is shown to them
            "f.relation_id in known",  # or, for a road's condition, the road is (#4258)
            "f.value in known",  # and so is the group that rules it
        ):
            self.assertIn(part, text, part)


class ThePlayersRule(unittest.TestCase):
    """`map_facts.for_players`, the one rule Discord and the website share (#4287)."""

    def fact(self, layer: str = "climate", value: str = "cold", **kw: Any) -> mf.Fact:
        base: dict[str, Any] = {
            "id": "f", "place_id": "town", "layer": layer, "value": value, "words": "",
            "how": "", "part": "", "status": mf.CONFIRMED, "source": "dm", "table_knows": True,
        }  # fmt: skip
        return mf.Fact(**{**base, **kw})

    def test_each_part_of_the_rule(self) -> None:
        known = {"town", "guild"}
        shown = self.fact()
        ruler = self.fact(mf.CONTROL, "guild")
        hidden = [
            self.fact(table_knows=False),  # (the DM hasn't shown it)
            self.fact(status=mf.READ),  # (DMbot read it; the DM hasn't kept it)
            self.fact(place_id="far"),  # (a place they don't know)
            self.fact(mf.CONTROL, "cult"),  # (a group they don't know)
        ]
        self.assertEqual(mf.for_players([shown, ruler, *hidden], known), [shown, ruler])
