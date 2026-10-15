"""A person's Discord name kept on their account (#1242 item 2): only the name, only for an
account that exists, at most once a day, and never in the way of a consent or a start."""

from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.bot import DISPLAY_NAME_REFRESH_S, NAMES_NOTED_MAX, DMBot
from dmbot.web import accounts
from tests.pg import DatabaseTest

ALICE, BOB = 7101, 7102


class Kept(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        for user, name in ((ALICE, None), (BOB, "Bob")):
            async with self.db.user(user) as conn:
                await conn.execute(
                    "INSERT INTO web_users (user_id, email, created_at, last_sign_in_at,"
                    " display_name) VALUES (%s, NULL, 0, 0, %s)",
                    (user, name),
                )

    async def name(self, user: int) -> str | None:
        async with self.db.user(user) as conn:
            cur = await conn.execute(
                "SELECT display_name FROM web_users WHERE user_id = %s", (user,)
            )
            row = await cur.fetchone()
        return None if row is None else row["display_name"]

    async def test_a_missing_name_is_filled_in_and_the_same_name_changes_nothing(self) -> None:
        self.assertTrue(await accounts.remember_display_name(self.db, ALICE, "  Mia  Hart "))
        self.assertEqual(await self.name(ALICE), "Mia Hart")
        self.assertFalse(await accounts.remember_display_name(self.db, ALICE, "Mia Hart"))
        self.assertTrue(await accounts.remember_display_name(self.db, ALICE, "Mia H"))  # renamed
        self.assertEqual(await self.name(ALICE), "Mia H")

    async def test_nothing_is_made_for_a_stranger_and_an_empty_name_is_ignored(self) -> None:
        self.assertFalse(await accounts.remember_display_name(self.db, 9999, "Nobody"))
        async with self.db.user(9999) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM web_users")
            self.assertEqual((await cur.fetchone() or {})["n"], 0)
        self.assertFalse(await accounts.remember_display_name(self.db, ALICE, "   "))
        self.assertIsNone(await self.name(ALICE))

    async def test_it_touches_only_that_persons_row_and_only_the_name(self) -> None:
        await accounts.remember_display_name(self.db, ALICE, "Mia")
        self.assertEqual(await self.name(BOB), "Bob")
        async with self.db.user(ALICE) as conn:
            cur = await conn.execute(
                "SELECT email, created_at, last_sign_in_at FROM web_users WHERE user_id = %s",
                (ALICE,),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["email"], row["created_at"], row["last_sign_in_at"]), (None, 0, 0))

    async def test_a_very_long_name_is_cut(self) -> None:
        await accounts.remember_display_name(self.db, ALICE, "x" * 500)
        self.assertEqual(len(await self.name(ALICE) or ""), accounts.NAME_MAX)


class Bot(unittest.IsolatedAsyncioTestCase):
    def fake(self, meter: Any = "db") -> Any:
        fake = SimpleNamespace(
            meter=None if meter is None else SimpleNamespace(db=meter),
            _names_noted={},
            _name_tasks=set(),
        )
        bound: Any = DMBot._keep_display_name
        fake._keep_display_name = lambda db, user, name: bound(fake, db, user, name)
        return fake

    async def settle(self, fake: Any) -> None:
        await asyncio.gather(*list(fake._name_tasks))

    async def test_it_runs_in_the_background_and_only_once_a_day(self) -> None:
        fake = self.fake()
        with patch("dmbot.bot.accounts.remember_display_name", new=AsyncMock()) as kept:
            DMBot.note_display_name(fake, ALICE, "Mia")  # returns at once, not a coroutine
            DMBot.note_display_name(fake, ALICE, "Mia")
            await self.settle(fake)
            self.assertEqual(kept.await_count, 1)  # the second is inside the day
            kept.assert_awaited_with("db", ALICE, "Mia")
            fake._names_noted[ALICE] -= DISPLAY_NAME_REFRESH_S + 1
            DMBot.note_display_name(fake, ALICE, "Mia")
            await self.settle(fake)
            self.assertEqual(kept.await_count, 2)
        self.assertEqual(fake._name_tasks, set())  # tasks do not pile up

    async def test_a_failure_never_raises_and_is_tried_again(self) -> None:
        fake = self.fake()
        with patch("dmbot.bot.accounts.remember_display_name", new=AsyncMock(side_effect=OSError)):
            DMBot.note_display_name(fake, BOB, "Bob")
            await self.settle(fake)
        self.assertNotIn(BOB, fake._names_noted)

    async def test_a_stuck_database_is_given_up_on(self) -> None:
        fake = self.fake()

        async def stuck(*_: Any) -> None:
            await asyncio.sleep(60)

        with (
            patch("dmbot.bot.accounts.remember_display_name", new=stuck),
            patch("dmbot.bot.DISPLAY_NAME_TIMEOUT_S", 0.05),
        ):
            DMBot.note_display_name(fake, BOB, "Bob")
            await self.settle(fake)  # ends by itself, quickly
        self.assertNotIn(BOB, fake._names_noted)

    async def test_nothing_without_a_database_or_a_name(self) -> None:
        with patch("dmbot.bot.accounts.remember_display_name", new=AsyncMock()) as kept:
            for fake, name in ((self.fake(None), "Mia"), (self.fake(), None), (self.fake(), "")):
                DMBot.note_display_name(fake, ALICE, name)
                self.assertEqual(fake._name_tasks, set())
            kept.assert_not_called()

    async def test_the_remembered_set_stays_bounded(self) -> None:
        fake = self.fake()
        fake._names_noted = {n: time.monotonic() + n for n in range(NAMES_NOTED_MAX)}
        with patch("dmbot.bot.accounts.remember_display_name", new=AsyncMock()):
            DMBot.note_display_name(fake, 99_999, "New")
            await self.settle(fake)
        self.assertLessEqual(len(fake._names_noted), NAMES_NOTED_MAX // 2 + 1)

    async def test_consent_and_start_pass_the_account_name_never_the_server_nickname(self) -> None:
        seen: list[tuple[int, str | None]] = []
        member = SimpleNamespace(
            global_name="Mia Hart", name="mia_h", nick="Captain", display_name="Captain"
        )
        fake = SimpleNamespace(note_display_name=lambda user_id, name: seen.append((user_id, name)))
        # The two call sites use `global_name or name`; this is the rule they share.
        for m in (member, SimpleNamespace(global_name=None, name="mia_h", nick="Cap")):
            fake.note_display_name(ALICE, m.global_name or m.name)
        self.assertEqual(seen, [(ALICE, "Mia Hart"), (ALICE, "mia_h")])
        import inspect

        source = inspect.getsource(DMBot.give_consent) + inspect.getsource(DMBot._start_locked)
        self.assertIn("global_name or member.name", source)
        self.assertIn("global_name or user.name", source)
        self.assertNotIn("display_name", source.split("note_display_name")[1])
