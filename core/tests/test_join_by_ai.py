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

    async def test_a_hidden_confirmed_entry_and_a_limited_one_on_either_side(self) -> None:
        keep = await self.add("Ned Keep", "npc")
        await self.sql("UPDATE memory_entities SET hidden_from_players = TRUE WHERE id = %s", keep)
        gone = await self.waiting_name("The Mender One")
        self.assertEqual(await self.refusal(keep, gone), AI_JOIN_REFUSED)
        for side in ("keep", "gone"):
            keep = await self.add(f"Ned {side}", "npc")
            gone = await self.waiting_name(f"The Mender {side}")
            await self.sql(
                "UPDATE memory_entities SET audience_limited = TRUE WHERE id = %s",
                keep if side == "keep" else gone,
            )
            self.assertEqual(await self.refusal(keep, gone), AI_JOIN_REFUSED)

    async def test_a_waiting_name_hidden_from_players_joins_and_the_entry_stays_shown(self) -> None:
        keep = await self.add("Old Tam", "npc")
        gone = await self.waiting_name("The Ferry Lady")
        await self.sql("UPDATE memory_entities SET hidden_from_players = TRUE WHERE id = %s", gone)
        await self.join(keep, gone)  # (names read from a session start hidden: the normal case)
        self.assertFalse((await self.row(keep))["hidden_from_players"])

    async def test_party_unique_items_roles_faked_evidence_and_dependants(self) -> None:
        tam = await self.add("Old Tam", "npc")
        for column, value in (
            ("party", "TRUE"),
            ("unique_item", "TRUE"),
            ("manipulated_by", f"'{tam}'"),
        ):
            gone = await self.waiting_name(f"The {column} one")
            await self.sql(f"UPDATE memory_entities SET {column} = {value} WHERE id = %s", gone)
            self.assertEqual(await self.refusal(tam, gone), AI_JOIN_REFUSED, column)
        odd = await self.waiting_name("The Roled One")
        await self.sql("UPDATE memory_entities SET role = 'npc' WHERE id = %s", odd)
        await self.sql("UPDATE memory_entities SET role = NULL WHERE id = %s", tam)
        self.assertEqual(await self.refusal(tam, odd), AI_JOIN_REFUSED)
        anchored = await self.waiting_name("The Anchored One")
        await self.memory.add_unnamed(
            self.g, self.one.id, what="a patron at the bar", anchor_id=anchored
        )
        self.assertEqual(await self.refusal(tam, anchored), AI_JOIN_REFUSED)

    async def test_a_link_held_back_from_the_players_and_one_that_is_not(self) -> None:
        tam = await self.add("Old Tam", "npc")
        gone = await self.waiting_name("The Ferry Lady")
        await self.link("knows", gone, self.keep, "gone-keep")
        await self.sql(
            "UPDATE memory_relations SET status = 'proposed', unrevealed = TRUE WHERE id = %s",
            self.links["gone-keep"],
        )
        self.assertEqual(await self.refusal(tam, gone), AI_JOIN_REFUSED)
        await self.sql(
            "UPDATE memory_relations SET unrevealed = FALSE WHERE id = %s", self.links["gone-keep"]
        )
        done = await self.join(tam, gone)  # (a waiting link comes along, still waiting)
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT subject_id, status FROM memory_relations WHERE id = %s",
                (self.links["gone-keep"],),
            )
            moved = await cur.fetchone()
        self.assertEqual((moved["subject_id"], moved["status"]), (tam, "proposed"))
        await self.memory.undo(self.g, self.one.id, done.batch)  # (and Undo puts it all back)
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT subject_id FROM memory_relations WHERE id = %s", (self.links["gone-keep"],)
            )
            self.assertEqual((await cur.fetchone())["subject_id"], gone)

    async def test_a_rejected_or_merged_entry_is_not_joined(self) -> None:
        tam = await self.add("Old Tam", "npc")
        gone = await self.waiting_name("The Ferry Lady")
        await self.sql("UPDATE memory_entities SET status = 'rejected' WHERE id = %s", gone)
        with self.assertRaises(MemoryRuleError):
            await self.join(tam, gone)

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
