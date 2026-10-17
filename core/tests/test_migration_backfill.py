"""A migration that changes rows opens row-level security for itself (#1826).

Migrations run unscoped and every server table has forced row-level security, so a plain
`UPDATE` or `DELETE` silently matches nothing, and an `INSERT ... SELECT` silently copies nothing
(#704; #781's 0029 would have deleted every row it meant to fill). CLAUDE.md's rule: wrap the
statement in `CREATE POLICY migrate_backfill ON <table> ...` before and `DROP POLICY
migrate_backfill ON <table>` after, for **every** table the statement writes or reads (through
`FROM` or `JOIN`). This reads the SQL of `schema.MIGRATIONS` as text; it needs no database.

Kept simple and conservative: comments are stripped, statements are split on `;` outside quotes
and `$$` bodies, and a statement that changes rows but whose table it cannot find fails the test
instead of passing. Function bodies (triggers) are not migration-time statements and are skipped;
`DO` blocks run at migration time and are read.
"""

from __future__ import annotations

import re
import unittest

from dmbot import schema

# Tables with row-level security, where a statement without the policy silently sees no rows.
FORCED = (
    set(schema.ISOLATED_TABLES) | set(schema.USER_ISOLATED_TABLES) | set(schema.WRITE_ONLY_TABLES)
)

# (migration, table) pairs that are correct without the wrapper, each with the reason. Empty
# today: every statement is wrapped, or reads only a table the same migration just created (which
# has no rows yet; the check sees that for itself).
NOT_NEEDED: dict[tuple[str, str], str] = {}

DOLLAR = re.compile(r"\$(\w*)\$")
WRITE = re.compile(
    r"^(?:UPDATE\s+(?:ONLY\s+)?(\w+)|DELETE\s+FROM\s+(?:ONLY\s+)?(\w+)|INSERT\s+INTO\s+(\w+))",
    re.I,
)
READ = re.compile(r"\b(?:FROM|JOIN)\s+(?:ONLY\s+)?(\w+)", re.I)
CONTROL = re.compile(r"^\s*(?:BEGIN|THEN|ELSE|END IF|END|LOOP|END LOOP)\b\s*", re.I)


def strip_comments(sql: str) -> str:
    """The SQL without `--` and `/* */` comments (quoted text is left alone)."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i)
            i = n if j < 0 else j + 2
        elif sql[i] == "'":
            j = _end_of_quote(sql, i)
            out.append(sql[i : j + 1])
            i = j + 1
        else:
            out.append(sql[i])
            i += 1
    return "".join(out)


def _end_of_quote(sql: str, start: int) -> int:
    j = start + 1
    while j < len(sql):
        if sql[j] == "'":
            if sql[j + 1 : j + 2] == "'":
                j += 2
                continue
            break
        j += 1
    return j


def statements(sql: str, base: int = 0) -> list[tuple[int, str]]:
    """(offset, text) of each statement. `DO` blocks are read inside; function bodies are not."""
    found: list[tuple[int, str]] = []
    start = i = 0

    def flush(end: int) -> None:
        text = sql[start:end].strip()
        if text:
            found.append((base + start, text))

    while i < len(sql):
        char = sql[i]
        if char == "'":
            i = _end_of_quote(sql, i) + 1
        elif char == "$" and (tag := DOLLAR.match(sql, i)):
            close = sql.find(tag.group(0), tag.end())
            if close < 0:
                raise ValueError("an unclosed $$ block")
            if re.search(r"\bDO\s*$", sql[start:i], re.I):
                for off, text in statements(sql[tag.end() : close], base + tag.end()):
                    found.append((off, CONTROL.sub("", text)))
            i = close + len(tag.group(0))
        elif char == ";":
            flush(i)
            i += 1
            start = i
        else:
            i += 1
    flush(len(sql))
    return found


def problems(name: str, sql: str, forced: set[str] = FORCED) -> list[str]:
    """What is missing in one migration, naming the migration and the table."""
    sql = strip_comments(sql)
    created = {m[1].lower(): m.start() for m in re.finditer(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", sql, re.I)}  # fmt: skip
    opens = [(m.start(), m[1].lower()) for m in re.finditer(
        r"CREATE\s+POLICY\s+migrate_backfill\s+ON\s+(\w+)", sql, re.I)]  # fmt: skip
    drop = r"DROP\s+POLICY\s+(?:IF\s+EXISTS\s+)?migrate_backfill\s+ON\s+(\w+)"
    closes = [(m.start(), m[1].lower()) for m in re.finditer(drop, sql, re.I)]
    out: list[str] = []
    for offset, text in statements(sql):
        if not text:
            continue
        first = text.split(None, 1)[0].upper()
        if first == "MERGE":
            out.append(f"{name}: a MERGE statement, which this check cannot read: {text[:60]!r}")
            continue
        if first == "WITH":  # a CTE: the row-changing statement is inside
            dml = re.search(r"\b(UPDATE|DELETE\s+FROM|INSERT\s+INTO)\s+\w+", text, re.I)
            if dml is None:
                continue
            text = text[dml.start() :]
        match = WRITE.match(text)
        if match is None:
            if first in ("UPDATE", "DELETE", "INSERT"):
                out.append(f"{name}: cannot place the table of {text[:60]!r}")
            continue
        written = (match[1] or match[2] or match[3]).lower()
        if match[3] and not re.search(r"\bSELECT\b", text, re.I):
            continue  # INSERT ... VALUES reads nothing (a blocked insert fails loudly anyway)
        tables = ({written} | {t.lower() for t in READ.findall(text)}) & forced
        for table in sorted(tables):
            if table in created and created[table] < offset:
                continue  # made earlier in this migration: no rows yet
            if (name, table) in NOT_NEEDED:
                continue
            wrapped = any(at < offset and t == table for at, t in opens) and any(
                at > offset and t == table for at, t in closes
            )
            if not wrapped:
                role = "writes" if table == written else "reads"
                out.append(
                    f"{name}: {role} {table} without `CREATE POLICY migrate_backfill ON {table}` "
                    f"before and `DROP POLICY migrate_backfill ON {table}` after: {text[:60]!r}"
                )
    return out


class TodaysMigrationsTest(unittest.TestCase):
    def test_every_row_changing_statement_opens_row_level_security(self) -> None:
        found = [p for name, sql in schema.MIGRATIONS for p in problems(name, sql)]
        self.assertEqual(found, [])

    def test_the_check_sees_the_statements_that_follow_the_rule(self) -> None:
        # (so a parser that finds nothing cannot pass for the right reason)
        _, sql = next(m for m in schema.MIGRATIONS if m[0].startswith("0130"))
        seen = [t for _, t in statements(strip_comments(sql)) if WRITE.match(t)]
        self.assertTrue(seen and all(t.upper().startswith("UPDATE CAMPAIGNS") for t in seen), seen)
        many = sum(
            1
            for _, s in schema.MIGRATIONS
            for _, t in statements(strip_comments(s))
            if WRITE.match(t)
        )
        self.assertGreater(many, 15)

    def test_the_allow_list_has_no_stale_entries(self) -> None:
        names = {name for name, _ in schema.MIGRATIONS}
        for migration, table in NOT_NEEDED:
            self.assertIn(migration, names)
            self.assertIn(table, FORCED)


def wrapped(table: str, body: str, *others: str) -> str:
    tables = (table, *others)
    opened = "".join(
        f"CREATE POLICY migrate_backfill ON {t} USING (true) WITH CHECK (true);\n" for t in tables
    )
    closed = "".join(f"DROP POLICY migrate_backfill ON {t};\n" for t in tables)
    return opened + body + ";\n" + closed


class StandInTest(unittest.TestCase):
    def check(self, sql: str) -> list[str]:
        return problems("9999_stand_in", sql, {"campaigns", "memory_entities"})

    def test_a_bare_update_fails_naming_the_migration_and_the_table(self) -> None:
        (found,) = self.check("UPDATE campaigns SET name = 'x' WHERE id = '1';")
        self.assertIn("9999_stand_in", found)
        self.assertIn("campaigns", found)

    def test_a_wrapped_update_passes(self) -> None:
        self.assertEqual(self.check(wrapped("campaigns", "UPDATE campaigns SET name = 'x'")), [])

    def test_a_joined_table_that_is_not_wrapped_fails(self) -> None:
        sql = wrapped(
            "campaigns",
            "UPDATE campaigns SET name = e.name FROM memory_entities e WHERE e.id = campaigns.id",
        )
        (found,) = self.check(sql)
        self.assertIn("reads memory_entities", found)

    def test_both_tables_wrapped_passes(self) -> None:
        sql = wrapped(
            "campaigns",
            "UPDATE campaigns SET name = (SELECT name FROM memory_entities LIMIT 1)",
            "memory_entities",
        )
        self.assertEqual(self.check(sql), [])

    def test_the_policy_after_the_statement_does_not_count(self) -> None:
        sql = "UPDATE campaigns SET name = 'x';\n" + wrapped("campaigns", "SELECT 1")
        self.assertTrue(self.check(sql))

    def test_a_delete_and_an_insert_select_are_read_too(self) -> None:
        self.assertTrue(self.check("DELETE FROM campaigns WHERE id = '1';"))
        self.assertTrue(self.check("INSERT INTO campaigns (id) SELECT id FROM memory_entities;"))

    def test_a_table_made_in_the_same_migration_has_no_rows_to_miss(self) -> None:
        sql = "CREATE TABLE campaigns (id TEXT);\nUPDATE campaigns SET id = 'x';"
        self.assertEqual(self.check(sql), [])

    def test_text_in_a_comment_a_string_or_a_function_is_not_a_statement(self) -> None:
        sql = """
        -- UPDATE campaigns SET name = 'x';
        /* DELETE FROM campaigns; */
        SELECT 'UPDATE campaigns SET a = 1; DELETE FROM campaigns';
        CREATE FUNCTION f() RETURNS trigger AS $$
            BEGIN UPDATE campaigns SET a = 1; RETURN NEW; END $$ LANGUAGE plpgsql;
        """
        self.assertEqual(self.check(sql), [])

    def test_a_statement_in_a_do_block_is_read(self) -> None:
        sql = "DO $$ BEGIN UPDATE campaigns SET name = 'x'; END $$;"
        self.assertTrue(self.check(sql))

    def test_a_cte_and_a_statement_it_cannot_place_fail_loudly(self) -> None:
        self.assertTrue(self.check("WITH s AS (SELECT 1) UPDATE campaigns SET name = 'x';"))
        self.assertTrue(self.check("UPDATE (campaigns) SET name = 'x';"))
        self.assertTrue(
            self.check("MERGE INTO campaigns USING x ON true WHEN MATCHED THEN DELETE;")
        )


if __name__ == "__main__":
    unittest.main()
