"""Helpers for tests that need a real Postgres.

Set DMBOT_TEST_DATABASE_URL to a database owned by an ordinary (non-superuser) role;
each test gets its own fresh schema, dropped afterwards. Without the variable, these
tests are skipped (CI always sets it). See README, "Database".
"""

from __future__ import annotations

import os
import unittest
import uuid

from dmbot.db import Database, drop_schema

TEST_URL = os.environ.get("DMBOT_TEST_DATABASE_URL", "")
# CI sets this so database tests fail, rather than quietly skip, without a database.
REQUIRE_DB = os.environ.get("DMBOT_REQUIRE_DB_TESTS") == "1"
# Optional: a superuser URL, to test that DMbot refuses one.
SUPERUSER_URL = os.environ.get("DMBOT_TEST_SUPERUSER_URL", "")

requires_db = unittest.skipUnless(TEST_URL, "set DMBOT_TEST_DATABASE_URL to run database tests")


class DatabaseTest(unittest.IsolatedAsyncioTestCase):
    """Gives each test `self.db`: a fresh, fully migrated schema."""

    db: Database

    async def asyncSetUp(self) -> None:
        if not TEST_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_DATABASE_URL is not set but DMBOT_REQUIRE_DB_TESTS=1")
            self.skipTest("set DMBOT_TEST_DATABASE_URL to run database tests")
        self.schema = f"t_{uuid.uuid4().hex[:16]}"
        self.db = await Database.open(TEST_URL, schema=self.schema, max_size=4)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)
