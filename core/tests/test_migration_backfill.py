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
# today: every statement is wrapped, or touches only a table the same migration just created
# (which has no rows yet; the check sees that for itself).
NOT_NEEDED: dict[tuple[str, str], str] = {}

DOLLAR = re.compile(r"\$(\w*)\$")
_NAME = r"(?:\w+\.)?(\w+)"
UPDATE = re.compile(rf"(?<!\bfor )\bUPDATE (?:ONLY )?{_NAME}", re.I)
DELETE = re.compile(rf"\bDELETE FROM (?:ONLY )?{_NAME}", re.I)
INSERT = re.compile(rf"\bINSERT INTO {_NAME}", re.I)
NOT_TABLES = {"on", "to", "of", "set", "nowait", "skip"}  # (`DO UPDATE SET`, `GRANT UPDATE ON`)
# A table list after FROM, JOIN or USING ("FROM a x, public.b AS y"): every table, not just one.
_WORDS = "USING|WHERE|SET|JOIN|ON|LEFT|RIGHT|INNER|FULL|CROSS|NATURAL|GROUP|ORDER|LIMIT|RETURNING"
_ONE = rf"(?:\w+\.)?\w+(?:\s+(?:AS\s+)?(?!(?:{_WORDS})\b)\w+)?"
READ = re.compile(rf"\b(?:FROM|JOIN|USING)\s+(?:ONLY\s+)?({_ONE}(?:\s*,\s*{_ONE})*)", re.I)
TABLE_FORM = re.compile(rf"\bTABLE {_NAME}", re.I)  # INSERT INTO t TABLE other
# Statements that cannot change rows (inside a DO block, where any statement can appear).
HARMLESS = ("GRANT", "REVOKE", "CREATE", "ALTER", "DROP", "COMMENT", "SET", "RAISE", "PERFORM")


def mask(sql: str) -> str:
    """The SQL with comments, string text and quoted names blanked to spaces (same length, so
    offsets stay true), so that nothing inside them is read as a statement or a policy. Text
    between `$$` marks is kept, masked in turn. An unbalanced quote raises: better loud."""
    out = list(sql)
    i, n = 0, len(sql)

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i)
            if j < 0:
                raise ValueError("an unclosed comment")
            blank(i, j + 2)
            i = j + 2
        elif sql[i] in "'\"":
            quote = sql[i]
            escapes = (
                quote == "'"
                and i > 0
                and sql[i - 1] in "eE"
                and not (i > 1 and (sql[i - 2].isalnum() or sql[i - 2] == "_"))
            )
            j = i + 1
            while True:
                if j >= n:
                    raise ValueError(f"an unclosed {quote} quote")
                if escapes and sql[j] == "\\":
                    j += 2
                elif sql[j] == quote:
                    if sql[j + 1 : j + 2] == quote:
                        j += 2
                    else:
                        break
                else:
                    j += 1
            blank(i + 1, j)
            i = j + 1
        elif sql[i] == "$" and (tag := DOLLAR.match(sql, i)):
            close = sql.find(tag[0], tag.end())
            if close < 0:
                raise ValueError("an unclosed $$ block")
            out[tag.end() : close] = list(mask(sql[tag.end() : close]))
            i = close + len(tag[0])
        else:
            i += 1
    return "".join(out)


def statements(text: str, base: int = 0, inner: bool = False) -> list[tuple[int, int, str, bool]]:
    """(offset, statement, inside_a_do_block) from masked SQL. `DO` blocks are read inside;
    function bodies (triggers) run later, not at migration time, and are skipped."""
    found: list[tuple[int, int, str, bool]] = []
    start = i = 0
    while i < len(text):
        if text[i] == "$" and (tag := DOLLAR.match(text, i)):
            close = text.find(tag[0], tag.end())
            if re.match(r"\s*DO\b", text[start:i], re.I):
                found += statements(text[tag.end() : close], base + tag.end(), inner=True)
            i = close + len(tag[0])
        elif text[i] == ";":
            if text[start:i].strip():
                found.append((base + start, base + i, text[start:i].strip(), inner))
            i += 1
            start = i
        else:
            i += 1
    if text[start:].strip():
        found.append((base + start, base + len(text), text[start:].strip(), inner))
    return found


def read_tables(text: str) -> set[str]:
    """The tables a statement reads through FROM, JOIN, USING or TABLE (comma lists and schema
    names included; an alias or the next keyword after a name is ignored)."""
    names: set[str] = set()
    for group in READ.findall(text):
        for part in group.split(","):
            names.add(part.split()[0].rsplit(".", 1)[-1].lower())
    names |= {m[1].lower() for m in TABLE_FORM.finditer(text)}
    return names


def changes(text: str, inside_do: bool, original: str = "") -> tuple[set[str], list[str]]:
    """(tables a statement writes or reads, reasons it cannot be placed)."""
    text = re.sub(r"\s+", " ", text)
    first = text.split(" ", 1)[0].upper()
    dynamic = inside_do and first not in HARMLESS and re.search(r"\bEXECUTE\b", text, re.I)
    rows = re.search(r"\b(?:UPDATE|DELETE\s+FROM|INSERT\s+INTO)\b", original, re.I)
    harmless = re.search(r"\b(?:GRANT|REVOKE|CREATE|ALTER|DROP)\b", original, re.I)
    if dynamic and rows and not harmless:
        # Dynamic SQL is text the check cannot follow. A grant, a policy or a role is harmless;
        # a row change in it must be written out where the check can read it.
        return set(), [f"dynamic SQL in a DO block changes rows: {text[:60]!r}"]
    if first == "MERGE":
        return set(), [f"a MERGE statement cannot be read: {text[:60]!r}"]
    if first == "TRUNCATE":
        return set(), []  # (not subject to row-level security)
    created = first == "CREATE" and re.match(
        r"CREATE (?:\w+ )*TABLE (?:IF NOT EXISTS )?[\w.]+ AS\b", text, re.I
    )
    selected = first == "SELECT" and re.search(r"\bINTO [a-z_]", text, re.I)
    if (first in HARMLESS or first == "SELECT") and not (created or selected):
        return set(), []
    written = [
        m[1].lower() for pat in (UPDATE, DELETE, INSERT) for m in pat.finditer(text)
        if m[1].lower() not in NOT_TABLES
    ]  # fmt: skip
    if first in ("UPDATE", "DELETE", "INSERT") and not written:
        return set(), [f"cannot place the table of {text[:60]!r}"]
    values = INSERT.match(text) and re.search(r"\bVALUES\b", text, re.I)
    if values and not re.search(r"\bSELECT\b", text, re.I):
        return set(), []  # INSERT ... VALUES reads nothing (a blocked insert fails loudly)
    if not written and not (created or selected):
        return set(), []
    return set(written) | read_tables(text), []


def problems(name: str, sql: str, forced: set[str] = FORCED) -> list[str]:
    """What is missing in one migration, naming the migration and the table."""
    text = mask(sql)
    created_at = {m[1].lower(): m.start() for m in re.finditer(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", text, re.I)}  # fmt: skip
    events = sorted(
        [(m.start(), "open", m[1].lower()) for m in re.finditer(
            r"CREATE\s+POLICY\s+migrate_backfill\s+ON\s+(\w+)", text, re.I)]
        + [(m.start(), "close", m[1].lower()) for m in re.finditer(
            r"DROP\s+POLICY\s+(?:IF\s+EXISTS\s+)?migrate_backfill\s+ON\s+(\w+)", text, re.I)]
    )  # fmt: skip
    windows: dict[str, list[tuple[int, int]]] = {}
    opened: dict[str, int] = {}
    for at, kind, table in events:  # each CREATE paired with the next DROP of the same table
        if kind == "open":
            opened.setdefault(table, at)
        elif table in opened:
            windows.setdefault(table, []).append((opened.pop(table), at))
    out = [f"{name}: migrate_backfill on {t} is never dropped" for t in sorted(opened)]
    for offset, end, statement, inside_do in statements(text):
        tables, unplaced = changes(statement, inside_do, sql[offset:end])
        out += [f"{name}: {why}" for why in unplaced]
        written = {
            m[1].lower() for pat in (UPDATE, DELETE, INSERT) for m in pat.finditer(statement)
        }
        for table in sorted(tables & forced):
            if table in created_at and created_at[table] < offset:
                continue  # made earlier in this migration: no rows yet
            if (name, table) in NOT_NEEDED:
                continue
            if not any(a < offset < b for a, b in windows.get(table, [])):
                role = "writes" if table in written else "reads"
                out.append(
                    f"{name}: {role} {table} without `CREATE POLICY migrate_backfill ON {table}` "
                    f"before and `DROP POLICY migrate_backfill ON {table}` after: "
                    f"{re.sub(r'\s+', ' ', statement)[:60]!r}"
                )
    return out


class TodaysMigrationsTest(unittest.TestCase):
    def test_every_row_changing_statement_opens_row_level_security(self) -> None:
        found = [p for name, sql in schema.MIGRATIONS for p in problems(name, sql)]
        self.assertEqual(found, [])

    def test_the_check_sees_the_statements_that_follow_the_rule(self) -> None:
        # (so a parser that finds nothing cannot pass for the right reason)
        counted = {}
        for name, sql in schema.MIGRATIONS:
            seen = [changes(t, d)[0] for _, _, t, d in statements(mask(sql))]
            counted[name] = [tables & FORCED for tables in seen if tables & FORCED]
        self.assertEqual(counted["0130_initiative_style_backfill"], [{"campaigns"}])
        self.assertIn({"memory_entities", "memory_types"}, counted["0044_memory_kinds"])
        self.assertGreater(sum(len(v) for v in counted.values()), 20)

    def test_the_allow_list_has_no_stale_entries(self) -> None:
        names = {name for name, _ in schema.MIGRATIONS}
        for migration, table in NOT_NEEDED:
            self.assertIn(migration, names)
            self.assertIn(table, FORCED)


def wrapped(table: str, body: str, *others: str) -> str:
    tables = (table, *others)
    policy = "CREATE POLICY migrate_backfill ON {} USING (true) WITH CHECK (true);\n"
    opened = "".join(policy.format(t) for t in tables)
    closed = "".join(f"DROP POLICY migrate_backfill ON {t};\n" for t in tables)
    return opened + body + ";\n" + closed


class StandInTest(unittest.TestCase):
    def check(self, sql: str) -> list[str]:
        return problems("9999_stand_in", sql, {"campaigns", "memory_entities"})

    def assert_reads(self, sql: str, table: str = "memory_entities") -> None:
        found = self.check(sql)
        self.assertEqual(len(found), 1, found)
        self.assertIn(f"reads {table}", found[0])

    def test_a_bare_update_fails_naming_the_migration_and_the_table(self) -> None:
        (found,) = self.check("UPDATE campaigns SET name = 'x' WHERE id = '1';")
        self.assertIn("9999_stand_in", found)
        self.assertIn("campaigns", found)

    def test_a_wrapped_update_passes(self) -> None:
        self.assertEqual(self.check(wrapped("campaigns", "UPDATE campaigns SET name = 'x'")), [])

    def test_a_joined_table_that_is_not_wrapped_fails(self) -> None:
        self.assert_reads(
            wrapped(
                "campaigns",
                "UPDATE campaigns SET name = e.name FROM memory_entities e WHERE e.id = 1",
            )
        )

    def test_both_tables_wrapped_passes(self) -> None:
        sql = wrapped(
            "campaigns",
            "UPDATE campaigns SET name = (SELECT name FROM memory_entities LIMIT 1)",
            "memory_entities",
        )
        self.assertEqual(self.check(sql), [])

    def test_delete_using_and_comma_lists_are_read(self) -> None:
        self.assert_reads(
            wrapped("campaigns", "DELETE FROM campaigns USING memory_entities e WHERE true")
        )
        self.assert_reads(
            wrapped("campaigns", "UPDATE campaigns SET a = 1 FROM unrelated u, memory_entities e")
        )

    def test_a_table_read_in_a_cte_is_read(self) -> None:
        sql = "WITH s AS (SELECT id FROM memory_entities) UPDATE campaigns SET a = 1 FROM s"
        found = self.check(wrapped("campaigns", sql))
        self.assertEqual(len(found), 1, found)
        self.assertIn("memory_entities", found[0])

    def test_a_second_write_in_one_statement_is_checked(self) -> None:
        sql = "WITH d AS (DELETE FROM memory_entities RETURNING id) UPDATE campaigns SET a = 1"
        found = self.check(wrapped("campaigns", sql))
        self.assertEqual(len(found), 1, found)
        self.assertIn("memory_entities", found[0])

    def test_the_policy_must_surround_the_statement_not_just_exist(self) -> None:
        after = "UPDATE campaigns SET name = 'x';\n" + wrapped("campaigns", "SELECT 1")
        self.assertTrue(self.check(after))
        twice = wrapped("campaigns", "SELECT 1") + "UPDATE campaigns SET a = 1;\n"
        self.assertTrue(self.check(twice + wrapped("campaigns", "SELECT 2")))
        self.assertTrue(self.check("CREATE POLICY migrate_backfill ON campaigns USING (true);"))

    def test_policy_text_in_a_string_or_comment_does_not_count(self) -> None:
        sql = (
            "SELECT 'CREATE POLICY migrate_backfill ON campaigns';\n"
            "-- CREATE POLICY migrate_backfill ON campaigns\n"
            "UPDATE campaigns SET a = 1;\n"
            "SELECT 'DROP POLICY migrate_backfill ON campaigns';\n"
        )
        self.assertTrue(self.check(sql))

    def test_a_delete_and_an_insert_select_are_read_too(self) -> None:
        self.assertTrue(self.check("DELETE FROM campaigns WHERE id = '1';"))
        self.assertTrue(self.check("INSERT INTO campaigns (id) SELECT id FROM memory_entities;"))
        self.assertTrue(self.check("INSERT INTO other TABLE memory_entities;"))
        self.assertTrue(self.check("CREATE TABLE copy AS SELECT * FROM campaigns;"))
        self.assertEqual(self.check("INSERT INTO other (a) VALUES (1);"), [])

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
        GRANT SELECT, INSERT, UPDATE ON campaigns TO someone;
        CREATE POLICY p ON campaigns FOR UPDATE USING (true);
        SELECT * FROM campaigns FOR UPDATE;
        INSERT INTO other (a) VALUES (1) ON CONFLICT (a) DO UPDATE SET a = 2;
        """
        self.assertEqual(self.check(sql), [])

    def test_a_statement_anywhere_in_a_do_block_is_read(self) -> None:
        for body in (
            "BEGIN UPDATE campaigns SET name = 'x'; END",
            "BEGIN IF true THEN UPDATE campaigns SET name = 'x'; END IF; END",
            "BEGIN BEGIN UPDATE campaigns SET a = 1; EXCEPTION WHEN OTHERS THEN NULL; END; END",
            "BEGIN FOR r IN SELECT 1 LOOP UPDATE campaigns SET a = 1; END LOOP; END",
            "DECLARE x int; BEGIN UPDATE campaigns SET a = 1; END",
        ):
            with self.subTest(body=body):
                self.assertTrue(self.check(f"DO $$ {body} $$;"))
        self.assertTrue(self.check("DO LANGUAGE plpgsql $$ BEGIN DELETE FROM campaigns; END $$;"))
        self.assertTrue(self.check("DO $$ BEGIN EXECUTE 'UPDATE campaigns SET a = 1'; END $$;"))

    def test_a_do_block_inside_a_wrapper_passes(self) -> None:
        sql = wrapped("campaigns", "DO $$ BEGIN UPDATE campaigns SET a = 1; END $$")
        self.assertEqual(self.check(sql), [])

    def test_quotes_cannot_hide_a_statement(self) -> None:
        self.assertTrue(self.check("""SELECT "it's"; UPDATE campaigns SET a = 1;"""))
        self.assertTrue(self.check("""SELECT E'it\\'s; x'; UPDATE campaigns SET a = 1;"""))
        with self.assertRaises(ValueError):
            mask("SELECT 'unclosed; UPDATE campaigns SET a = 1;")

    def test_a_cte_a_merge_and_an_unplaceable_statement_fail_loudly(self) -> None:
        self.assertTrue(self.check("WITH s AS (SELECT 1) UPDATE campaigns SET name = 'x';"))
        self.assertTrue(self.check("UPDATE (campaigns) SET name = 'x';"))
        self.assertTrue(self.check("UPDATE \"campaigns\" SET name = 'x';"))
        merge = "MERGE INTO campaigns USING x ON true WHEN MATCHED THEN DELETE;"
        self.assertTrue(self.check(merge))


if __name__ == "__main__":
    unittest.main()
