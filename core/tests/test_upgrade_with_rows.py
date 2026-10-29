"""A database holding a row in every table is upgraded from the live version to the head (#1846).

Every other migration test starts from an empty database, but a deploy runs the new migrations on
one with rows. The failures that costs are the quiet ones: a `NOT NULL` column added without a
default, a `CHECK` that existing rows break, a backfill that matches nothing because row-level
security hides the rows from a migration. So this builds the schema as it is live, puts one row in
every table that exists then (the list is read from the catalog, so a table can't be missed),
applies the rest of `MIGRATIONS` the way the bot does at start-up, and checks that every row is
still there and that the stores read the new columns.

Needs `DMBOT_TEST_SUPERUSER_URL` (CI sets it): rows are written and counted as the superuser, past
the row-level security that would hide them from the migration's own role. Without it the test is
skipped, like the other database tests that need one.

**What it does not prove:** only NOT NULL columns without a default are filled (a nullable or
defaulted column keeps its default), and each CHECK is met at its easy edge, so a migration that
narrows a CHECK on an optional column is not exercised by these rows. Rows are written as the
superuser, so row-level security hides nothing from the planting. The before/after comparison
catches an upgrade that changes or doubles a value it should not; it cannot see a backfill that
silently matches nothing. That is caught only where `reads_through_the_stores` reads the result
through the app's own role (nothing today: 0130's `initiative_style`, the last one, is live), so a
new row-changing migration needs its own check there.
When `LIVE` moves forward, drop the store checks for migrations that are then live.

**Keep `LIVE` current:** move it forward in the PR that records a deploy (or in dev1's handover),
to the last migration in `docs/testing-status.log`, "LIVE NOW". Only made-up values are used: the
repository is public.
"""

from __future__ import annotations

import asyncio
import itertools
import re
import uuid
from typing import Any

import psycopg
from psycopg import sql

from dmbot.db import Database, drop_schema
from dmbot.schema import MIGRATIONS
from tests.pg import REQUIRE_DB, SUPERUSER_URL, TEST_URL, DatabaseTest

# The last migration that is live. Deploy 86976110 (2026-10-18 01:40 UTC) ended at 0130: its
# migrations are 0128_npcs_channel, 0129_initiative_style and 0130_initiative_style_backfill, per
# `docs/testing-status.log`, "LIVE NOW". Before it, deploy 174c35ec ended at 0127_rules_channel.
LIVE = "0130_initiative_style_backfill"

GUILD, OTHER_GUILD = (
    1,
    111_000_000_000_000_002,
)  # the filler plants guild 1; the second campaign is OTHER_GUILD

# Values for columns the generic filler gets wrong (a CHECK that wants a particular word, a
# format), by table. SQL text, not Python values. A table that still fails says which to add.
SAMPLES: dict[str, dict[str, str]] = {
    # An account that moved may not sign up again under its old id: keep 1 free for web_users.
    "account_moves": {"old_user_id": "900"},
    "recap_audio_strays": {"subscription_id": "'sub_a'"},
    "entitlements": {
        "plan": "'guild'",
        "status": "'active'",
        "period_start": "1",
        "period_end": "2",
    },
    "homebrew_entries": {"source_kind": "'typed'", "text": "'x'"},
    "move_code_asks": {"source": "'" + "a" * 64 + "'"},
    "plan_spend": {"month": "'2026-10'"},
    "character_sheets": {"url": "'https://www.dndbeyond.com/characters/1'"},
    "memory_relations": {"status": "'proposed'"},
    "memory_flags": {"status": "'open'", "kind": "'wrong_subject'"},
    "memory_corrections": {"action": "'keep'"},
    "memory_entities": {"status": "'confirmed'"},
    "plan_nodes": {"kind": "'chapter'"},
    "plan_divergences": {"status": "'open'"},
    "rule_set_codes": {"code": "'AAAAAAAAAA'"},
    "waiting_name_questions": {"option_ids": "'{a,b}'"},
}

# More than one row where the upgrade does something to the rows: the second campaign has the
# retired side-initiative switch on, which migration 0130 folds into `initiative_style`.
EXTRA_ROWS: dict[str, list[dict[str, str]]] = {
    # A second row for the tables whose children point at two different rows of them (a link from
    # one to another can't point from a row to itself).
    "memory_entities": [{"id": "'%s'" % ("b" * 32), "name": "'y'"}],
    "plan_nodes": [{"id": "'%s'" % ("b" * 32), "name_key": "'y'"}],
    "campaigns": [{"id": "'camp-two'", "guild_id": str(OTHER_GUILD), "side_initiative": "true"}],
}

TEXT_SAMPLE = "'x'"
VALUES = {
    "text": TEXT_SAMPLE,
    "character varying": TEXT_SAMPLE,
    "bigint": "1",
    "integer": "1",
    "smallint": "1",
    "boolean": "false",
    "jsonb": "'{}'::jsonb",
    "json": "'{}'::json",
    "bytea": "'\\x00'::bytea",
    "timestamp with time zone": "now()",
    "timestamp without time zone": "now()",
    "double precision": "1",
    "numeric": "1",
    "real": "1",
    "uuid": "gen_random_uuid()",
    "ARRAY": "'{}'",
}

COLUMNS = """
SELECT c.table_name, c.column_name, c.data_type, c.is_nullable = 'YES' AS nullable
  FROM information_schema.columns c
  JOIN information_schema.tables t
    ON t.table_schema = c.table_schema AND t.table_name = c.table_name
 WHERE c.table_schema = current_schema() AND t.table_type = 'BASE TABLE'
   AND c.table_name <> 'schema_migrations'
   AND c.column_default IS NULL AND c.is_generated = 'NEVER' AND c.is_identity = 'NO'
 ORDER BY c.table_name, c.ordinal_position
"""

TABLES = """
SELECT table_name FROM information_schema.tables
 WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'
   AND table_name <> 'schema_migrations' ORDER BY table_name
"""

FOREIGN_KEYS = """
SELECT k.conrelid::regclass::text AS child, k.confrelid::regclass::text AS parent,
       array(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY u(n, i)
             JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.n
             ORDER BY u.i) AS cols,
       array(SELECT a.attname FROM unnest(k.confkey) WITH ORDINALITY u(n, i)
             JOIN pg_attribute a ON a.attrelid = k.confrelid AND a.attnum = u.n
             ORDER BY u.i) AS refs
  FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace
 WHERE k.contype = 'f' AND n.nspname = current_schema()
 ORDER BY k.conrelid::regclass::text, k.conname
"""


def raw(text: str) -> sql.SQL:
    """A piece of SQL built from this file's own constants (never from outside input)."""
    return sql.SQL(text)


def short(name: str) -> str:
    """A table's name as the catalog prints it, without the schema in front."""
    return name.rsplit(".", 1)[-1].strip('"')


def connect(schema: str) -> psycopg.Connection[Any]:
    conn = psycopg.connect(SUPERUSER_URL, autocommit=True)
    conn.execute(sql.SQL("SET search_path = {}").format(sql.Identifier(schema)))
    return conn


def table_order(tables: list[str], keys: list[tuple[str, str]]) -> list[str]:
    """Parents before children; a table that references itself, or a loop, goes in as it comes
    (those columns are nullable, or the insert says which sample row to add)."""
    waiting = {t: {p for c, p in keys if c == t and p != t and p in tables} for t in tables}
    done: list[str] = []
    while waiting:
        ready = sorted(t for t, parents in waiting.items() if parents <= set(done))
        if not ready:
            ready = sorted(waiting)[:1]
        for t in ready:
            done.append(t)
            del waiting[t]
    return done


def candidates(kind: str, definition: str) -> list[str]:
    """Values worth trying for a column a CHECK refuses 'x' (or 1) for: the words and numbers the
    constraint itself names, plus the shapes ids and hashes take. SQL text."""
    words = re.findall(r"'([^']*)'", definition)
    numbers = [int(n) for n in re.findall(r"(?<![\w.])\d+(?![\w.])", definition)]
    if kind in ("text", "character varying"):
        sized = ["a" * n for n in sorted({*numbers, 1, 8, 32, 64}) if 0 < n <= 200]
        found = [w for w in words if w and "\\" not in w and "^" not in w and "[" not in w]
        return [sql.Literal(w).as_string() for w in [*found, "a" * 32, "a" * 64, "x", *sized]]
    if kind in ("bigint", "integer", "smallint"):
        return [str(n) for n in dict.fromkeys([1, 0, 2, *numbers, *(n + 1 for n in numbers)])]
    if kind == "boolean":
        return ["false", "true"]
    if kind in ("jsonb", "json"):
        return ["'{}'::" + kind, "'[]'::" + kind, "'[\"a\"]'::" + kind]
    if kind == "ARRAY":
        return ["'{}'", "'{a}'", "'{1}'"]
    return [VALUES[kind]]


def insert_row(
    conn: psycopg.Connection[Any],
    table: str,
    values: dict[str, str],
    kinds: dict[tuple[str, str], str],
) -> None:
    """Insert one row. If a CHECK refuses the plain values, find the columns it names and try the
    values it hints at, a few at a time, until one set is accepted; the order of the tries is
    fixed, so the row is the same every run. Raises when nothing fits: add it to SAMPLES."""
    last: psycopg.Error | None = None
    for _ in range(6):  # a table can have several CHECKs; each round fixes one
        try:
            statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table),
                sql.SQL(", ").join(map(sql.Identifier, values)),
                sql.SQL(", ").join(raw(v) for v in values.values()),
            )
            if not values:
                statement = sql.SQL("INSERT INTO {} DEFAULT VALUES").format(sql.Identifier(table))
            conn.execute(statement)
            return
        except psycopg.errors.CheckViolation as exc:
            last = exc
            name = exc.diag.constraint_name
            found = conn.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = %s AND conrelid = %s::regclass",
                (name, table),
            ).fetchone()
            if found is None:
                break
            definition = found[0]
            named = [c for c in values if re.search(rf"\b{re.escape(c)}\b", definition)]
            fixed = False
            tries = [candidates(kinds.get((table, c), "text"), definition) for c in named]
            for combo in itertools.islice(itertools.product(*tries), 400):
                trial = {**values, **dict(zip(named, combo, strict=True))}
                try:
                    with conn.transaction():
                        conn.execute(
                            sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                                sql.Identifier(table),
                                sql.SQL(", ").join(map(sql.Identifier, trial)),
                                sql.SQL(", ").join(raw(v) for v in trial.values()),
                            )
                        )
                    return
                except psycopg.errors.CheckViolation as again:
                    if again.diag.constraint_name != name:  # this one passes now; the next round
                        values = trial
                        fixed = True
                        break
                except psycopg.Error:
                    continue
            if not fixed:
                break
        except psycopg.Error as exc:
            last = exc
            break
    raise AssertionError(f"add a sample row for {table} (SAMPLES): {last}")


def fill(schema: str) -> dict[str, int]:
    """One row in every table (two in `EXTRA_ROWS`'s tables); returns the count per table."""
    with connect(schema) as conn:
        tables = [r[0] for r in conn.execute(TABLES)]
        columns: dict[str, list[tuple[str, str, bool]]] = {t: [] for t in tables}
        kinds: dict[tuple[str, str], str] = {}
        for table, column, kind, nullable in conn.execute(COLUMNS):
            columns[table].append((column, kind, nullable))
            kinds[table, column] = kind
        fks = [(short(c), short(p), cols, refs) for c, p, cols, refs in conn.execute(FOREIGN_KEYS)]
        counts: dict[str, int] = {}
        failed: list[str] = []
        for table in table_order(tables, [(c, p) for c, p, _, _ in fks]):
            wanted = [(c, k) for c, k, nullable in columns[table] if not nullable]
            rows = [{}, *EXTRA_ROWS.get(table, [])]
            for extra in rows:
                values: dict[str, str] = {}
                for column, kind in wanted:
                    if kind not in VALUES:
                        raise AssertionError(f"add a sample row for {table}: no value for {kind}")
                    values[column] = VALUES[kind]
                seen: dict[str, int] = {}
                for child, parent, cols, refs in fks:
                    if child != table or parent == table:
                        continue
                    # Parent rows in the order they were planted (ctid), the k-th link to a parent
                    # taking its k-th row, so a link from one row to another is not to itself.
                    nth = seen[parent] = seen.get(parent, -1) + 1
                    found = (
                        conn.execute(
                            sql.SQL("SELECT {} FROM {} ORDER BY ctid LIMIT 1 OFFSET {}").format(
                                sql.SQL(", ").join(map(sql.Identifier, refs)),
                                sql.Identifier(parent),
                                sql.Literal(nth),
                            )
                        ).fetchone()
                        or conn.execute(
                            sql.SQL("SELECT {} FROM {} LIMIT 1").format(
                                sql.SQL(", ").join(map(sql.Identifier, refs)),
                                sql.Identifier(parent),
                            )
                        ).fetchone()
                    )
                    if found is not None:
                        for column, value in zip(cols, found, strict=True):
                            if column in values:  # a nullable link stays empty: less to satisfy
                                values[column] = sql.Literal(value).as_string(conn)
                values.update(SAMPLES.get(table, {}))
                values.update(extra)
                try:
                    insert_row(conn, table, values, kinds)
                except AssertionError as exc:
                    failed.append(str(exc).splitlines()[0][:230])
            counts[table] = len(rows)
        if failed:
            raise AssertionError("\n".join(failed))
        return counts


def count_rows(schema: str) -> dict[str, int]:
    with connect(schema) as conn:
        counts: dict[str, int] = {}
        for (table,) in conn.execute(TABLES).fetchall():
            row = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
            found = row.fetchone()
            assert found is not None
            counts[table] = int(found[0])
        return counts


def snapshot(schema: str) -> dict[str, list[dict[str, Any]]]:
    """Every row of every table as JSON, in a fixed order, to see what an upgrade changed."""
    with connect(schema) as conn:
        out: dict[str, list[dict[str, Any]]] = {}
        for (table,) in conn.execute(TABLES).fetchall():
            rows = conn.execute(
                sql.SQL("SELECT to_jsonb(t) FROM {} t ORDER BY to_jsonb(t)::text").format(
                    sql.Identifier(table)
                )
            ).fetchall()
            out[table] = [r[0] for r in rows]
        return out


# Columns an upgrade is meant to change in rows that were already there: table → column names.
# None today. A backfill that rewrites an old column goes here, with why.
EXPECTED_CHANGES: dict[str, set[str]] = {}


def changed_rows(
    before: dict[str, list[dict[str, Any]]], after: dict[str, list[dict[str, Any]]]
) -> list[str]:
    """Old columns whose values differ after the upgrade (a new column is not an old one)."""
    out = []
    for table, rows in before.items():
        for old, new in zip(rows, after.get(table, []), strict=False):
            for column, value in old.items():
                if column in EXPECTED_CHANGES.get(table, set()):
                    continue
                if new.get(column) != value:
                    out.append(f"{table}.{column}: {value!r} -> {new.get(column)!r}")
    return out


class UpgradeWithRows(DatabaseTest):
    async def asyncSetUp(self) -> None:
        if not SUPERUSER_URL:
            if REQUIRE_DB:  # CI must not skip this one quietly
                self.fail("DMBOT_TEST_SUPERUSER_URL is not set but DMBOT_REQUIRE_DB_TESTS=1")
            self.skipTest("set DMBOT_TEST_SUPERUSER_URL to run the upgrade test")
        await super().asyncSetUp()

    async def test_rows_in_every_table_survive_the_upgrade_to_the_head(self) -> None:
        names = [n for n, _ in MIGRATIONS]
        self.assertIn(LIVE, names, "LIVE must be a migration in MIGRATIONS")
        live = MIGRATIONS[: names.index(LIVE) + 1]
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)
        schema = f"t_{uuid.uuid4().hex[:16]}"
        self.schema = schema
        try:
            self.db = await Database.open(TEST_URL, schema=schema, migrations=live)
            await asyncio.to_thread(fill, schema)
            planted = await asyncio.to_thread(count_rows, schema)  # triggers add rows too
            self.assertGreater(len(planted), 50)  # the catalog gave a real list, not nothing
            self.assertEqual([t for t, n in planted.items() if n == 0], [], "a table has no row")
            before = await asyncio.to_thread(snapshot, schema)
            applied = await self.db.migrate()
            self.assertEqual(applied, names[names.index(LIVE) + 1 :])
            after = await asyncio.to_thread(count_rows, schema)
            lost = {t: (n, after.get(t)) for t, n in planted.items() if after.get(t) != n}
            self.assertEqual(lost, {}, "rows planted before the upgrade, then lost or doubled")
            self.assertEqual(
                changed_rows(before, await asyncio.to_thread(snapshot, schema)),
                [],
                "an upgrade changed a value that was already there (EXPECTED_CHANGES)",
            )
            # With LIVE at the head (right after a deploy) there is nothing to upgrade, and the
            # checks above still prove the planted rows and the sample lists are sound. Once a
            # migration lands after LIVE, `reads_through_the_stores` is where it is read back.
            if applied:
                await self.reads_through_the_stores(schema)
        finally:
            await self.db.close()
            await drop_schema(TEST_URL, schema)

    async def test_the_sample_lists_name_real_tables_and_columns(self) -> None:
        names = [n for n, _ in MIGRATIONS]
        live = MIGRATIONS[: names.index(LIVE) + 1]
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)
        schema = f"t_{uuid.uuid4().hex[:16]}"
        self.schema = schema
        try:
            self.db = await Database.open(TEST_URL, schema=schema, migrations=live)

            def columns() -> set[tuple[str, str]]:
                with connect(schema) as conn:
                    rows = conn.execute(
                        "SELECT table_name, column_name FROM information_schema.columns"
                        " WHERE table_schema = current_schema()"
                    )
                    return {(t, c) for t, c in rows}

            have = await asyncio.to_thread(columns)
            wanted = {(t, c) for t, cols in SAMPLES.items() for c in cols}
            wanted |= {(t, c) for t, rows in EXTRA_ROWS.items() for row in rows for c in row}
            self.assertEqual(
                sorted(wanted - have), [], "SAMPLES / EXTRA_ROWS name a column that isn't there"
            )
            self.assertEqual(
                sorted(t for t in EXPECTED_CHANGES if t not in {t for t, _ in have}), []
            )
        finally:
            await self.db.close()
            await drop_schema(TEST_URL, schema)

    async def reads_through_the_stores(self, schema: str) -> None:
        """What the migrations after LIVE added, read the way the bot reads it.

        Nothing today: 0128-0130, whose read-backs lived here, are live (deploy 86976110). A
        migration that changes rows, or adds a column the bot reads, adds its check here in the
        PR that adds the migration: read the planted rows through the store (`CampaignStore` and
        the like, on `self.db`), as the bot would, and assert the result. It is called only when
        there are migrations to apply."""
