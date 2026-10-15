"""Helpers for tests that need a real Postgres.

Set DMBOT_TEST_DATABASE_URL to a database owned by an ordinary (non-superuser) role;
each test gets its own empty, fully migrated schema. Without the variable, these tests are
skipped (CI always sets it). See README, "Database".

Building a schema (73 migrations, about 0.3 s) used to be most of what a database test cost, and
there are thousands (#1334). So a schema is migrated once and then *reused*: when a test ends its
rows are cleared and the schema is checked to be untouched since it was built: nothing in the
catalogs that describe it (tables, columns, constraints, indexes, policies, row-level security
flags, functions, triggers, grants) may have been written. Unchanged: the next test in this
process gets it, empty and as freshly migrated (tests/test_pg_reuse.py proves a reused schema
equals a fresh one). Any change at all (a test that altered the schema): it is dropped and never
reused.
Clearing rows needs `DMBOT_TEST_SUPERUSER_URL` (CI sets it); without it, or with
`DMBOT_TEST_REUSE_SCHEMAS=0`, every test builds its own schema as before. Tests that test
migrations themselves (`migrations=before`, test_db's Migrations) always migrate from zero: they
make their own schemas and never go through here.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import os
import unittest
import uuid

import psycopg

from dmbot.db import Database, drop_schema

TEST_URL = os.environ.get("DMBOT_TEST_DATABASE_URL", "")
# CI sets this so database tests fail, rather than quietly skip, without a database.
REQUIRE_DB = os.environ.get("DMBOT_REQUIRE_DB_TESTS") == "1"
# Optional: a superuser URL, to test that DMbot refuses one.
SUPERUSER_URL = os.environ.get("DMBOT_TEST_SUPERUSER_URL", "")

requires_db = unittest.skipUnless(TEST_URL, "set DMBOT_TEST_DATABASE_URL to run database tests")

# Reuse needs a superuser to clear rows past the row-level security and the guard triggers.
REUSE = bool(SUPERUSER_URL) and os.environ.get("DMBOT_TEST_REUSE_SCHEMAS", "1") != "0"

# This process's schemas that are migrated, empty and unchanged, ready for the next test; and the
# structure each had when it was built. Plain names and text: each test has its own event loop,
# so nothing tied to a loop can live here.
_FREE: list[str] = []
_BUILT: dict[str, str] = {}

# One line for every part of a schema that a test could change. The schema's own name is
# replaced by "~" afterwards, so two schemas with different names compare equal.
_STRUCTURE = """
SELECT line FROM (
  SELECT 'table ' || c.relname || ' ' || c.relkind::text || ' rls=' || c.relrowsecurity
         || ' forced=' || c.relforcerowsecurity || ' owner=' || pg_get_userbyid(c.relowner)
         || ' acl=' || coalesce(c.relacl::text, '') AS line
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %(s)s
  UNION ALL
  SELECT 'column ' || c.relname || '.' || a.attname || ' ' || format_type(a.atttypid, a.atttypmod)
         || ' notnull=' || a.attnotnull
         || ' default=' || coalesce(pg_get_expr(d.adbin, d.adrelid), '')
         || ' identity=' || a.attidentity::text || ' generated=' || a.attgenerated::text
    FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
   WHERE n.nspname = %(s)s AND a.attnum > 0 AND NOT a.attisdropped
  UNION ALL
  SELECT 'constraint ' || conrelid::regclass::text || ' ' || conname || ' '
         || pg_get_constraintdef(k.oid)
    FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace WHERE n.nspname = %(s)s
  UNION ALL
  SELECT 'index ' || indexdef FROM pg_indexes WHERE schemaname = %(s)s
  UNION ALL
  SELECT 'policy ' || tablename || ' ' || policyname || ' ' || permissive || ' ' || cmd || ' '
         || roles::text || ' using=' || coalesce(qual, '') || ' check=' || coalesce(with_check, '')
    FROM pg_policies WHERE schemaname = %(s)s
  UNION ALL
  SELECT 'function ' || p.proname || ' ' || pg_get_functiondef(p.oid) || ' acl='
         || coalesce(p.proacl::text, '')
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = %(s)s
  UNION ALL
  SELECT 'trigger ' || c.relname || ' ' || pg_get_triggerdef(t.oid)
         || ' enabled=' || t.tgenabled::text
    FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %(s)s AND NOT t.tgisinternal
  UNION ALL
  SELECT 'schema acl=' || coalesce(nspacl::text, '') || ' owner=' || pg_get_userbyid(nspowner)
    FROM pg_namespace WHERE nspname = %(s)s
  UNION ALL
  SELECT 'type ' || t.typname || ' ' || t.typtype::text
    FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
   WHERE n.nspname = %(s)s AND t.typtype IN ('e', 'd')
  UNION ALL
  SELECT 'migration ' || name FROM schema_migrations
) s ORDER BY line
"""


def structure(schema: str) -> list[str]:
    """Everything about a schema's structure that a test could change, one line each, sorted,
    with the schema's own name replaced so schemas can be compared. Slow (it prints every
    function and constraint): used by the exactness test, not between tests."""
    with psycopg.connect(SUPERUSER_URL, autocommit=True) as conn:
        conn.execute(psycopg.sql.SQL("SET search_path = {}").format(psycopg.sql.Identifier(schema)))
        rows = conn.execute(_STRUCTURE, {"s": schema}).fetchall()
    return [str(r[0]).replace(f'"{schema}".', "~.").replace(schema, "~") for r in rows]


# The quick check between tests: how many catalog rows describe the schema and the newest
# transaction that touched any of them. Any DDL (a column, a policy, a grant, a trigger switched
# off and on again) either adds or removes a row or leaves a newer transaction on one, so a
# schema is "unchanged" only if nothing in its catalogs was written since it was looked at. Reading
# this takes a few milliseconds; printing the whole structure takes a tenth of a second.
_STAMP = """
WITH ns AS (SELECT oid FROM pg_namespace WHERE nspname = %(s)s),
cls AS (SELECT c.oid, c.xmin FROM pg_class c JOIN ns ON c.relnamespace = ns.oid)
SELECT concat_ws('|',
  (SELECT count(*) || ':' || coalesce(max(xmin::text::bigint), 0) FROM cls),
  (SELECT count(*) || ':' || coalesce(max(a.xmin::text::bigint), 0)
     FROM pg_attribute a JOIN cls ON a.attrelid = cls.oid),
  (SELECT count(*) || ':' || coalesce(max(d.xmin::text::bigint), 0)
     FROM pg_attrdef d JOIN cls ON d.adrelid = cls.oid),
  (SELECT count(*) || ':' || coalesce(max(k.xmin::text::bigint), 0)
     FROM pg_constraint k JOIN ns ON k.connamespace = ns.oid),
  (SELECT count(*) || ':' || coalesce(max(t.xmin::text::bigint), 0)
     FROM pg_trigger t JOIN cls ON t.tgrelid = cls.oid),
  (SELECT count(*) || ':' || coalesce(max(p.xmin::text::bigint), 0)
     FROM pg_policy p JOIN cls ON p.polrelid = cls.oid),
  (SELECT count(*) || ':' || coalesce(max(i.xmin::text::bigint), 0)
     FROM pg_index i JOIN cls ON i.indrelid = cls.oid),
  (SELECT count(*) || ':' || coalesce(max(f.xmin::text::bigint), 0)
     FROM pg_proc f JOIN ns ON f.pronamespace = ns.oid),
  (SELECT count(*) || ':' || coalesce(max(y.xmin::text::bigint), 0)
     FROM pg_type y JOIN ns ON y.typnamespace = ns.oid),
  (SELECT count(*) || ':' || coalesce(max(r.xmin::text::bigint), 0)
     FROM pg_rewrite r JOIN cls ON r.ev_class = cls.oid),
  (SELECT count(*) || ':' || coalesce(max(n.xmin::text::bigint), 0) FROM pg_namespace n
     JOIN ns ON n.oid = ns.oid)
)
"""


def _admin() -> psycopg.Connection[tuple[object, ...]]:
    """One superuser connection for this process, kept between tests (a connection is not tied
    to an event loop, and opening one for every test costs more than the check does)."""
    global _ADMIN
    if _ADMIN is None or _ADMIN.closed or _ADMIN.broken:
        _ADMIN = psycopg.connect(SUPERUSER_URL, autocommit=True)
    return _ADMIN


_ADMIN: psycopg.Connection[tuple[object, ...]] | None = None


def stamp(schema: str) -> str:
    """The quick fingerprint of a schema (see `_STAMP`), plus its list of migrations."""
    conn = _admin()
    row = conn.execute(_STAMP, {"s": schema}).fetchone()
    names = conn.execute(
        psycopg.sql.SQL(
            "SELECT coalesce(md5(string_agg(name, ',' ORDER BY name)), '') FROM {}"
        ).format(psycopg.sql.SQL("{}.schema_migrations").format(psycopg.sql.Identifier(schema)))
    ).fetchone()
    return f"{row[0] if row else ''}|{names[0] if names else ''}"


def clear_rows(schema: str) -> None:
    """Empty every table that has rows (except the list of migrations) and restart every counter,
    as a fresh schema has them. The guard triggers (such as the one refusing a TRUNCATE of
    campaigns) are off for this one transaction: they protect production, not a test's leftovers."""
    conn = _admin()
    names = [
        str(r[0])
        for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s"
            " AND tablename <> 'schema_migrations'",
            (schema,),
        ).fetchall()
    ]
    ident = psycopg.sql.Identifier
    probe = psycopg.sql.SQL(" UNION ALL ").join(
        [
            psycopg.sql.SQL("SELECT {} WHERE EXISTS (SELECT 1 FROM {}.{})").format(
                psycopg.sql.Literal(n), ident(schema), ident(n)
            )
            for n in names
        ]
    )
    full = [str(r[0]) for r in conn.execute(probe).fetchall()] if names else []
    sequences = [
        str(r[0])
        for r in conn.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = %s AND c.relkind = 'S'",
            (schema,),
        ).fetchall()
    ]
    with conn.transaction():
        # A connection a test left open in a transaction would make this wait for ever: give up
        # after a few seconds instead (the schema is then dropped, or left behind, never reused).
        conn.execute("SET LOCAL lock_timeout = '5s'")
        if full:
            conn.execute("SET LOCAL session_replication_role = replica")
            tables = psycopg.sql.SQL(", ").join(
                psycopg.sql.SQL("{}.{}").format(ident(schema), ident(n)) for n in full
            )
            conn.execute(psycopg.sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(tables))
        for name in sequences:  # a counter moves even when its rows were rolled back
            conn.execute(
                psycopg.sql.SQL("ALTER SEQUENCE {}.{} RESTART").format(ident(schema), ident(name))
            )


def _forget(schema: str) -> None:
    _BUILT.pop(schema, None)
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        conn.execute("SET lock_timeout = '10s'")
        conn.execute(
            psycopg.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                psycopg.sql.Identifier(schema)
            )
        )


def recycle(schema: str) -> bool:
    """Take a schema back after a test: True when it is unchanged (nothing in its catalogs was
    written since it was last looked at), was cleared, and may go to the next test; False when
    it was dropped because something about it changed. Clearing rewrites the catalog rows of
    the tables it empties, so the new look is taken after it."""
    try:
        if stamp(schema) == _BUILT.get(schema):
            clear_rows(schema)
            _BUILT[schema] = stamp(schema)
            _FREE.append(schema)
            return True
    except psycopg.Error:
        pass  # anything odd: do not trust it
    with contextlib.suppress(psycopg.Error):  # stuck on a lock: left behind, never reused
        _forget(schema)
    return False


@atexit.register
def _drop_reusable() -> None:
    """When this process ends, the schemas kept for reuse go too."""
    for schema in [*_FREE]:
        with contextlib.suppress(psycopg.Error):
            _forget(schema)
    _FREE.clear()


class DatabaseTest(unittest.IsolatedAsyncioTestCase):
    """Gives each test `self.db`: an empty, fully migrated schema (see the top of this file for
    how it is reused)."""

    db: Database

    async def asyncSetUp(self) -> None:
        if not TEST_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_DATABASE_URL is not set but DMBOT_REQUIRE_DB_TESTS=1")
            self.skipTest("set DMBOT_TEST_DATABASE_URL to run database tests")
        reused = REUSE and bool(_FREE)
        self.schema = _FREE.pop() if reused else f"t_{uuid.uuid4().hex[:16]}"
        # A reused schema is already migrated and granted: only check it is up to date.
        self.db = await Database.open(TEST_URL, schema=self.schema, max_size=4, migrate=not reused)
        if REUSE and not reused:  # just built: remember how it looks
            _BUILT[self.schema] = stamp(self.schema)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        if REUSE and self.schema in _BUILT:
            # In a thread, so the event loop stays free for whatever the test left to finish
            # (a task rolling back a connection): blocking it here could wait for ever.
            await asyncio.to_thread(recycle, self.schema)
        else:
            await drop_schema(TEST_URL, self.schema)
