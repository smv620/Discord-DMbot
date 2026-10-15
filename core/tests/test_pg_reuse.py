"""The test database's schema reuse (#1334, tests/pg.py): a schema that has been used by a test,
cleared and handed to the next one must be *exactly* a fresh migrated schema (structure, row-level
security, policies, functions, grants, counters), and a schema a test changed in any way must never
be handed on. Needs the superuser URL, which CI sets."""

from __future__ import annotations

import unittest
import uuid

import psycopg

from dmbot.db import MIGRATIONS, Database
from tests import pg

needs_superuser = unittest.skipUnless(
    pg.TEST_URL and pg.SUPERUSER_URL, "set DMBOT_TEST_SUPERUSER_URL to run the schema reuse tests"
)


def exists(schema: str) -> bool:
    with psycopg.connect(pg.TEST_URL, autocommit=True) as conn:
        row = conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,)).fetchone()
    return row is not None


def run(sql: str) -> None:
    """DDL as the schema's owner, the way a test that changed the schema would have done it."""
    with psycopg.connect(pg.TEST_URL, autocommit=True) as conn:
        conn.execute(sql)


@needs_superuser
class Reuse(unittest.IsolatedAsyncioTestCase):
    async def build(self) -> str:
        """A schema made the way DatabaseTest makes one, remembered the same way."""
        schema = f"t_{uuid.uuid4().hex[:16]}"
        db = await Database.open(pg.TEST_URL, schema=schema, max_size=2)
        pg._BUILT[schema] = pg.stamp(schema)
        await db.close()
        self.addCleanup(self.discard, schema)
        return schema

    def discard(self, schema: str) -> None:
        if schema in pg._FREE:
            pg._FREE.remove(schema)
        pg._forget(schema)

    async def use(self, schema: str) -> None:
        """What a test does to a reused schema: fills tables, uses counters, and lets go."""
        db = await Database.open(pg.TEST_URL, schema=schema, max_size=2, migrate=False)
        async with db.guild(7) as conn:
            await conn.execute(
                "INSERT INTO consent (guild_id, user_id, granted_at) VALUES (7, 8, 0)"
            )
        async with db.unscoped() as conn:
            await conn.execute("SELECT nextval('feedback_id_seq')")
            await conn.execute("SELECT nextval('feedback_id_seq')")
        await db.close()

    def rows(self, schema: str) -> dict[str, tuple[int, str]]:
        """Every table's number of rows and a fingerprint of their contents, so a seeded table
        (today only the list of migrations) must match row for row, not just in size."""
        with psycopg.connect(pg.SUPERUSER_URL, autocommit=True) as conn:
            tables = [
                str(r[0])
                for r in conn.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = %s", (schema,)
                ).fetchall()
            ]
            found: dict[str, tuple[int, str]] = {}
            for t in tables:
                # `applied_at` is when it was applied: the only column that differs by design.
                row = conn.execute(
                    psycopg.sql.SQL(
                        "SELECT count(*),"
                        " coalesce(md5(string_agg(x::text, ',' ORDER BY x::text)), '')"
                        " FROM {}.{} x"
                    ).format(psycopg.sql.Identifier(schema), psycopg.sql.Identifier(t))
                    if t != "schema_migrations"
                    else psycopg.sql.SQL(
                        "SELECT count(*), coalesce(md5(string_agg(name, ',' ORDER BY name)), '')"
                        " FROM {}.schema_migrations"
                    ).format(psycopg.sql.Identifier(schema))
                ).fetchone()
                assert row is not None
                found[t] = (int(row[0]), str(row[1]))
        return found

    def counter(self, schema: str) -> tuple[int, bool]:
        with psycopg.connect(pg.SUPERUSER_URL, autocommit=True) as conn:
            row = conn.execute(
                psycopg.sql.SQL("SELECT last_value, is_called FROM {}.feedback_id_seq").format(
                    psycopg.sql.Identifier(schema)
                )
            ).fetchone()
        assert row is not None
        return int(row[0]), bool(row[1])

    # ---- exactness -------------------------------------------------------------------------

    async def test_a_used_cleared_schema_is_exactly_a_fresh_one(self) -> None:
        fresh = await self.build()
        used = await self.build()
        for _ in range(3):  # three tests in a row, so a leftover would show
            await self.use(used)
            self.assertTrue(pg.recycle(used))
            pg._FREE.remove(used)
        self.assertEqual(pg.structure(used), pg.structure(fresh))  # every line, names aside
        self.assertEqual(self.rows(used), self.rows(fresh))  # same rows, content included
        self.assertEqual(self.counter(used), self.counter(fresh))  # counters start over

    async def test_the_two_checks_agree_on_what_a_fresh_schema_looks_like(self) -> None:
        fresh = await self.build()
        self.assertEqual(pg.stamp(fresh), pg._BUILT[fresh])
        lines = pg.structure(fresh)
        for kind in (
            "table ",
            "column ",
            "constraint ",
            "index ",
            "policy ",
            "function ",
            "trigger ",
        ):
            self.assertTrue(any(line.startswith(kind) for line in lines), kind)
        self.assertTrue(any("rls=true forced=true" in line for line in lines))

    async def test_a_recycled_schema_is_handed_on_and_one_with_rows_is_cleared(self) -> None:
        schema = await self.build()
        await self.use(schema)
        self.assertGreater(
            sum(n for n, _ in self.rows(schema).values()), len(MIGRATIONS)
        )  # rows left behind
        self.assertTrue(pg.recycle(schema))
        self.assertIn(schema, pg._FREE)
        self.assertEqual(
            sum(n for n, _ in self.rows(schema).values()), len(MIGRATIONS)
        )  # only the migrations

    # ---- any change to a schema keeps it from being handed on ------------------------------

    async def assertDropped(self, schema: str, change: str) -> None:
        run(change.format(s=schema))
        self.assertFalse(pg.recycle(schema), change)
        self.assertNotIn(schema, pg._FREE)
        self.assertFalse(exists(schema))

    async def test_every_kind_of_change_is_noticed(self) -> None:
        changes = [
            'ALTER TABLE "{s}".consent ADD COLUMN extra TEXT',
            'ALTER TABLE "{s}".consent ALTER COLUMN granted_at SET DEFAULT 5',
            'ALTER TABLE "{s}".consent DISABLE ROW LEVEL SECURITY',
            'ALTER TABLE "{s}".consent NO FORCE ROW LEVEL SECURITY',
            'DROP POLICY IF EXISTS {policy} ON "{s}".consent',
            'GRANT SELECT ON "{s}".consent TO PUBLIC',
            'CREATE TABLE "{s}".leftover (id INT)',
            'CREATE INDEX leftover_ix ON "{s}".consent (user_id)',
            'CREATE FUNCTION "{s}".leftover() RETURNS INT LANGUAGE sql AS $$ SELECT 1 $$',
            'ALTER TABLE "{s}".consent ADD CONSTRAINT leftover CHECK (user_id > -5)',
            "INSERT INTO \"{s}\".schema_migrations (name) VALUES ('0999_extra')",
            "DELETE FROM \"{s}\".schema_migrations WHERE name = '0001_initial'",
            'REVOKE ALL ON SCHEMA "{s}" FROM PUBLIC',
        ]
        for change in changes:
            with self.subTest(change):
                schema = await self.build()
                if "{policy}" in change:
                    with psycopg.connect(pg.SUPERUSER_URL, autocommit=True) as conn:
                        policy = conn.execute(
                            "SELECT policyname FROM pg_policies WHERE schemaname = %s"
                            " AND tablename = 'consent' LIMIT 1",
                            (schema,),
                        ).fetchone()
                    assert policy is not None
                    change = change.replace("{policy}", f'"{policy[0]}"')
                await self.assertDropped(schema, change)

    async def test_a_trigger_switched_off_and_on_again_still_counts_as_a_change(self) -> None:
        schema = await self.build()
        with psycopg.connect(pg.SUPERUSER_URL, autocommit=True) as conn:
            row = conn.execute(
                "SELECT t.tgname, c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = %s AND NOT t.tgisinternal LIMIT 1",
                (schema,),
            ).fetchone()
        assert row is not None
        name, table = row
        run(f'ALTER TABLE "{schema}"."{table}" DISABLE TRIGGER "{name}"')
        run(f'ALTER TABLE "{schema}"."{table}" ENABLE TRIGGER "{name}"')
        self.assertFalse(pg.recycle(schema))  # careful over clever: it is looked at again

    async def test_a_schema_that_is_not_one_of_ours_is_never_taken(self) -> None:
        stranger = f"t_{uuid.uuid4().hex[:16]}"
        run(f'CREATE SCHEMA "{stranger}"')
        self.addCleanup(pg._forget, stranger)
        # No record of how it was built: it can only be dropped, never handed on.
        run(f'CREATE TABLE "{stranger}".schema_migrations (name TEXT)')
        self.assertFalse(pg.recycle(stranger))
        self.assertFalse(exists(stranger))


if __name__ == "__main__":
    unittest.main()
