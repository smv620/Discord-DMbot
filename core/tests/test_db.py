"""The database layer: migrations, and row-level security keeping servers apart."""

import asyncio
import uuid

from psycopg import errors as pg_errors

from dmbot.db import Database, DatabaseError, drop_schema
from dmbot.schema import ISOLATED_TABLES, MIGRATIONS, UNSCOPED_TABLES
from tests.pg import SUPERUSER_URL, TEST_URL, DatabaseTest

GUILD_A, GUILD_B = 111, 222


class RowLevelSecurity(DatabaseTest):
    async def add_consent(self, guild: int, user: int) -> None:
        async with self.db.guild(guild) as conn:
            await conn.execute(
                "INSERT INTO consent (guild_id, user_id, granted_at) VALUES (%s, %s, 0)",
                (guild, user),
            )

    async def test_a_server_sees_only_its_own_rows_even_without_a_filter(self) -> None:
        await self.add_consent(GUILD_A, 1)
        await self.add_consent(GUILD_B, 2)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT guild_id, user_id FROM consent")  # no WHERE
            rows = await cur.fetchall()
        self.assertEqual([(r["guild_id"], r["user_id"]) for r in rows], [(GUILD_A, 1)])

    async def test_no_server_set_sees_nothing(self) -> None:
        await self.add_consent(GUILD_A, 1)
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM consent")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_cannot_write_rows_for_another_server(self) -> None:
        with self.assertRaises(pg_errors.InsufficientPrivilege):
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute(
                    "INSERT INTO consent (guild_id, user_id, granted_at) VALUES (%s, 1, 0)",
                    (GUILD_B,),
                )

    async def test_cannot_move_rows_to_another_server(self) -> None:
        await self.add_consent(GUILD_A, 1)
        with self.assertRaises(pg_errors.InsufficientPrivilege):
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute("UPDATE consent SET guild_id = %s", (GUILD_B,))

    async def test_server_setting_does_not_leak_to_the_next_transaction(self) -> None:
        await self.add_consent(GUILD_A, 1)
        for _ in range(3):  # pooled connections are reused
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute("SELECT 1")
            async with self.db.unscoped() as conn:
                cur = await conn.execute("SELECT count(*) AS n FROM consent")
                row = await cur.fetchone()
                assert row is not None
                self.assertEqual(row["n"], 0)

    async def test_every_table_is_isolated_or_explicitly_unscoped(self) -> None:
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class"
                " WHERE relnamespace = current_schema()::regnamespace AND relkind = 'r'"
            )
            tables = {r["relname"]: r for r in await cur.fetchall()}
        self.assertEqual(set(tables), set(ISOLATED_TABLES) | set(UNSCOPED_TABLES))
        for name in ISOLATED_TABLES:
            self.assertTrue(tables[name]["relrowsecurity"], name)
            self.assertTrue(tables[name]["relforcerowsecurity"], name)


class Migrations(DatabaseTest):
    async def test_runs_once(self) -> None:
        self.assertEqual(await self.db.migrate(), [])
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT name FROM schema_migrations ORDER BY name")
            names = [r["name"] for r in await cur.fetchall()]
        self.assertEqual(names, [name for name, _ in MIGRATIONS])

    async def test_failed_migration_leaves_no_trace(self) -> None:
        bad = (*MIGRATIONS, ("9999_bad", "CREATE TABLE t (x INT); CREATE TABLE t (x INT)"))
        with self.assertRaises(pg_errors.DuplicateTable):
            await self.db.migrate(bad)
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT to_regclass('t') AS t")
            row = await cur.fetchone()
            assert row is not None
            self.assertIsNone(row["t"])
            cur = await conn.execute(
                "SELECT count(*) AS n FROM schema_migrations WHERE name = '9999_bad'"
            )
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual(row["n"], 0)


class StartupChecks(DatabaseTest):
    async def test_several_processes_starting_at_once(self) -> None:
        schema = f"t_{uuid.uuid4().hex[:16]}"
        try:
            dbs = await asyncio.gather(
                *(Database.open(TEST_URL, schema=schema, max_size=2) for _ in range(4))
            )
            for db in dbs:
                await db.close()
        finally:
            await drop_schema(TEST_URL, schema)

    async def test_refuses_a_superuser(self) -> None:
        if not SUPERUSER_URL:
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to test this")
        with self.assertRaisesRegex(DatabaseError, "not a superuser"):
            await Database.open(SUPERUSER_URL, schema=f"t_{uuid.uuid4().hex[:16]}")

    async def test_bad_url_gives_a_readable_error(self) -> None:
        with self.assertRaisesRegex(DatabaseError, "DATABASE_URL"):
            await Database.open("postgresql://nobody@127.0.0.1:1/none?connect_timeout=1")
