"""Slash commands are registered globally whatever the dev server setting says (#1767), so a new
server that adds DMbot has `/dmbot`. No Discord or database needed."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.bot import DMBot
from dmbot.install import welcome


def discord_not_found(_id: int) -> Any:
    import discord

    raise discord.NotFound(MagicMock(status=404), "unknown guild")


def fake_bot(
    dev_guild: int | None, shards: tuple[int, ...] = (0,), clear: tuple[int, ...] = ()
) -> Any:
    bot = MagicMock()
    bot.settings = SimpleNamespace(
        dev_guild_id=dev_guild, shards=SimpleNamespace(ids=shards), clear_guild_commands=clear
    )
    bot.tree.sync = AsyncMock(return_value=[object()] * 3)
    bot.get_guild = MagicMock(return_value=None)
    bot._guild_name = lambda gid: DMBot._guild_name(bot, gid)
    bot.fetch_guild = AsyncMock(side_effect=discord_not_found)
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

    async def test_the_listed_servers_old_copy_is_cleared_and_never_the_dev_server(self) -> None:
        bot = fake_bot(123, clear=(555, 123, 777))
        await DMBot._sync_commands(bot)
        cleared = [c.kwargs["guild"].id for c in bot.tree.clear_commands.call_args_list]
        self.assertEqual(cleared, [555, 777])
        synced = [c.kwargs["guild"].id for c in bot.tree.sync.await_args_list if c.kwargs]
        self.assertEqual(synced, [123, 555, 777])  # the dev copy, then each cleared server
        self.assertEqual(bot.tree.sync.await_args_list[0].kwargs, {})  # global first

    async def test_nothing_is_cleared_when_none_are_listed_or_off_shard_zero(self) -> None:
        bot = fake_bot(None)
        await DMBot._sync_commands(bot)
        bot.tree.clear_commands.assert_not_called()
        other = fake_bot(None, shards=(2,), clear=(555,))
        await DMBot._sync_commands(other)
        other.tree.clear_commands.assert_not_called()

    async def test_one_server_failing_does_not_stop_the_next(self) -> None:
        import discord

        bot = fake_bot(None, clear=(555, 777))
        bot.tree.sync = AsyncMock(
            side_effect=[None, discord.HTTPException(MagicMock(status=500), "x"), None]
        )
        await DMBot._sync_commands(bot)
        self.assertEqual(bot.tree.sync.await_count, 3)

    async def test_only_the_pod_with_shard_zero_syncs(self) -> None:
        for dev in (None, 123):
            bot = fake_bot(dev, shards=(1, 2))
            await DMBot._sync_commands(bot)
            bot.tree.sync.assert_not_awaited()


class Logs(unittest.IsolatedAsyncioTestCase):
    """What the log says about the sync, so a deploy can be checked from it alone (#1785)."""

    async def run_sync(self, bot: Any) -> list[str]:
        with self.assertLogs("dmbot.bot", level="INFO") as caught:
            await DMBot._sync_commands(bot)
        return [r.getMessage() for r in caught.records]

    async def test_one_line_says_how_many_global_commands_and_whether_the_dev_copy(self) -> None:
        plain = await self.run_sync(fake_bot(None))
        self.assertIn("Slash commands: 3 global commands registered", plain)
        dev = await self.run_sync(fake_bot(123))
        self.assertIn(
            "Slash commands: 3 global commands registered and copied to the dev server", dev
        )

    async def test_a_cleared_server_is_logged_with_its_name_before_and_after(self) -> None:
        bot = fake_bot(None, clear=(555,))
        bot.get_guild = MagicMock(return_value=SimpleNamespace(name="Frostmaiden Table"))
        lines = await self.run_sync(bot)
        before = "Clearing the old per-server commands in 555 (Frostmaiden Table)"
        self.assertIn(before, lines)
        self.assertIn("Server 555: old per-server commands removed", lines)
        self.assertLess(
            lines.index(before), lines.index("Server 555: old per-server commands removed")
        )

    async def test_a_server_found_only_by_asking_discord_is_named_too(self) -> None:
        bot = fake_bot(None, clear=(555,))
        bot.fetch_guild = AsyncMock(return_value=SimpleNamespace(name="Asked Table"))
        self.assertIn(
            "Clearing the old per-server commands in 555 (Asked Table)", await self.run_sync(bot)
        )

    async def test_a_server_the_bot_is_not_in_is_said_and_still_cleared(self) -> None:
        bot = fake_bot(None, clear=(555,))
        lines = await self.run_sync(bot)
        self.assertIn("Clearing the old per-server commands in 555 (not in this server)", lines)
        self.assertIn("Server 555: old per-server commands removed", lines)
        bot.tree.clear_commands.assert_called_once()

    async def test_the_dev_server_in_the_list_warns_and_is_skipped(self) -> None:
        bot = fake_bot(123, clear=(123,))
        with self.assertLogs("dmbot.bot", level="INFO") as caught:
            await DMBot._sync_commands(bot)
        warnings = [r.getMessage() for r in caught.records if r.levelname == "WARNING"]
        self.assertEqual(warnings, ["Not clearing the commands in 123: it is DISCORD_DEV_GUILD_ID"])
        bot.tree.clear_commands.assert_not_called()


class Welcome(unittest.TestCase):
    def test_the_welcome_says_what_to_do_if_the_command_is_not_there_yet(self) -> None:
        self.assertIn("wait a minute and try again", welcome())
