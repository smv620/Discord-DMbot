"""Slash commands are registered globally whatever the dev server setting says (#1767), so a new
server that adds DMbot has `/dmbot`. No Discord or database needed."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.bot import DMBot
from dmbot.install import welcome


def fake_bot(dev_guild: int | None, shards: tuple[int, ...] = (0,)) -> Any:
    bot = MagicMock()
    bot.settings = SimpleNamespace(dev_guild_id=dev_guild, shards=SimpleNamespace(ids=shards))
    bot.tree.sync = AsyncMock()
    return bot


class Sync(unittest.IsolatedAsyncioTestCase):
    async def test_with_a_dev_server_both_the_global_and_the_guild_sync_run(self) -> None:
        bot = fake_bot(123)
        await DMBot._sync_commands(bot)
        calls = bot.tree.sync.await_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].kwargs, {})  # global
        self.assertEqual(calls[1].kwargs["guild"].id, 123)
        self.assertEqual(bot.tree.copy_global_to.call_args.kwargs["guild"].id, 123)

    async def test_without_a_dev_server_only_the_global_sync_runs(self) -> None:
        bot = fake_bot(None)
        await DMBot._sync_commands(bot)
        bot.tree.sync.assert_awaited_once_with()
        bot.tree.copy_global_to.assert_not_called()

    async def test_a_failed_global_sync_still_does_the_dev_server(self) -> None:
        import discord

        bot = fake_bot(123)
        bot.tree.sync = AsyncMock(
            side_effect=[discord.HTTPException(MagicMock(status=429), "slow down"), None]
        )
        await DMBot._sync_commands(bot)
        self.assertEqual(bot.tree.sync.await_count, 2)

    async def test_only_the_pod_with_shard_zero_syncs(self) -> None:
        for dev in (None, 123):
            bot = fake_bot(dev, shards=(1, 2))
            await DMBot._sync_commands(bot)
            bot.tree.sync.assert_not_awaited()


class Welcome(unittest.TestCase):
    def test_the_welcome_says_what_to_do_if_the_command_is_not_there_yet(self) -> None:
        self.assertIn("wait a minute and try again", welcome())
