"""How a pronoun was settled, kept on the relationship it became (#2790): carried from a waiting
connection, none on a plain one, undo, merge, backup and restore, the DM's card only, and no door
reading it. Invented names."""

from __future__ import annotations

import re
from typing import Any

from dmbot import schema
from dmbot.campaigns import CampaignError
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory import backstory_keep as keep
from dmbot.memory.backstory_links import BackstoryLinks, NewLink
from dmbot.memory.models import CONFIRMED
from tests.test_memory_store import GUILD_A, GUILD_B, MemoryTest

DM = 7
SAID = "who-is-it: accepted 95%"


class OnTheRelationship(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.links = BackstoryLinks(self.db)
        self.mara = await self.add("Mara", status=CONFIRMED)
        self.ken = await self.add("Ken", status=CONFIRMED)

    async def kept(self, decided: str) -> Any:
        await self.links.add(
            GUILD_A, self.c, [NewLink(self.mara, "knows", self.ken, "q", decided=decided)]
        )
        waiting = await self.links.waiting(GUILD_A, self.c)
        done = await keep.keep_all(self.memory, self.links, GUILD_A, self.c, waiting)
        self.assertEqual(len(done.kept), 1)
        (rel,) = [
            r
            for r in await self.memory.relations(GUILD_A, self.c, include_secret=True)
            if r.predicate == "knows"
        ]
        return rel, done

    async def test_a_connection_a_pronoun_check_settled_carries_the_note(self) -> None:
        rel, _ = await self.kept(SAID)
        self.assertEqual(rel.decided, SAID)

    async def test_a_plain_connection_has_none(self) -> None:
        rel, _ = await self.kept("")
        self.assertIsNone(rel.decided)

    async def test_undo_takes_it_away_and_redo_brings_it_back(self) -> None:
        _, done = await self.kept(SAID)
        batch = done.kept[0][1]
        assert batch is not None
        undone = await self.memory.undo(GUILD_A, self.c, batch)
        self.assertEqual(
            [r for r in await self.memory.relations(GUILD_A, self.c, include_secret=True)], []
        )
        assert undone.batch is not None
        await self.memory.undo(GUILD_A, self.c, undone.batch)
        (back,) = await self.memory.relations(GUILD_A, self.c, include_secret=True)
        self.assertEqual(back.decided, SAID)

    async def test_a_merge_keeps_it(self) -> None:
        await self.kept(SAID)
        twin = await self.add("Mara Vell", status=CONFIRMED)
        await self.memory.merge(GUILD_A, self.c, self.mara, twin, source="dm", dm_said_same=True)
        (rel,) = [
            r
            for r in await self.memory.relations(GUILD_A, self.c, include_secret=True)
            if r.predicate == "knows"
        ]
        self.assertEqual(rel.decided, SAID)

    async def test_a_backup_round_trip_and_an_old_file(self) -> None:
        await self.kept(SAID)
        data = await self.campaigns.export(GUILD_A, self.c)
        raw = encode_backup(data)
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        original, copied = await self.snapshot(), await self.snapshot(GUILD_B, restored.id)
        for table in ("memory_mentions",):
            original.pop(table), copied.pop(table)
        self.assertEqual(copied, original)
        self.assertEqual(
            [r["decided"] for r in copied["memory_relations"] if r["predicate"] == "knows"], [SAID]
        )
        for row in data["sections"]["memory"]:  # a file from before the column
            if row.get("table") == "relation":
                row.pop("decided", None)
        old = await self.campaigns.import_backup(GUILD_B, decode_backup(encode_backup(data)), DM)
        self.assertEqual(
            [
                r["decided"]
                for r in (await self.snapshot(GUILD_B, old.id))["memory_relations"]
                if r["predicate"] == "knows"
            ],
            [None],
        )

    async def test_a_damaged_value_in_a_file_is_refused(self) -> None:
        await self.kept(SAID)
        for bad in ("x" * 61, 5, ["a"]):
            data = await self.campaigns.export(GUILD_A, self.c)
            for row in data["sections"]["memory"]:
                if row.get("table") == "relation":
                    row["decided"] = bad
            with self.subTest(bad=str(bad)[:5]), self.assertRaises(CampaignError):
                await self.campaigns.import_backup(GUILD_B, decode_backup(encode_backup(data)), DM)


class NoDoorReadsIt(MemoryTest):
    def test_no_door_search_or_player_view_selects_it(self) -> None:
        pattern = re.compile(r"\b[a-z]{1,3}\.decided\b")
        for name, sql_text in schema.MIGRATIONS:
            with self.subTest(migration=name):
                self.assertIsNone(pattern.search(sql_text))
