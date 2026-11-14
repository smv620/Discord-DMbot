"""Migration 0375 (#3650): the "happened to them" note comes off kept connections' details, and the
readers take it off old rows too (a restored backup never runs the migration). Run on a table that
holds rows, with the migration held back until they are written (CLAUDE.md, #704). Invented
names."""

from __future__ import annotations

import importlib
import unittest
import uuid
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.memory import backup
from dmbot.memory.models import CONFIRMED
from dmbot.memory.reputation_words import word_of
from dmbot.memory.store import MemoryStore
from dmbot.memory.voice import split_effect_note
from dmbot.schema import MIGRATIONS
from tests.pg import REQUIRE_DB, TEST_URL

GUILD, DM = 4345, 99
NAME = "0375_relation_effect_note"
NOTE = "Told by the DM as something that happened to Rook, not as Rook's own choice."


class EffectNoteMigration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        if not TEST_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_DATABASE_URL is needed")
            self.skipTest("set DMBOT_TEST_DATABASE_URL to test the migration")
        self.schema = f"t_{uuid.uuid4().hex[:16]}"
        self.db = await Database.open(
            TEST_URL,
            schema=self.schema,
            max_size=2,
            migrations=tuple(m for m in MIGRATIONS if m[0] != NAME),
        )
        self.memory = MemoryStore(self.db)
        self.c = (await CampaignStore(self.db).create(GUILD, "Frostmaiden", DM)).id
        self.rook = await self.add("Rook")

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)

    async def add(self, name: str) -> str:
        written = await self.memory.add_entity(
            GUILD, self.c, type="npc", name=name, source="dm", status=CONFIRMED
        )
        return str(written.value.id)

    async def link(self, detail: str, predicate: str = "knows") -> str:
        """A connection kept before #3628: its detail written as the old confirm path left it. Each
        one to a person of its own, so no two are the same connection."""
        other = await self.add(f"Person {uuid.uuid4().hex[:6]}")
        made = await self.memory.add_relation(
            GUILD, self.c, self.rook, predicate, other, source="dm", confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_relations SET detail = %s WHERE id = %s", (detail, made.value[0].id)
            )
        return str(made.value[0].id)

    async def stored(self, relation_id: str) -> str:
        """The detail as the table holds it (not through a reader that tidies it)."""
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT detail FROM memory_relations WHERE id = %s", (relation_id,)
            )
            row: Any = await cur.fetchone()
        return str(row["detail"])

    async def test_only_the_note_at_the_end_comes_off_and_the_feeling_reads_again(self) -> None:
        feeling = await self.link(f"friendly {NOTE}")
        plain = await self.link("old friend")
        middle = await self.link(f"{NOTE} and then some", predicate="associated_with")
        only = await self.link(NOTE, predicate="associated_with")
        other = await self.link(  # (two different names: not the note)
            "Told by the DM as something that happened to Rook, not as Mira's own choice.",
            predicate="associated_with",
        )
        self.assertIsNone(word_of(await self.stored(feeling)))  # (the bug: no word at all)
        await self.db.migrate(MIGRATIONS)
        self.assertEqual(await self.stored(feeling), "friendly")
        self.assertEqual(word_of(await self.stored(feeling)), "friendly")
        self.assertEqual(await self.stored(plain), "old friend")
        self.assertEqual(await self.stored(middle), f"{NOTE} and then some")
        self.assertEqual(await self.stored(only), "")
        self.assertEqual(
            await self.stored(other),
            "Told by the DM as something that happened to Rook, not as Mira's own choice.",
        )

    async def test_a_second_run_changes_nothing(self) -> None:
        feeling = await self.link(f"friendly {NOTE}")
        middle = await self.link(f"{NOTE} and then some", predicate="associated_with")
        await self.db.migrate(MIGRATIONS)
        async with self.db.guild(GUILD) as conn:
            await conn.execute("DELETE FROM schema_migrations WHERE name = %s", (NAME,))
        await self.db.migrate(MIGRATIONS)
        self.assertEqual(await self.stored(feeling), "friendly")
        self.assertEqual(await self.stored(middle), f"{NOTE} and then some")

    async def test_the_databases_pattern_takes_off_exactly_what_the_reader_does(self) -> None:
        """The migration's SQL and `voice.split_effect_note` must never drift apart."""
        tricky = [
            f"friendly {NOTE}",
            NOTE,
            "wary Told by the DM as something that happened to A, not as B, not as A, not as "
            "B's own choice.",
            "close Told by the DM as something that happened to a.b*c(d), not as a.b*c(d)'s own "
            "choice.",
            "Told by the DM as something that happened to Ro'ok, not as Ro'ok's own choice.",
            f"{NOTE} {NOTE}",
            f"{NOTE} and then some",
            "Told by the DM as something that happened to Rook, not as Mira's own choice.",
            "old friend",
        ]
        pattern = importlib.import_module(f"dmbot.migrations.{NAME}").NOTE
        async with self.db.guild(GUILD) as conn:
            for detail in tricky:
                cur = await conn.execute(
                    "SELECT regexp_replace(%s, %s, '') AS d", (detail, pattern)
                )
                row: Any = await cur.fetchone()
                self.assertEqual(row["d"], split_effect_note(detail)[0], detail)

    async def test_the_store_reads_an_old_row_without_the_note(self) -> None:
        """A row the migration never saw (restored, or put back by an Undo) reads clean."""
        feeling = await self.link(f"friendly {NOTE}")
        got = await self.memory.relation(GUILD, self.c, feeling)
        assert got is not None
        self.assertEqual(got.detail, "friendly")
        self.assertEqual(word_of(got.detail), "friendly")


class ARestoredBackup(unittest.TestCase):
    def test_a_backups_connection_loses_the_note_and_keeps_everything_else(self) -> None:
        rows = [
            {"table": "relation", "id": "r1", "predicate": "knows", "detail": f"wary {NOTE}"},
            {"table": "relation", "id": "r2", "predicate": "knows", "detail": "old friend"},
            {"table": "relation", "id": "r3", "predicate": "knows", "detail": f"{NOTE} later"},
        ]
        got = {r["id"]: r["detail"] for r in backup._with_definition_fields(rows)}
        self.assertEqual(got, {"r1": "wary", "r2": "old friend", "r3": f"{NOTE} later"})
