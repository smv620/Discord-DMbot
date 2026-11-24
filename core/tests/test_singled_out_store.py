"""A kept entry that is a description, not a name (#5173, session names slice 6; migration 0614):
the mark is set where the entry is made, kept across a backup, and goes when the DM gives it a
name or it is joined to a real name. Postgres; invented names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import backup
from dmbot.memory.name_found import Found, NameFound
from dmbot.transcript.models import line_ref
from tests.test_memory_store import GUILD_A, MemoryTest

DM_SOURCE = "dm"


class TheMark(MemoryTest):
    async def entity(self, entity_id: str) -> Any:
        return next(e for e in await self.memory.entities(GUILD_A, self.c) if e.id == entity_id)

    async def test_a_singled_out_suggestion_is_marked_and_a_name_is_not(self) -> None:
        mine = await self.add(
            "Gemstone mine", type="place", source="scan", described="gemstone mine"
        )
        vorna = await self.add("Vorna", source="scan")
        self.assertEqual((await self.entity(mine)).described, "gemstone mine")
        self.assertIsNone((await self.entity(vorna)).described)

    async def test_the_words_are_kept_as_the_story_said_them(self) -> None:
        ferry = await self.add(
            "Easthaven ferry", type="place", source="scan", described="Easthaven  ferry"
        )
        self.assertEqual((await self.entity(ferry)).described, "Easthaven ferry")

    async def test_the_mark_is_never_longer_than_a_name(self) -> None:
        import importlib

        from dmbot.memory.models import NAME_MAX

        migration = importlib.import_module("dmbot.migrations.0614_singled_out")

        mine = await self.add("Gemstone mine", type="place", source="scan", described="m" * 120)
        self.assertEqual(len((await self.entity(mine)).described), NAME_MAX)
        # (the column's CHECK holds the same length, so a hand-made row can't pass it either)
        self.assertIn(f"length(described) BETWEEN 1 AND {NAME_MAX}", migration.SQL)

    async def test_how_it_was_found_takes_singled_out(self) -> None:
        mine = await self.add(
            "Gemstone mine", type="place", source="scan", described="gemstone mine"
        )
        found = NameFound(self.db, clock=lambda: 1_700_000_000.0)
        ref = line_ref("s1", 1000, 7)
        await found.keep(GUILD_A, self.c, mine, "singled_out", ref, times=4)
        got = await found.of(GUILD_A, self.c, [mine])
        self.assertEqual(got[mine], Found("singled_out", ref, times=4))

    async def test_giving_it_a_name_clears_the_mark(self) -> None:
        mine = await self.add(
            "Gemstone mine", type="place", source="scan", described="gemstone mine"
        )
        await self.memory.rename_entity(GUILD_A, self.c, mine, "Glitterdeep", source=DM_SOURCE)
        renamed = await self.entity(mine)
        self.assertEqual((renamed.name, renamed.described), ("Glitterdeep", None))

    async def test_joined_to_a_real_name_the_name_is_kept(self) -> None:
        mine = await self.add(
            "Gemstone mine", type="place", described="gemstone mine", status="confirmed"
        )
        named = await self.add("Glitterdeep", type="place", status="confirmed")
        await self.memory.merge(GUILD_A, self.c, mine, named, source=DM_SOURCE, dm_said_same=True)
        live = [e for e in await self.memory.entities(GUILD_A, self.c) if e.status != "merged"]
        kept = next(e for e in live if e.type == "place")
        self.assertEqual((kept.name, kept.described), ("Glitterdeep", None))

    async def test_renamed_the_storys_words_stay_as_another_name(self) -> None:
        mine = await self.add(
            "Gemstone mine", type="place", source="scan", described="gemstone mine"
        )
        await self.memory.rename_entity(GUILD_A, self.c, mine, "Glitterdeep", source=DM_SOURCE)
        keys = {a.key for a in await self.memory.aliases(GUILD_A, self.c, entity_id=mine)}
        self.assertIn("gemstone mine", keys)  # (so the reader never offers it again as new)

    async def test_a_star_on_another_name_clears_the_mark(self) -> None:
        mine = await self.add(
            "Gemstone mine", type="place", described="gemstone mine", status="confirmed"
        )
        await self.memory.add_alias(
            GUILD_A,
            self.c,
            mine,
            "Glitterdeep",
            kind="nickname",
            source=DM_SOURCE,
            status="confirmed",
        )
        other = next(
            a
            for a in await self.memory.aliases(GUILD_A, self.c, entity_id=mine)
            if a.text == "Glitterdeep"
        )
        await self.memory.set_main_name(GUILD_A, self.c, mine, other.id, source=DM_SOURCE)
        kept = await self.entity(mine)
        self.assertEqual((kept.name, kept.described), ("Glitterdeep", None))

    async def test_joined_the_other_way_round_the_name_is_kept_too(self) -> None:
        named = await self.add("Glitterdeep", type="place", status="confirmed")
        mine = await self.add(
            "Gemstone mine", type="place", described="gemstone mine", status="confirmed"
        )
        await self.memory.merge(GUILD_A, self.c, named, mine, source=DM_SOURCE, dm_said_same=True)
        live = [e for e in await self.memory.entities(GUILD_A, self.c) if e.status != "merged"]
        kept = next(e for e in live if e.type == "place")
        self.assertEqual((kept.name, kept.described), ("Glitterdeep", None))


class TheBackup(unittest.TestCase):
    def test_the_mark_is_a_checked_column(self) -> None:
        self.assertTrue(backup._valid("described", "gemstone mine"))
        self.assertTrue(backup._valid("described", None))
        self.assertFalse(backup._valid("described", True))
        self.assertFalse(backup._valid("described", ""))
        self.assertFalse(backup._valid("described", "x" * 101))

    def test_a_backup_from_before_has_no_descriptions(self) -> None:
        row = {"table": "entity", "id": "e1", "name": "Vorna"}
        (out,) = [r for r in backup._with_definition_fields([row]) if r.get("table") == "entity"]
        self.assertIsNone(out["described"])


if __name__ == "__main__":
    unittest.main()
