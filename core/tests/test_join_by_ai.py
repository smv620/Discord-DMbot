"""DMbot's own join of a waiting name onto a confirmed entry (#3602, slice 4): only the narrow case
the owner allowed, one change with Undo, and the spelling stays waiting (unseen by players and
unused by the recap and table answers) until the DM confirms it. Real Postgres; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import (
    CONFIRMED,
    DM,
    PROPOSED,
    WHO_SOURCE,
    MemoryRuleError,
)
from dmbot.memory.store import AI_JOIN_REFUSED
from tests.test_web_relationships import Web


class Base(Web):
    async def waiting_name(self, name: str, kind: str = "npc", **kw: Any) -> str:
        made = await self.memory.add_entity(
            self.g, self.one.id, type=kind, name=name, source="scan", **kw
        )
        return str(made.value.id)

    async def join(self, keep: str, gone: str) -> Any:
        return await self.memory.join_by_ai(self.g, self.one.id, keep, gone)

    async def refusal(self, keep: str, gone: str) -> str:
        with self.assertRaises(MemoryRuleError) as caught:
            await self.join(keep, gone)
        return str(caught.exception)

    async def row(self, entity: str) -> Any:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute("SELECT * FROM memory_entities WHERE id = %s", (entity,))
            return await cur.fetchone()

    async def aliases_of(self, entity: str) -> dict[str, str]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT text, status FROM memory_aliases WHERE entity_id = %s", (entity,)
            )
            return {r["text"]: r["status"] for r in await cur.fetchall()}


class WhatItMayDo(Base):
    async def test_a_waiting_name_joins_a_confirmed_one_and_stays_waiting(self) -> None:
        tam = await self.add("Old Tam", "npc")
        gone = await self.waiting_name("The Ferry Lady")
        done = await self.join(tam, gone)
        self.assertEqual(done.value.id, tam)
        self.assertEqual((await self.row(gone))["merged_into"], tam)
        names = await self.aliases_of(tam)
        self.assertEqual(names["The Ferry Lady"], PROPOSED)  # waiting for the DM's word
        self.assertEqual(names["Old Tam"], CONFIRMED)

    async def test_players_and_table_answers_do_not_use_the_waiting_spelling(self) -> None:
        tam = await self.add("Old Tam", "npc")
        gone = await self.waiting_name("The Ferry Lady")
        await self.join(tam, gone)
        lookup = CampaignLookup.build(await self.memory.lookup_data(self.g, self.one.id))
        mine = [e for e in lookup.for_viewer(False).names if e.text == "The Ferry Lady"]
        self.assertTrue(mine)
        self.assertFalse(any(e.confirmed for e in mine))  # never a silent fix, never a recap name

    async def test_the_change_is_one_batch_from_who_and_one_undo_puts_it_back(self) -> None:
        tam = await self.add("Old Tam", "npc")
        gone = await self.waiting_name("The Ferry Lady")
        done = await self.join(tam, gone)
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT DISTINCT source FROM memory_changes WHERE campaign_id = %s AND batch = %s",
                (self.one.id, done.batch),
            )
            self.assertEqual([r["source"] for r in await cur.fetchall()], [WHO_SOURCE])
        await self.memory.undo(self.g, self.one.id, done.batch)
        self.assertIsNone((await self.row(gone))["merged_into"])
        self.assertNotIn("The Ferry Lady", await self.aliases_of(tam))

    async def test_merge_stays_dm_only(self) -> None:
        tam = await self.add("Old Tam", "npc")
        gone = await self.waiting_name("The Ferry Lady")
        with self.assertRaises((MemoryRuleError, ValueError)):
            await self.memory.merge(
                self.g, self.one.id, tam, gone, source=WHO_SOURCE, dm_said_same=True
            )


class WhatItRefuses(Base):
    async def test_a_secret_name_on_either_side(self) -> None:
        gone = await self.waiting_name("The Ferry Lady")
        # (Mira has the secret name "Lady Grey" in this fixture)
        self.assertEqual(await self.refusal(self.mira, gone), AI_JOIN_REFUSED)
        plain = await self.add("Old Tam", "npc")
        other = await self.waiting_name("The Net Mender")
        await self.memory.add_alias(
            self.g, self.one.id, other, "Hidden Hand", kind="short", source="scan", secret=True
        )
        self.assertEqual(await self.refusal(plain, other), AI_JOIN_REFUSED)

    async def test_hidden_or_limited_entries(self) -> None:
        for flag in ("hidden_from_players", "audience_limited"):
            keep = await self.add(f"Ned {flag[:4]}", "npc")
            await self.sql(f"UPDATE memory_entities SET {flag} = TRUE WHERE id = %s", keep)
            gone = await self.waiting_name(f"The Mender {flag[:4]}")
            self.assertEqual(await self.refusal(keep, gone), AI_JOIN_REFUSED)

    async def test_a_player_character_a_person_and_two_kinds(self) -> None:
        gone = await self.waiting_name("The Ferry Lady")
        self.assertEqual(await self.refusal(self.rook, gone), AI_JOIN_REFUSED)
        self.assertEqual(await self.refusal(self.keep, gone), AI_JOIN_REFUSED)  # a place
        player_named = await self.waiting_name("Rook's Cousin", "character")
        self.assertEqual(await self.refusal(self.rook, player_named), AI_JOIN_REFUSED)

    async def test_two_confirmed_entries_and_the_same_entry(self) -> None:
        other = await self.add("Old Tam", "npc")
        self.assertEqual(await self.refusal(self.mira, other), AI_JOIN_REFUSED)
        self.assertEqual(await self.refusal(self.mira, self.mira), AI_JOIN_REFUSED)

    async def test_a_disguise(self) -> None:
        gone = await self.waiting_name("The Ferry Lady")
        masked = await self.add("The Masked Duke", "npc")
        await self.link("disguised_as", masked, self.mira, "mask")
        self.assertEqual(await self.refusal(self.mira, gone), AI_JOIN_REFUSED)

    async def test_a_name_that_is_no_longer_waiting_and_nothing_changed(self) -> None:
        gone = await self.waiting_name("The Ferry Lady")
        await self.memory.confirm_entity(self.g, self.one.id, gone, "npc", source=DM)
        before = await self.aliases_of(self.mira)
        self.assertEqual(await self.refusal(self.mira, gone), AI_JOIN_REFUSED)
        self.assertEqual(await self.aliases_of(self.mira), before)
