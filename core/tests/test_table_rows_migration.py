"""Migration 0316 (#2995): a player character that joined after things were shown gets the rows it
lacks. Run on tables that hold rows, with the migration held back until they are written
(CLAUDE.md, #704). Invented names throughout."""

from __future__ import annotations

import unittest
import uuid
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.memory.gate import ENTRY, Viewer
from dmbot.memory.models import CONFIRMED
from dmbot.memory.store import MemoryStore
from dmbot.schema import MIGRATIONS
from tests.pg import REQUIRE_DB, TEST_URL

GUILD, DM = 4344, 99
NAME = "0316_table_rows"


class TableRowsMigration(unittest.IsolatedAsyncioTestCase):
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

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)

    async def add(self, name: str, **kw: Any) -> str:
        kw.setdefault("type", "npc")
        kw.setdefault("status", CONFIRMED)
        written = await self.memory.add_entity(GUILD, self.c, name=name, source="dm", **kw)
        return str(written.value.id)

    async def pc(self, name: str, player: int) -> str:
        return await self.add(name, type="character", role="player_character", played_by=player)

    async def rows(self) -> int:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM memory_knowers")
            row = await cur.fetchone()
        assert row is not None
        return int(row["n"])

    async def forget_for(self, entity_id: str) -> None:
        """A campaign before this fix: the character joined and was given nothing."""
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "DELETE FROM memory_knowers WHERE perspective_id IN"
                " (SELECT id FROM memory_perspectives WHERE entity_id = %s)",
                (entity_id,),
            )

    async def joined(self, entity_id: str, seconds_from_now: int) -> None:
        """When the character joined, kept apart from the rows made in the same second."""
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE memory_entities SET created_at = extract(epoch FROM now())::bigint + %s"
                " WHERE id = %s",
                (seconds_from_now, entity_id),
            )

    async def seen_by(self, character: str) -> set[str]:
        got = await self.memory.visible_to(GUILD, self.c, Viewer.character(character))
        return set(got.entries)

    async def test_a_late_player_character_gets_what_the_others_know(self) -> None:
        await self.pc("Tavin", 1)
        await self.pc("Mara", 2)
        pub = await self.add("The Pub")
        place = await self.add("Greywater", type="place")
        partial = await self.add("Rook", hidden_from_players=True)
        await self.memory.set_statement_knowers(
            GUILD, self.c, ENTRY, partial, [await self.pc("Orin", 4)], source="dm"
        )
        late = await self.pc("Kesh", 5)
        await self.joined(late, 1000)
        await self.forget_for(late)
        before = await self.rows()
        await self.db.migrate(MIGRATIONS)
        self.assertGreater(await self.rows(), before)
        seen = await self.seen_by(late)
        self.assertLessEqual({pub, place}, seen)
        self.assertNotIn(partial, seen)  # a "some characters" list is not the table's

    async def test_it_adds_only_and_a_second_run_adds_nothing(self) -> None:
        await self.pc("Tavin", 1)
        await self.pc("Mara", 2)
        await self.add("The Pub")
        late = await self.pc("Kesh", 3)
        await self.joined(late, 1000)
        await self.forget_for(late)
        await self.db.migrate(MIGRATIONS)
        after = await self.rows()
        async with self.db.guild(GUILD) as conn:
            await conn.execute("DELETE FROM schema_migrations WHERE name = %s", (NAME,))
        await self.db.migrate(MIGRATIONS)
        self.assertEqual(await self.rows(), after)

    async def test_a_sleeping_row_is_not_woken(self) -> None:
        await self.pc("Tavin", 1)
        await self.pc("Mara", 2)
        pub = await self.add("The Pub")
        late = await self.pc("Kesh", 3)
        await self.joined(late, 1000)
        await self.forget_for(late)
        async with self.db.guild(GUILD) as conn:  # the pub is asleep for the late character
            await conn.execute(
                "INSERT INTO memory_knowers (guild_id, campaign_id, id, perspective_id,"
                " about_entity_id, created_at, confidence, hidden_by)"
                " SELECT %s, %s, md5('sleeper'), p.id, %s, 1, 1.0, ARRAY['x']"
                " FROM memory_perspectives p WHERE p.entity_id = %s",
                (GUILD, self.c, pub, late),
            )
        await self.db.migrate(MIGRATIONS)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n, count(*) FILTER (WHERE cardinality(hidden_by) = 0) AS awake"
                " FROM memory_knowers WHERE about_entity_id = %s AND perspective_id IN"
                " (SELECT id FROM memory_perspectives WHERE entity_id = %s)",
                (pub, late),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["n"], row["awake"]), (1, 0))  # still the one sleeping row

    async def test_the_only_player_character_gets_what_the_flags_show(self) -> None:
        pub = await self.add("The Pub")
        hidden = await self.add("Rook", hidden_from_players=True)
        only = await self.pc("Tavin", 1)
        await self.forget_for(only)
        await self.db.migrate(MIGRATIONS)
        seen = await self.seen_by(only)
        self.assertIn(pub, seen)
        self.assertNotIn(hidden, seen)

    async def test_a_clue_one_player_character_was_given_is_not_copied_to_the_other(self) -> None:
        tavin = await self.pc("Tavin", 1)
        mara = await self.pc("Mara", 2)
        await self.joined(tavin, -2000)
        await self.joined(mara, -1000)  # both were at the table before the clues were told
        clue_a = await self.add("A private clue", hidden_from_players=True)
        clue_b = await self.add("Another private clue", hidden_from_players=True)
        await self.memory.set_statement_knowers(GUILD, self.c, ENTRY, clue_a, [tavin], source="dm")
        await self.memory.set_statement_knowers(GUILD, self.c, ENTRY, clue_b, [mara], source="dm")
        await self.db.migrate(MIGRATIONS)
        self.assertNotIn(clue_a, await self.seen_by(mara))
        self.assertNotIn(clue_b, await self.seen_by(tavin))
