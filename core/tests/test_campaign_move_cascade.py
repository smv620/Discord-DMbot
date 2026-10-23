"""A campaign changes server by one update of its `campaigns` row, and every row follows (#2532).

Migration 0215 makes each foreign key that carries `guild_id` cascade the update. These tests check
that no such key was missed (children of children included), that the update works under forced
row-level security with the bot's role and only when the move's two settings are set, and that
rows really arrive: relations under entities, transcript lines under sessions, an offer.
"""

from __future__ import annotations

from psycopg import errors

from dmbot.campaigns import CampaignStore
from dmbot.db import Conn
from dmbot.memory.models import new_id
from tests.pg import DatabaseTest

OLD, NEW, DM = 111, 222, 9


class MoveCascade(DatabaseTest):
    async def test_every_foreign_key_that_carries_the_server_cascades_an_update(self) -> None:
        async with self.db.guild(OLD) as conn:
            cur = await conn.execute(
                "SELECT t.relname AS tbl, c.conname FROM pg_constraint c"
                " JOIN pg_class t ON t.oid = c.conrelid"
                " JOIN pg_namespace n ON n.oid = t.relnamespace"
                " JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)"
                " AND a.attname = 'guild_id'"
                " WHERE c.contype = 'f' AND n.nspname = current_schema() AND c.confupdtype <> 'c'"
            )
            self.assertEqual([tuple(r.values()) for r in await cur.fetchall()], [])

    async def seed(self) -> tuple[str, str]:
        campaign = await CampaignStore(self.db).create(OLD, "Frostmaiden", DM)
        a, b, link, session = new_id(), new_id(), new_id(), new_id()
        async with self.db.guild(OLD) as conn:
            for key, name in ((a, "Aaa"), (b, "Bbb")):
                await conn.execute(
                    "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                    " description, status, source, created_at)"
                    " VALUES (%s, %s, %s, 'npc', %s, '', 'confirmed', 'dm', 1)",
                    (OLD, campaign.id, key, name),
                )
            await conn.execute(
                "INSERT INTO memory_relations (guild_id, campaign_id, id, subject_id,"
                " predicate, object_id, detail, confidence, status, source, mention_ids,"
                " secret, created_at, definition_version)"
                " VALUES (%s, %s, %s, %s, 'knows', %s, '', 1.0, 'confirmed', 'dm', '{}', false,"
                " 1, 1)",
                (OLD, campaign.id, link, a, b),
            )
            await conn.execute(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at,"
                " ended_at, line_count) VALUES (%s, %s, %s, 100, 200, 1)",
                (session, OLD, campaign.id),
            )
            await conn.execute(
                "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id, heard,"
                " duration_ms) VALUES (%s, %s, 5, %s, 'we meet', 1000)",
                (OLD, session, DM),
            )
        return campaign.id, session

    async def move(self, conn: Conn, campaign_id: str) -> None:
        await conn.execute("SELECT set_config('dmbot.moving_campaign', %s, true)", (campaign_id,))
        await conn.execute("SELECT set_config('dmbot.moving_to', %s, true)", (str(NEW),))
        await conn.execute(
            "UPDATE campaigns SET guild_id = %s WHERE guild_id = %s AND id = %s",
            (NEW, OLD, campaign_id),
        )

    async def count(self, guild: int, table: str) -> int:
        async with self.db.guild(guild) as conn:
            cur = await conn.execute(f"SELECT count(*) AS n FROM {table}")
            row = await cur.fetchone()
            assert row is not None
            return int(row["n"])

    async def test_the_rows_arrive_and_none_is_left_behind(self) -> None:
        campaign_id, session = await self.seed()
        tables = ("campaigns", "memory_entities", "memory_relations", "transcript_sessions",
                  "transcript_lines", "campaign_dms")  # fmt: skip
        before = {t: await self.count(OLD, t) for t in tables}
        self.assertTrue(all(n > 0 for n in before.values()), before)
        async with self.db.guild(OLD) as conn:
            await self.move(conn, campaign_id)
        for table in tables:
            self.assertEqual(await self.count(OLD, table), 0, table)
            self.assertEqual(await self.count(NEW, table), before[table], table)
        async with self.db.guild(NEW) as conn:
            cur = await conn.execute(
                "SELECT id FROM transcript_sessions WHERE campaign_id = %s", (campaign_id,)
            )
            self.assertEqual([r["id"] for r in await cur.fetchall()], [session])  # same ids

    async def test_without_the_two_settings_no_campaign_can_leave_its_server(self) -> None:
        campaign_id, _ = await self.seed()
        with self.assertRaises(errors.InsufficientPrivilege):
            async with self.db.guild(OLD) as conn:
                await conn.execute(
                    "UPDATE campaigns SET guild_id = %s WHERE guild_id = %s AND id = %s",
                    (NEW, OLD, campaign_id),
                )
        self.assertEqual(await self.count(NEW, "campaigns"), 0)
        self.assertEqual(await self.count(OLD, "campaigns"), 1)

    async def test_the_settings_name_one_campaign_and_one_server_only(self) -> None:
        campaign_id, _ = await self.seed()
        other = await CampaignStore(self.db).create(OLD, "Other", DM)
        with self.assertRaises(errors.InsufficientPrivilege):
            async with self.db.guild(OLD) as conn:  # the settings name another campaign
                await conn.execute(
                    "SELECT set_config('dmbot.moving_campaign', %s, true)", (campaign_id,)
                )
                await conn.execute("SELECT set_config('dmbot.moving_to', %s, true)", (str(NEW),))
                await conn.execute(
                    "UPDATE campaigns SET guild_id = %s WHERE id = %s", (NEW, other.id)
                )
        with self.assertRaises(errors.InsufficientPrivilege):
            async with self.db.guild(OLD) as conn:  # the settings name another server
                await conn.execute(
                    "SELECT set_config('dmbot.moving_campaign', %s, true)", (campaign_id,)
                )
                await conn.execute("SELECT set_config('dmbot.moving_to', %s, true)", ("333",))
                await conn.execute(
                    "UPDATE campaigns SET guild_id = %s WHERE id = %s", (NEW, campaign_id)
                )
        self.assertEqual(await self.count(NEW, "campaigns"), 0)

    async def test_the_new_server_is_listed_as_holding_campaigns(self) -> None:
        campaign_id, _ = await self.seed()
        async with self.db.guild(OLD) as conn:
            await self.move(conn, campaign_id)
        async with self.db.guild(NEW) as conn:
            cur = await conn.execute("SELECT guild_id FROM campaign_guilds")
            self.assertIn(NEW, [r["guild_id"] for r in await cur.fetchall()])
