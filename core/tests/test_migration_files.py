"""One file per migration (#2127): the package builds the same migrations the old `schema.py`
tuple held, and a number can only be taken once.

`tests/fixtures/migrations_before.json` was captured from the monolithic schema.py by
`scripts/split_migrations.py` before the move: each migration's name and the SHA-256 of its SQL,
and the registrations (isolated, user-isolated and routing tables, the website role's functions).
A migration may be added after those, never changed or removed, so the fixture stays a prefix of
what the package builds.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

from dmbot import migrations, schema

BEFORE = json.loads((Path(__file__).parent / "fixtures" / "migrations_before.json").read_text())


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class TheBuiltList(unittest.TestCase):
    def test_every_old_migration_is_there_in_order_with_the_same_sql(self) -> None:
        built = [name for name, _ in schema.MIGRATIONS]
        old = BEFORE["names"]
        positions = [built.index(name) for name in old]  # (a missing one raises ValueError)
        self.assertEqual(positions, sorted(positions), "the old migrations changed order")
        by_name = dict(schema.MIGRATIONS)
        changed = [name for name in old if sha(by_name[name]) != BEFORE["sha256"][name]]
        self.assertEqual(changed, [], "a shipped migration's SQL changed; add a new one instead")

    def test_the_list_is_in_name_order_with_no_repeated_number(self) -> None:
        names = [name for name, _ in schema.MIGRATIONS]
        self.assertEqual(names, sorted(names))
        numbers = [name[:4] for name in names]
        self.assertEqual(len(numbers), len(set(numbers)))

    def test_the_old_registrations_are_all_still_registered(self) -> None:
        self.assertLessEqual(set(BEFORE["isolated"]), set(schema.ISOLATED_TABLES))
        self.assertLessEqual(set(BEFORE["user_isolated"]), set(schema.USER_ISOLATED_TABLES))
        self.assertLessEqual(set(BEFORE["routing"]), set(schema.ROUTING_TABLES))
        built = {tuple(f) for f in schema.WEB_ROLE_FUNCTIONS}
        self.assertLessEqual({tuple(f) for f in BEFORE["web_role_functions"]}, built)

    def test_nothing_is_registered_twice(self) -> None:
        for name in ("ISOLATED_TABLES", "USER_ISOLATED_TABLES", "ROUTING_TABLES"):
            values = list(getattr(schema, name))
            self.assertEqual(len(values), len(set(values)), name)
        functions = list(schema.WEB_ROLE_FUNCTIONS)
        self.assertEqual(len(functions), len(set(functions)))


class TheFiles(unittest.TestCase):
    def test_a_file_registers_only_what_it_creates(self) -> None:
        """A migration lists the tables and functions it makes, so the list is where they are."""
        for name, module in migrations._BY_NAME.items():
            sql = module.SQL
            for attr in ("ISOLATED_TABLES", "USER_ISOLATED_TABLES", "ROUTING_TABLES"):
                for table in getattr(module, attr, ()):
                    pattern = rf"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?{re.escape(table)}\b"
                    self.assertRegex(sql, re.compile(pattern, re.I), f"{name}: {table}")
            for fn, _signature in getattr(module, "WEB_ROLE_FUNCTIONS", ()):
                pattern = rf"CREATE\s+(OR\s+REPLACE\s+)?FUNCTION\s+{re.escape(fn)}\s*\("
                self.assertRegex(sql, re.compile(pattern, re.I), f"{name}: {fn}")

    def test_two_files_with_one_number_fail_to_load(self) -> None:
        with self.assertRaises(ImportError):
            migrations.check_names(["0001_a", "0002_b", "0002_c"])

    def test_a_file_must_be_named_with_its_number(self) -> None:
        with self.assertRaises(ImportError):
            migrations.check_names(["0001_a", "extra_b"])
        migrations.check_names(["0001_a", "0002_b"])  # fine

    def test_names_the_old_schema_defined_still_resolve(self) -> None:
        self.assertIsInstance(schema.WEB_ACCOUNTS, str)  # a migration's constant
        self.assertEqual(schema.WEB_ROLE, "dmbot_web")
        with self.assertRaises(AttributeError):
            schema.NOT_A_THING  # noqa: B018
