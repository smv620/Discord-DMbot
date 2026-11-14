"""Someone else's install is left alone whichever way Postgres says no (found when CI's Postgres
moved from 16.15 to 16.16): the upsert's WHERE used to be applied before the update policy and
skipped the row quietly; 16.16 checks the policy first and raises. No database here."""

from __future__ import annotations

import contextlib
import unittest
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

from psycopg import errors as pg_errors

from dmbot import accounts

NONE = "none"  # (no row visible)


def _async(value: Any) -> Any:
    async def fetchone() -> Any:
        return value

    return fetchone


class FakeConn:
    """`seen` is what the first look at the install shows, `later` what a second look shows."""

    def __init__(self, seen: Any, raises: bool, later: Any) -> None:
        self.seen, self.raises, self.later = seen, raises, later
        self.statements: list[str] = []
        self.looks = 0

    async def execute(self, sql: str, args: tuple[Any, ...] = ()) -> Any:
        self.statements.append(sql.split()[0])
        if sql.startswith("SELECT"):
            self.looks += 1
            seen = self.seen if self.looks == 1 else self.later
            row = None if seen == NONE else {"installed_by_user_id": seen}
            return SimpleNamespace(fetchone=_async(row))
        if self.raises:
            raise pg_errors.InsufficientPrivilege("new row violates row-level security policy")
        return SimpleNamespace(fetchone=_async({"guild_id": 1}))


class FakeDb:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    @contextlib.asynccontextmanager
    async def user(self, user_id: int, **_: Any) -> AsyncIterator[FakeConn]:
        yield self.conn


class RecordInstall(unittest.IsolatedAsyncioTestCase):
    async def record(
        self, seen: Any, *, raises: bool = False, later: Any = None
    ) -> tuple[str, list[str]]:
        conn = FakeConn(seen, raises, seen if later is None else later)
        got = await accounts.record_install(cast(Any, FakeDb(conn)), 5, 99, now=1, session="s")
        return got, conn.statements

    async def test_someone_elses_install_is_seen_first_and_nothing_is_written(self) -> None:
        self.assertEqual(await self.record(7), ("already_linked", ["SELECT"]))

    async def test_a_policy_error_after_someone_else_won_a_race_is_the_same_answer(self) -> None:
        got, _ = await self.record(None, raises=True, later=7)
        self.assertEqual(got, "already_linked")

    async def test_a_policy_error_for_a_server_the_person_may_not_touch_stands(self) -> None:
        for later in (NONE, None, 5):  # (no row seen, nobody's, or their own: nothing to blame)
            with self.subTest(later=later), self.assertRaises(pg_errors.InsufficientPrivilege):
                await self.record(NONE, raises=True, later=NONE if later is None else later)

    async def test_nobodys_install_or_ones_own_or_a_new_server_is_recorded(self) -> None:
        for seen in (None, 5, NONE):
            with self.subTest(seen=seen):
                self.assertEqual((await self.record(seen))[0], "recorded")
