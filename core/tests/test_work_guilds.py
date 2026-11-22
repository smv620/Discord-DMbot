"""A worker works only the servers the bot is in (#5030): a server it left is skipped, since the
bot's news covers only its own servers and the AI calls would cost for nobody. Invented ids."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from dmbot.bot import DMBot
from dmbot.install_record import InstallStore
from dmbot.recaps import discord as recaps_discord
from tests.pg import DatabaseTest

HERE, LEFT, NEVER = 8101, 8102, 8103


class TheStore(DatabaseTest):
    async def test_a_server_the_bot_left_is_named_and_a_rejoin_clears_it(self) -> None:
        installs = InstallStore(self.db)
        await installs.joined(HERE)
        await installs.joined(LEFT)
        await installs.left(LEFT)
        # (NEVER has no row at all: nothing says the bot left it)
        self.assertEqual(await installs.left_among([HERE, LEFT, NEVER]), {LEFT})
        await installs.joined(LEFT)  # (a rejoin)
        self.assertEqual(await installs.left_among([HERE, LEFT, NEVER]), set())


class TheWorkersServers(IsolatedAsyncioTestCase):
    def bot(self, *, headless: bool, left: set[int] | None = None) -> Any:
        bot: Any = SimpleNamespace(
            headless=headless,
            guilds=[SimpleNamespace(id=HERE)],
            campaigns=SimpleNamespace(
                guilds_with_campaigns=AsyncMock(return_value=[HERE, LEFT, NEVER])
            ),
            installs=None
            if left is None
            else SimpleNamespace(left_among=AsyncMock(return_value=left)),
        )
        bot.work_guild_ids = lambda: DMBot.work_guild_ids(bot)
        return bot

    async def test_a_worker_skips_the_servers_the_bot_left(self) -> None:
        bot = self.bot(headless=True, left={LEFT})
        self.assertEqual(await bot.work_guild_ids(), [HERE, NEVER])

    async def test_the_answer_is_kept_for_a_short_while_then_made_again(self) -> None:
        bot = self.bot(headless=True, left={LEFT})
        await bot.work_guild_ids()
        await bot.work_guild_ids()
        bot.campaigns.guilds_with_campaigns.assert_awaited_once()
        later = time.monotonic() + recaps_discord.WORK_GUILDS_KEEP_S + 1
        with patch("dmbot.recaps.discord.time.monotonic", return_value=later):
            await bot.work_guild_ids()
        self.assertEqual(bot.campaigns.guilds_with_campaigns.await_count, 2)

    async def test_with_no_install_record_every_server_with_a_campaign_is_worked(self) -> None:
        bot = self.bot(headless=True, left=None)
        self.assertEqual(await bot.work_guild_ids(), [HERE, LEFT, NEVER])

    async def test_the_bot_works_its_own_servers_as_before(self) -> None:
        bot = self.bot(headless=False, left={LEFT})
        self.assertEqual(await bot.work_guild_ids(), [HERE])
        bot.campaigns.guilds_with_campaigns.assert_not_awaited()
