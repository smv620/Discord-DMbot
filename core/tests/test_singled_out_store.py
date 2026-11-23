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
        mine = await self.add("Gemstone mine", type="place", source="scan", described=True)
        vorna = await self.add("Vorna", source="scan")
        self.assertTrue((await self.entity(mine)).described)
        self.assertFalse((await self.entity(vorna)).described)

    async def test_how_it_was_found_takes_singled_out(self) -> None:
        mine = await self.add("Gemstone mine", type="place", source="scan", described=True)
        found = NameFound(self.db, clock=lambda: 1_700_000_000.0)
        ref = line_ref("s1", 1000, 7)
        await found.keep(GUILD_A, self.c, mine, "singled_out", ref, times=4)
        got = await found.of(GUILD_A, self.c, [mine])
        self.assertEqual(got[mine], Found("singled_out", ref, times=4))

    async def test_giving_it_a_name_clears_the_mark(self) -> None:
        mine = await self.add("Gemstone mine", type="place", source="scan", described=True)
        await self.memory.rename_entity(GUILD_A, self.c, mine, "Glitterdeep", source=DM_SOURCE)
        renamed = await self.entity(mine)
        self.assertEqual((renamed.name, renamed.described), ("Glitterdeep", False))

    async def test_joined_to_a_real_name_the_name_is_kept(self) -> None:
        mine = await self.add("Gemstone mine", type="place", described=True, status="confirmed")
        named = await self.add("Glitterdeep", type="place", status="confirmed")
        await self.memory.merge(GUILD_A, self.c, mine, named, source=DM_SOURCE, dm_said_same=True)
        live = [e for e in await self.memory.entities(GUILD_A, self.c) if e.status != "merged"]
        kept = next(e for e in live if e.type == "place")
        self.assertEqual((kept.name, kept.described), ("Glitterdeep", False))

    async def test_renamed_the_storys_words_stay_as_another_name(self) -> None:
        mine = await self.add("Gemstone mine", type="place", source="scan", described=True)
        await self.memory.rename_entity(GUILD_A, self.c, mine, "Glitterdeep", source=DM_SOURCE)
        keys = {a.key for a in await self.memory.aliases(GUILD_A, self.c, entity_id=mine)}
        self.assertIn("gemstone mine", keys)  # (so the reader never offers it again as new)

    async def test_a_star_on_another_name_clears_the_mark(self) -> None:
        mine = await self.add("Gemstone mine", type="place", described=True, status="confirmed")
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
        self.assertEqual((kept.name, kept.described), ("Glitterdeep", False))

    async def test_joined_the_other_way_round_the_name_is_kept_too(self) -> None:
        named = await self.add("Glitterdeep", type="place", status="confirmed")
        mine = await self.add("Gemstone mine", type="place", described=True, status="confirmed")
        await self.memory.merge(GUILD_A, self.c, named, mine, source=DM_SOURCE, dm_said_same=True)
        live = [e for e in await self.memory.entities(GUILD_A, self.c) if e.status != "merged"]
        kept = next(e for e in live if e.type == "place")
        self.assertEqual((kept.name, kept.described), ("Glitterdeep", False))


class TheBackup(unittest.TestCase):
    def test_the_mark_is_a_checked_column(self) -> None:
        self.assertTrue(backup._valid("described", True))
        self.assertFalse(backup._valid("described", "yes"))

    def test_a_backup_from_before_has_no_descriptions(self) -> None:
        row = {"table": "entity", "id": "e1", "name": "Vorna"}
        (out,) = [r for r in backup._with_definition_fields([row]) if r.get("table") == "entity"]
        self.assertIs(out["described"], False)


if __name__ == "__main__":
    unittest.main()
