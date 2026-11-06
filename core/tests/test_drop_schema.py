"""Dropping a test schema when Postgres runs out of lock slots (#3030): the tables go one at a
time and the schema last; any other error is still raised. Needs a database."""

from __future__ import annotations

import unittest
import uuid
from typing import Any
from unittest.mock import patch

import psycopg
from psycopg import AsyncConnection, sql
from psycopg import errors as pg_errors

from dmbot.db import drop_schema
from tests.pg import TEST_URL, requires_db


class FlakyConnection:
    """A connection that refuses the first whole-schema drop the way a full lock table does."""

    def __init__(self, conn: Any, error: Exception) -> None:
        self._conn, self._error = conn, error
        self.statements: list[str] = []

    async def execute(self, query: Any, params: Any = None) -> Any:
        text = query.as_string() if hasattr(query, "as_string") else str(query)
        self.statements.append(text)
        if "DROP SCHEMA" in text and sum("DROP SCHEMA" in s for s in self.statements) == 1:
            raise self._error
        return await self._conn.execute(query, params)

    async def close(self) -> None:
        await self._conn.close()


@requires_db
class TheFallback(unittest.IsolatedAsyncioTestCase):
    def make(self, tables: int = 3) -> str:
        schema = f"t_{uuid.uuid4().hex[:16]}"
        with psycopg.connect(TEST_URL, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            for n in range(tables):
                conn.execute(
                    sql.SQL("CREATE TABLE {}.{} (id INT)").format(
                        sql.Identifier(schema), sql.Identifier(f"t{n}")
                    )
                )
        return schema

    def exists(self, schema: str) -> bool:
        with psycopg.connect(TEST_URL, autocommit=True) as conn:
            row = conn.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,)
            ).fetchone()
        return row is not None

    def flaky(self, error: Exception) -> tuple[Any, list[FlakyConnection]]:
        made: list[FlakyConnection] = []
        real = AsyncConnection.connect

        async def connect(*args: Any, **kwargs: Any) -> FlakyConnection:
            wrapper = FlakyConnection(await real(*args, **kwargs), error)
            made.append(wrapper)
            return wrapper

        return patch.object(AsyncConnection, "connect", connect), made

    async def test_a_plain_drop_is_one_statement(self) -> None:
        schema = self.make()
        await drop_schema(TEST_URL, schema)
        self.assertFalse(self.exists(schema))

    async def test_out_of_lock_slots_drops_the_tables_one_by_one_then_the_schema(self) -> None:
        schema = self.make(tables=4)
        patcher, made = self.flaky(pg_errors.OutOfMemory("out of shared memory"))
        with patcher:
            await drop_schema(TEST_URL, schema)
        self.assertFalse(self.exists(schema))
        statements = made[0].statements
        self.assertEqual(sum("DROP TABLE" in s for s in statements), 4)
        self.assertEqual(sum("DROP SCHEMA" in s for s in statements), 2)  # refused, then done

    async def test_any_other_error_is_still_raised_and_the_schema_is_left(self) -> None:
        schema = self.make()
        patcher, _ = self.flaky(pg_errors.InsufficientPrivilege("no"))
        try:
            with patcher, self.assertRaises(pg_errors.InsufficientPrivilege):
                await drop_schema(TEST_URL, schema)
            self.assertTrue(self.exists(schema))
        finally:
            await drop_schema(TEST_URL, schema)

    async def test_a_bad_name_is_refused_before_anything_is_dropped(self) -> None:
        with self.assertRaises(ValueError):
            await drop_schema(TEST_URL, "public; DROP TABLE x")
