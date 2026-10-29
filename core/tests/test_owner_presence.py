"""An owner who left Discord for good (#1214): what to do about each answer, the questions
asked of Discord, the bot's flow with a stand-in server, and the store's two writes."""

from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot import owner_presence as op
from dmbot.campaigns import Campaign, CampaignStore
from tests.pg import DatabaseTest
from tests.test_memory_store import DM, GUILD_A
from tests.test_sidebar_answer import campaign as base_campaign
from tests.test_sidebar_bot import make_bot

NOW = 1_800_000_000
DAY = 86400


def not_found(code: int) -> discord.NotFound:
    response: Any = SimpleNamespace(
        status=404, reason="Not Found"
    )  # only status and reason are read
    return discord.NotFound(response, {"code": code, "message": "x"})


class Deciding(unittest.TestCase):
    def test_here_forgets_an_earlier_miss_and_otherwise_does_nothing(self) -> None:
        self.assertEqual(op.decide(NOW - DAY, op.HERE, NOW).action, op.CLEAR)
        self.assertEqual(op.decide(None, op.HERE, NOW).action, op.NOTHING)

    def test_missing_is_noted_then_six_days_do_nothing_and_seven_end_it(self) -> None:
        self.assertEqual(op.decide(None, op.GONE_FROM_SERVER, NOW).action, op.MARK)
        self.assertEqual(op.decide(NOW - 6 * DAY, op.GONE_FROM_SERVER, NOW).action, op.NOTHING)
        self.assertEqual(op.decide(NOW - 7 * DAY, op.GONE_FROM_SERVER, NOW).action, op.ORPHAN)

    def test_a_deleted_account_skips_the_wait(self) -> None:
        self.assertEqual(op.decide(None, op.ACCOUNT_DELETED, NOW).action, op.ORPHAN)

    def test_not_knowing_changes_nothing(self) -> None:
        for since in (None, NOW - 30 * DAY):
            self.assertEqual(op.decide(since, op.DONT_KNOW, NOW).action, op.NOTHING)


class Looking(unittest.IsolatedAsyncioTestCase):
    async def look(self, member: Any, user: Any = None) -> str:
        return await op.look(AsyncMock(side_effect=member), AsyncMock(side_effect=user), 5)

    async def test_the_answers(self) -> None:
        self.assertEqual(await self.look(None), op.HERE)
        self.assertEqual(await self.look(not_found(op.UNKNOWN_MEMBER)), op.GONE_FROM_SERVER)
        self.assertEqual(
            await self.look(not_found(op.UNKNOWN_MEMBER), not_found(op.UNKNOWN_USER)),
            op.ACCOUNT_DELETED,
        )

    async def test_every_other_error_is_not_knowing(self) -> None:
        self.assertEqual(
            await self.look(not_found(10004)), op.DONT_KNOW
        )  # some other missing thing
        self.assertEqual(await self.look(RuntimeError("down")), op.DONT_KNOW)
        self.assertEqual(
            await self.look(not_found(op.UNKNOWN_MEMBER), not_found(10004)), op.DONT_KNOW
        )


def campaign(since: int | None = None, owner: int | None = 77) -> Campaign:
    return replace(
        base_campaign("c" * 32),
        dm_user_ids=frozenset({DM, 88}),
        owner_user_id=owner,
        owner_missing_since=since,
    )


class TheBotsFlow(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = make_bot()
        self.guild = MagicMock()
        self.guild.fetch_member = AsyncMock()
        self.guild.get_member = MagicMock(return_value=None)  # not cached
        self.bot.get_guild = lambda gid: self.guild  # type: ignore[method-assign]
        self.fetch_user = AsyncMock()
        cast(Any, self.bot).fetch_user = self.fetch_user
        self.store = MagicMock()
        self.store.note_owner_presence = AsyncMock(return_value=True)
        self.store.orphan_missing_owner = AsyncMock(return_value=True)
        self.store.get = AsyncMock(side_effect=lambda g, c: None)
        self.bot.campaigns = self.store
        self.sent: list[tuple[int, str]] = []

        async def dm(user_id: int, text: str) -> bool:
            self.sent.append((user_id, text))
            return True

        self.bot._dm_user = dm  # type: ignore[method-assign]

    async def test_a_first_miss_is_noted_and_nobody_is_told(self) -> None:
        self.guild.fetch_member.side_effect = not_found(op.UNKNOWN_MEMBER)
        await self.bot.check_owner_present(campaign())
        self.store.note_owner_presence.assert_awaited_once()
        self.store.orphan_missing_owner.assert_not_awaited()
        self.assertEqual(self.sent, [])

    async def test_seen_again_clears_the_date(self) -> None:
        await self.bot.check_owner_present(campaign(since=NOW))
        self.store.note_owner_presence.assert_awaited_once()
        self.assertIsNone(self.store.note_owner_presence.await_args.kwargs["missing_since"])

    async def test_after_seven_days_it_is_ownerless_and_the_dms_are_told(self) -> None:
        import time

        self.guild.fetch_member.side_effect = not_found(op.UNKNOWN_MEMBER)
        await self.bot.check_owner_present(campaign(since=int(time.time()) - 8 * DAY))
        self.store.orphan_missing_owner.assert_awaited_once()
        self.assertEqual({u for u, _ in self.sent}, {DM, 88})
        self.assertNotIn(77, {u for u, _ in self.sent})  # the owner is gone: not told
        self.assertIn("Press Take it on in ⚙️ Manage DMbot", self.sent[0][1])

    async def test_a_deleted_account_is_ownerless_at_once(self) -> None:
        self.guild.fetch_member.side_effect = not_found(op.UNKNOWN_MEMBER)
        self.fetch_user.side_effect = not_found(op.UNKNOWN_USER)
        await self.bot.check_owner_present(campaign())
        self.store.orphan_missing_owner.assert_awaited_once()

    async def test_other_errors_change_nothing(self) -> None:
        self.guild.fetch_member.side_effect = RuntimeError("Discord is down")
        await self.bot.check_owner_present(campaign(since=NOW - 30 * DAY))
        self.store.note_owner_presence.assert_not_awaited()
        self.store.orphan_missing_owner.assert_not_awaited()

    async def test_a_cached_owner_costs_no_call_to_discord(self) -> None:
        self.guild.get_member.return_value = object()
        await self.bot.check_owner_present(campaign(since=NOW))
        self.guild.fetch_member.assert_not_awaited()
        self.store.note_owner_presence.assert_awaited_once()  # seen: the mark is cleared

    async def test_discord_is_asked_at_most_once_an_hour_for_a_campaign(self) -> None:
        self.guild.fetch_member.side_effect = not_found(op.UNKNOWN_MEMBER)
        await self.bot.check_owner_present(campaign())
        await self.bot.check_owner_present(campaign())
        self.assertEqual(self.guild.fetch_member.await_count, 1)
        await self.bot.check_owner_present(campaign(), force=True)  # the daily job
        self.assertEqual(self.guild.fetch_member.await_count, 2)

    async def test_a_start_waits_at_most_its_short_time(self) -> None:
        import asyncio

        async def slow(_: int) -> None:
            await asyncio.sleep(5)

        self.guild.fetch_member.side_effect = slow
        started = asyncio.get_running_loop().time()
        await self.bot.check_owner_present(campaign(), wait=0.05)
        self.assertLess(asyncio.get_running_loop().time() - started, 1)
        self.store.orphan_missing_owner.assert_not_awaited()

    async def test_a_campaign_with_no_owner_is_not_looked_at(self) -> None:
        await self.bot.check_owner_present(campaign(owner=None))
        self.guild.fetch_member.assert_not_awaited()


class TheStore(DatabaseTest):
    async def test_noting_clearing_and_ending(self) -> None:
        store = CampaignStore(self.db, clock=lambda: 1)
        made = await store.create(GUILD_A, "Frozen Wastes", DM)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE campaigns SET owner_user_id = 77 WHERE id = %s", (made.id,))
        self.assertTrue(await store.note_owner_presence(GUILD_A, made.id, 77, missing_since=100))
        got = await store.get(GUILD_A, made.id)
        assert got is not None
        self.assertEqual(got.owner_missing_since, 100)
        self.assertFalse(await store.note_owner_presence(GUILD_A, made.id, 99, missing_since=5))
        self.assertTrue(await store.note_owner_presence(GUILD_A, made.id, 77, missing_since=None))
        self.assertTrue(await store.orphan_missing_owner(GUILD_A, made.id, 77, 500))
        got = await store.get(GUILD_A, made.id)
        assert got is not None
        self.assertEqual(
            (got.owner_user_id, got.owner_left_at, got.owner_missing_since), (None, 500, None)
        )
        self.assertFalse(await store.orphan_missing_owner(GUILD_A, made.id, 77, 600))  # already

    async def test_an_older_look_cannot_end_an_owner_seen_again_meanwhile(self) -> None:
        store = CampaignStore(self.db, clock=lambda: 1)
        made = await store.create(GUILD_A, "Frozen Wastes", DM)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE campaigns SET owner_user_id = 77 WHERE id = %s", (made.id,))
        await store.note_owner_presence(GUILD_A, made.id, 77, missing_since=100)
        await store.note_owner_presence(GUILD_A, made.id, 77, missing_since=None)  # seen again
        self.assertFalse(
            await store.orphan_missing_owner(GUILD_A, made.id, 77, 900, still_missing_since=100)
        )
        self.assertTrue(  # a deleted account can't come back: no condition
            await store.orphan_missing_owner(GUILD_A, made.id, 77, 900, any_state=True)
        )
