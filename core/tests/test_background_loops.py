"""The background loops keep going when one campaign or one server fails (#3950; part of #3891 and
#3901): channel sync and the automatic scripts. Each loop runs exactly one pass: the sleep after
the pass raises CancelledError, which ends it. Invented ids, no database."""

from __future__ import annotations

import asyncio
import unittest
from functools import partial
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.bot import DMBot

GUILD_A, GUILD_B = 1001, 1002
CAMPAIGN_1, CAMPAIGN_2 = "a" * 32, "b" * 32


def server(guild_id: int) -> Any:
    return SimpleNamespace(id=guild_id)


def campaign(campaign_id: str) -> Any:
    return SimpleNamespace(id=campaign_id)


def stand_in(guilds: list[Any], campaigns: Any = None, recaps: Any = None) -> Any:
    return SimpleNamespace(
        guilds=guilds,
        campaigns=SimpleNamespace(list_campaigns=AsyncMock(return_value=campaigns or [])),
        memory=object(),
        recaps=recaps,
    )


class LoopsKeepGoing(unittest.IsolatedAsyncioTestCase):
    async def test_one_campaign_failing_does_not_stop_channel_sync(self) -> None:
        me = stand_in([server(GUILD_A)], [campaign(CAMPAIGN_1), campaign(CAMPAIGN_2)])
        reconcile = AsyncMock(side_effect=[RuntimeError("x"), None])
        # first pause: the start delay; then the pause after the first campaign's look (the
        # second campaign's look is not stopped by the first's failure); then the hourly pause
        sleep = AsyncMock(side_effect=[None, None, asyncio.CancelledError()])
        with (
            patch("dmbot.bot.channel_sync.reconcile", reconcile),
            patch("dmbot.bot.asyncio.sleep", sleep),
            self.assertRaises(asyncio.CancelledError),
        ):
            await partial(DMBot._channel_sync_loop, me)()
        reconciled = [call.args[1].id for call in reconcile.await_args_list]
        self.assertEqual(reconciled, [CAMPAIGN_1, CAMPAIGN_2])  # the second was still looked at

    async def test_one_server_failing_does_not_stop_channel_sync(self) -> None:
        me = stand_in([server(GUILD_A), server(GUILD_B)], [campaign(CAMPAIGN_1)])
        me.campaigns.list_campaigns = AsyncMock(
            side_effect=[RuntimeError("x"), [campaign(CAMPAIGN_1)]]
        )
        reconcile = AsyncMock(return_value=None)
        sleep = AsyncMock(side_effect=[None, None, asyncio.CancelledError()])
        with (
            patch("dmbot.bot.channel_sync.reconcile", reconcile),
            patch("dmbot.bot.asyncio.sleep", sleep),
            self.assertRaises(asyncio.CancelledError),
        ):
            await partial(DMBot._channel_sync_loop, me)()
        reconcile.assert_awaited_once()
        self.assertEqual([call.args[0].id for call in reconcile.await_args_list], [GUILD_B])

    async def test_one_server_failing_does_not_stop_the_auto_script_round(self) -> None:
        me = stand_in([server(GUILD_A), server(GUILD_B)])
        run_round = AsyncMock(side_effect=[RuntimeError("x"), 0])
        sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])
        with (
            patch("dmbot.bot.auto_script.run_round", run_round),
            patch("dmbot.bot.asyncio.sleep", sleep),
            self.assertRaises(asyncio.CancelledError),
        ):
            await partial(DMBot._auto_script_loop, me)()
        self.assertEqual(run_round.await_count, 2)
        self.assertEqual([call.args[1] for call in run_round.await_args_list], [GUILD_A, GUILD_B])

    async def test_the_recap_wait_is_switched_on_when_the_script_loop_starts_and_not_without_recaps(
        self,
    ) -> None:
        recaps = SimpleNamespace(script_wait=False)
        me = stand_in([], recaps=recaps)
        with (
            patch("dmbot.bot.asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError())),
            self.assertRaises(asyncio.CancelledError),
        ):
            await partial(DMBot._auto_script_loop, me)()
        self.assertTrue(recaps.script_wait)

        me.recaps = None  # no recaps: the loop still starts and does not raise
        with (
            patch("dmbot.bot.asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError())),
            self.assertRaises(asyncio.CancelledError),
        ):
            await partial(DMBot._auto_script_loop, me)()
