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
What cannot leak between tests, and why: rows and counters are cleared and checked; the
structure is compared by catalog (see `_STAMP`); a schema built before the website's role
existed is dropped (the role's rights are cluster-wide, so they follow the role, not the
schema). Session settings, advisory locks and LISTEN channels live and die with each test's
own connection pool, which is closed at the end of every test. Objects outside the schema
(extensions, other schemas, roles) are not touched by a test through here, and a test that
makes one is not covered: the exactness test (tests/test_pg_reuse.py) is where to add a check.
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
from psycopg import sql

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
        conn.execute(sql.SQL("SET search_path = {}").format(sql.Identifier(schema)))
        rows = conn.execute(_STRUCTURE, {"s": schema}).fetchall()
    return [str(r[0]).replace(f'"{schema}".', "~.").replace(schema, "~") for r in rows]


# The quick check between tests: for every catalog that describes the schema, how many rows there
# are, the sum of the transactions that wrote them and the newest one. Any DDL (a column, a policy,
# a grant, a trigger switched off and on again) adds or removes a row or leaves a newer transaction
# on one, so a schema is "unchanged" only if nothing in its catalogs was written since it was
# built. (The sum, not only the newest, so a later change cannot hide under an old high-water mark
# after transaction numbers are frozen or wrap.) `pg_class` is the exception: clearing a table
# rewrites its row, and VACUUM and ANALYZE touch it, so it is compared by what the row *says*
# (kind, columns, row-level security, owner, grants), not by when it was written.
# Reading this takes a few milliseconds; printing the whole structure takes a tenth of a second.
_CATALOGS = [
    ("pg_attribute", "a", "JOIN cls ON a.attrelid = cls.oid"),
    ("pg_attrdef", "a", "JOIN cls ON a.adrelid = cls.oid"),
    ("pg_constraint", "a", "JOIN ns ON a.connamespace = ns.oid"),
    ("pg_trigger", "a", "JOIN cls ON a.tgrelid = cls.oid"),
    ("pg_policy", "a", "JOIN cls ON a.polrelid = cls.oid"),
    ("pg_index", "a", "JOIN cls ON a.indrelid = cls.oid"),
    ("pg_proc", "a", "JOIN ns ON a.pronamespace = ns.oid"),
    ("pg_type", "a", "JOIN ns ON a.typnamespace = ns.oid"),
    ("pg_enum", "a", "JOIN pg_type t ON t.oid = a.enumtypid JOIN ns ON t.typnamespace = ns.oid"),
    ("pg_rewrite", "a", "JOIN cls ON a.ev_class = cls.oid"),
    ("pg_sequence", "a", "JOIN cls ON a.seqrelid = cls.oid"),
    ("pg_description", "a", "JOIN cls ON a.objoid = cls.oid"),
    ("pg_statistic_ext", "a", "JOIN ns ON a.stxnamespace = ns.oid"),
    ("pg_inherits", "a", "JOIN cls ON a.inhrelid = cls.oid"),
    ("pg_default_acl", "a", "JOIN ns ON a.defaclnamespace = ns.oid"),
    ("pg_namespace", "a", "JOIN ns ON a.oid = ns.oid"),
]
_STAMP = (
    "WITH ns AS (SELECT oid FROM pg_namespace WHERE nspname = %(s)s),"
    " cls AS (SELECT c.* FROM pg_class c JOIN ns ON c.relnamespace = ns.oid)"
    " SELECT concat_ws('|', (SELECT count(*) || ':' || coalesce(md5(string_agg("
    "relname || relkind::text || relnatts || relhasindex || relrowsecurity || relforcerowsecurity"
    " || relpersistence::text || relhastriggers || relowner || coalesce(relacl::text, '')"
    " || coalesce(reloptions::text, ''), ',' ORDER BY relname)), '') FROM cls)"
    + "".join(
        f", (SELECT count(*) || ':' || coalesce(sum(a.xmin::text::bigint), 0) || ':'"
        f" || coalesce(max(a.xmin::text::bigint), 0) FROM {table} a {join})"
        for table, _, join in _CATALOGS
    )
    + ")"
)


_ADMIN: psycopg.Connection[tuple[object, ...]] | None = None


def _admin() -> psycopg.Connection[tuple[object, ...]]:
    """One superuser connection for this process, kept between tests (a connection is not tied
    to an event loop, and opening one for every test costs more than the check does). Waits
    for a lock only a few seconds, so a test that left a connection open in a transaction cannot
    hang the run: the schema is dropped or left behind instead of reused."""
    global _ADMIN
    if _ADMIN is None or _ADMIN.closed or _ADMIN.broken:
        conn = psycopg.connect(SUPERUSER_URL, autocommit=True)
        # Clearing rows passes row-level security only as a superuser; without one it would
        # quietly leave rows behind.
        row = conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()
        if not row or not row[0]:
            conn.close()
            raise RuntimeError("DMBOT_TEST_SUPERUSER_URL must be a superuser to reuse schemas")
        conn.execute("SET lock_timeout = '5s'")
        conn.execute("SET statement_timeout = '30s'")
        _ADMIN = conn
    return _ADMIN


def stamp(schema: str) -> str:
    """The quick fingerprint of a schema (see `_STAMP`), its list of migrations, and whether the
    website's role has its rights in it. Not "whether the role exists": the role may be made by
    another worker (pytest -n 4) *while* this schema is migrating, after the migration looked and
    found none (it grants only to a role that exists then). Reading the role afterwards would
    call such a schema granted when it is not, and a website test handed it fails with "The
    database isn't set up yet" (Postgres hides a schema the role may not use) (#1357)."""
    conn = _admin()
    row = conn.execute(_STAMP, {"s": schema}).fetchone()
    names = conn.execute(
        sql.SQL("SELECT coalesce(md5(string_agg(name, ',' ORDER BY name)), '') FROM {}.{}").format(
            sql.Identifier(schema), sql.Identifier("schema_migrations")
        )
    ).fetchone()
    return f"{row[0] if row else ''}|{names[0] if names else ''}|{web_granted(schema)}"


_ROLE_MADE = False


def create_role_sql(name: str, password: str) -> str:
    """CREATE ROLE that is safe when another worker makes the same role at the same instant
    (#1438, #1439). Two sessions can both pass a "does it exist" check; Postgres then stops one
    with duplicate_object, or, when both reach the catalog's unique index together, with
    unique_violation (pg_authid_rolname_index). Both mean "someone else made it", which is the
    result wanted, so the block swallows both. Names are fixed by the tests, never user input."""
    if not name.isidentifier() or "'" in password:
        raise ValueError("not a plain role name or password")
    return (
        f"DO $do$ BEGIN CREATE ROLE {name} LOGIN PASSWORD '{password}'; "
        "EXCEPTION WHEN duplicate_object OR unique_violation THEN NULL; END $do$"
    )


def make_web_role() -> None:
    """Make the website's role if it is missing, once per process, before any schema is migrated.
    A migration grants to the role only if it exists at that moment, so with workers making it
    at random (pytest -n 4) a schema built a moment too early had no grants and a website test
    handed it failed with "The database isn't set up yet" (#1357). The role is cluster-wide and
    harmless to tests that never use it; the password is the one tests/test_web_role.py uses."""
    global _ROLE_MADE
    if _ROLE_MADE or not SUPERUSER_URL:
        return
    if not web_role_exists():
        _admin().execute(create_role_sql("dmbot_web", "dmbot_web"))  # (another worker may be first)
    _ROLE_MADE = True


def web_role_exists() -> bool:
    row = _admin().execute("SELECT 1 FROM pg_roles WHERE rolname = 'dmbot_web'").fetchone()
    return row is not None


def fits_role(schema: str) -> bool:
    """Whether a kept schema has what a fresh one would have now: the website's role's rights if
    the role exists, none if it does not."""
    return _BUILT.get(schema, "").endswith(f"|{web_role_exists()}")


def web_granted(schema: str) -> bool:
    """Whether the website's role can use this schema: true only if the role exists and the
    migration that built it saw the role (it grants nothing to a role made afterwards)."""
    row = (
        _admin()
        .execute(
            "SELECT r.oid IS NOT NULL AND has_schema_privilege(r.oid, n.oid, 'USAGE')"
            " FROM pg_namespace n LEFT JOIN pg_roles r ON r.rolname = 'dmbot_web'"
            " WHERE n.nspname = %s",
            (schema,),
        )
        .fetchone()
    )
    return bool(row and row[0])


def clear_rows(schema: str) -> None:
    """Empty every table that has rows (except the list of migrations) and restart every counter,
    as a fresh schema has them; then check nothing is left. The guard triggers (such as the one
    refusing a TRUNCATE of campaigns) are off for this one transaction: they protect production,
    not a test's leftovers."""
    conn = _admin()
    names = [
        str(r[0])
        for r in conn.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = %s AND c.relkind IN ('r', 'p')"
            " AND c.relname <> 'schema_migrations'",
            (schema,),
        ).fetchall()
    ]
    probe = sql.SQL(" UNION ALL ").join(
        [
            sql.SQL("SELECT {} WHERE EXISTS (SELECT 1 FROM {}.{})").format(
                sql.Literal(n), sql.Identifier(schema), sql.Identifier(n)
            )
            for n in names
        ]
    )

    def full() -> list[str]:
        return [str(r[0]) for r in conn.execute(probe).fetchall()] if names else []

    filled = full()
    sequences = [
        str(r[0])
        for r in conn.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = %s AND c.relkind = 'S'",
            (schema,),
        ).fetchall()
    ]
    with conn.transaction():
        if filled:
            conn.execute("SET LOCAL session_replication_role = replica")
            tables = sql.SQL(", ").join(
                sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(n)) for n in filled
            )
            conn.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(tables))
        for name in sequences:  # a counter moves even when its rows were rolled back
            conn.execute(
                sql.SQL("ALTER SEQUENCE {}.{} RESTART").format(
                    sql.Identifier(schema), sql.Identifier(name)
                )
            )
    left = full()
    if left:  # a table nobody expected (hidden by a rule, say): never hand it on dirty
        raise RuntimeError(f"rows left after clearing: {left}")


def _forget(schema: str) -> None:
    _BUILT.pop(schema, None)
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        conn.execute("SET lock_timeout = '10s'")
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


WHY = ""  # why the last schema was not reused (a test failure message needs to say)


def _what_differs(now: str, built: str) -> str:
    """Which catalogs of the fingerprint changed, by name, for a readable failure message."""
    names = ["pg_class", *(table for table, _, _ in _CATALOGS)]
    mine, theirs = now.split("|"), built.split("|")
    parts = [n for n, x, y in zip(names, mine, theirs, strict=False) if x != y]
    tail = [] if mine[len(names) :] == theirs[len(names) :] else ["migrations or web role"]
    return ", ".join([*parts, *tail]) or "nothing visible"


def recycle(schema: str) -> bool:
    """Take a schema back after a test: True when it is unchanged (nothing in its catalogs was
    written since it was built), was cleared, and may go to the next test; False when it was
    dropped because something about it changed or anything at all went wrong."""
    global WHY
    try:
        now = stamp(schema)
        if now == _BUILT.get(schema):
            clear_rows(schema)
            # Restarting a counter rewrites its row in pg_sequence, so the fingerprint moves
            # even though nothing but the counter changed. Take it again now: the check above
            # has already shown the schema was untouched until this moment.
            _BUILT[schema] = stamp(schema)
            _FREE.append(schema)
            return True
        WHY = "changed: " + _what_differs(now, _BUILT.get(schema, ""))
    except Exception as exc:  # anything odd: do not trust it
        WHY = f"{type(exc).__name__}: {str(exc)[:300]}"
    with contextlib.suppress(Exception):  # stuck on a lock: left behind, never reused
        _forget(schema)
    return False


@atexit.register
def _drop_reusable() -> None:
    """When this process ends, the schemas kept for reuse go too."""
    for schema in {*_FREE, *_BUILT}:
        with contextlib.suppress(Exception):
            _forget(schema)
    _FREE.clear()


def sender_pids(*dbs: Database) -> set[int]:
    """The backend pids of the connections these test pools hold. NOTIFY is cluster-wide, and
    every worker of a parallel run shares the one database server: a test that LISTENs on a channel
    hears the same-looking notices (same server id, same first offer number) that other workers'
    tests send from their own schemas. A notice carries its sender's pid, so a test keeps only the
    ones sent by its own pools (read this once the work is done, when its connections are idle)."""
    return {conn.info.backend_pid for db in dbs for conn in db._pool._pool}  # type: ignore[attr-defined]


class DatabaseTest(unittest.IsolatedAsyncioTestCase):
    """Gives each test `self.db`: an empty, fully migrated schema (see the top of this file for
    how it is reused)."""

    db: Database

    async def asyncSetUp(self) -> None:
        if not TEST_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_DATABASE_URL is not set but DMBOT_REQUIRE_DB_TESTS=1")
            self.skipTest("set DMBOT_TEST_DATABASE_URL to run database tests")
        reused = False
        if REUSE:
            await asyncio.to_thread(make_web_role)
            while _FREE and not reused:
                self.schema = _FREE.pop()
                # The website's role may have been made since this schema was: it would have none
                # of the role's rights. Such a schema is dropped, never reused.
                if fits_role(self.schema):
                    reused = True
                else:
                    await asyncio.to_thread(_forget, self.schema)
        if not reused:
            self.schema = f"t_{uuid.uuid4().hex[:16]}"
        try:
            # A reused schema is already migrated and granted: only check it is up to date.
            self.db = await Database.open(
                TEST_URL, schema=self.schema, max_size=4, migrate=not reused
            )
            if REUSE and not reused:  # just built: remember how it looks
                _BUILT[self.schema] = stamp(self.schema)
        except BaseException:
            if REUSE:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(_forget, self.schema)
            raise

    async def asyncTearDown(self) -> None:
        await self.db.close()
        if REUSE and self.schema in _BUILT:
            # In a thread, so the event loop stays free for whatever the test left to finish
            # (a task rolling back a connection): blocking it here could wait for ever.
            await asyncio.to_thread(recycle, self.schema)
        else:
            await drop_schema(TEST_URL, self.schema)
