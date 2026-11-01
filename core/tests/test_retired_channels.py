"""The rules, NPC and game-time channels DMbot no longer makes (#2874, #2877): the ones it made
before are deleted once, only when they are its own (stored id and DMbot's name), and the ids are
cleared. Channels here are stand-ins; names and numbers are made up."""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.bot import DMBot
from dmbot.dm_screen import buttons, messages
from dmbot.dm_screen import retired_channels as rc

GUILD = 5
RULES, NPCS, GAMETIME = 601, 602, 603


def channel(channel_id: int, name: str, *, guild_id: int = GUILD, fail: bool = False) -> Any:
    chan = MagicMock(spec=discord.TextChannel)
    chan.id, chan.name = channel_id, name
    chan.guild = SimpleNamespace(id=guild_id)
    if fail:
        chan.delete = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
    else:
        chan.delete = AsyncMock()
    return chan


def guild_with(*chans: Any) -> Any:
    found = {c.id: c for c in chans}
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD
    guild.get_channel = MagicMock(side_effect=lambda cid: found.get(cid))

    async def fetch(cid: int) -> Any:
        if cid in found:
            return found[cid]
        raise discord.NotFound(MagicMock(status=404), "gone")

    guild.fetch_channel = fetch
    return guild


def campaign(
    rules: int | None = RULES, npcs: int | None = NPCS, gametime: int | None = None
) -> Any:
    return SimpleNamespace(
        id="c" * 32, rules_channel_id=rules, npcs_channel_id=npcs, gametime_channel_id=gametime
    )


def store() -> Any:
    return SimpleNamespace(
        set_rules_channel=AsyncMock(),
        set_npcs_channel=AsyncMock(),
        set_gametime_channel=AsyncMock(),
    )


class Removing(unittest.IsolatedAsyncioTestCase):
    async def test_a_stored_channel_with_dmbots_name_is_deleted_and_its_id_cleared(self) -> None:
        rules, npcs = channel(RULES, "dmb-rules-frostmaiden"), channel(NPCS, "dmb-npcs-frostmaiden")
        campaigns = store()
        got = await rc.remove_old_channels(guild_with(rules, npcs), campaign(), campaigns)
        self.assertEqual((got.deleted, got.kept, got.failed), (2, 0, 0))
        rules.delete.assert_awaited_once_with(reason=rc.REASON)  # one audit-log line
        npcs.delete.assert_awaited_once_with(reason=rc.REASON)
        campaigns.set_rules_channel.assert_awaited_once_with(GUILD, "c" * 32, None)
        campaigns.set_npcs_channel.assert_awaited_once_with(GUILD, "c" * 32, None)

    async def test_the_stored_game_time_channel_is_deleted_and_a_renamed_one_kept(self) -> None:
        own, other = channel(GAMETIME, "dmb-gametime-frostmaiden"), channel(604, "table-clock")
        campaigns = store()
        got = await rc.remove_old_channels(
            guild_with(own, other), campaign(None, None, GAMETIME), campaigns
        )
        self.assertEqual((got.deleted, got.kept), (1, 0))
        own.delete.assert_awaited_once_with(reason=rc.REASON)
        campaigns.set_gametime_channel.assert_awaited_once_with(GUILD, "c" * 32, None)
        campaigns = store()
        got = await rc.remove_old_channels(
            guild_with(own, other), campaign(None, None, 604), campaigns
        )
        self.assertEqual((got.deleted, got.kept), (0, 1))
        other.delete.assert_not_awaited()
        campaigns.set_gametime_channel.assert_awaited_once_with(GUILD, "c" * 32, None)

    async def test_a_renamed_channel_is_kept_and_its_id_cleared(self) -> None:
        rules, npcs = channel(RULES, "house-rules-talk"), channel(NPCS, "npc-talk")
        campaigns = store()
        got = await rc.remove_old_channels(guild_with(rules, npcs), campaign(), campaigns)
        self.assertEqual((got.deleted, got.kept), (0, 2))
        rules.delete.assert_not_awaited()
        npcs.delete.assert_not_awaited()
        campaigns.set_rules_channel.assert_awaited_once()
        campaigns.set_npcs_channel.assert_awaited_once()

    async def test_a_channel_of_another_server_is_never_deleted(self) -> None:
        rules = channel(RULES, "dmb-rules-frostmaiden", guild_id=999)
        got = await rc.remove_old_channels(guild_with(rules), campaign(npcs=None), store())
        self.assertEqual((got.deleted, got.kept), (0, 1))
        rules.delete.assert_not_awaited()

    async def test_a_channel_that_is_already_gone_is_only_forgotten(self) -> None:
        campaigns = store()
        got = await rc.remove_old_channels(guild_with(), campaign(), campaigns)
        self.assertEqual((got.deleted, got.kept, got.failed), (0, 0, 0))
        campaigns.set_rules_channel.assert_awaited_once()
        campaigns.set_npcs_channel.assert_awaited_once()

    async def test_a_failed_delete_is_counted_the_other_goes_on_and_both_ids_are_cleared(
        self,
    ) -> None:
        rules = channel(RULES, "dmb-rules-frostmaiden", fail=True)
        npcs = channel(NPCS, "dmb-npcs-frostmaiden")
        campaigns = store()
        with self.assertLogs("dmbot.dm_screen.retired_channels", "INFO") as logged:
            got = await rc.remove_old_channels(guild_with(rules, npcs), campaign(), campaigns)
        self.assertEqual((got.deleted, got.failed), (1, 1))
        npcs.delete.assert_awaited_once()
        campaigns.set_rules_channel.assert_awaited_once()
        self.assertEqual(
            logged.records[0].getMessage(),
            "Old rules, NPC and game-time channels: 1 deleted, 0 kept, 1 failed",
        )

    async def test_nothing_is_deleted_by_name_alone(self) -> None:
        lookalike = channel(777, "dmb-rules-frostmaiden")  # DMbot's kind of name, but not stored
        guild = guild_with(lookalike)
        got = await rc.remove_old_channels(guild, campaign(None, None), store())
        self.assertEqual((got.deleted, got.kept, got.failed), (0, 0, 0))
        lookalike.delete.assert_not_awaited()
        guild.get_channel.assert_not_called()

    async def test_the_names_that_say_a_channel_is_dmbots_own(self) -> None:
        self.assertTrue(rc.is_rules_name("dmb-rules-rmfthfrstmdn"))
        self.assertTrue(rc.is_npcs_name("dmb-npcs-rmfthfrstmdn"))
        self.assertFalse(rc.is_rules_name("rules"))
        self.assertFalse(rc.is_npcs_name("dmb-rules-x"))


class TheBot(unittest.IsolatedAsyncioTestCase):
    def test_a_start_no_longer_makes_either_channel(self) -> None:
        self.assertFalse(hasattr(DMBot, "_rules_channel"))
        self.assertFalse(hasattr(DMBot, "_npcs_channel"))
        self.assertFalse(hasattr(DMBot, "_gametime_channel"))
        source = inspect.getsource(DMBot)  # nothing in the bot sets either one up
        self.assertNotIn("setup_rules_channel", source)
        self.assertNotIn("setup_npcs_channel", source)
        self.assertNotIn("setup_gametime_channel", source)
        root = Path(__file__).resolve().parents[1] / "src" / "dmbot" / "dm_screen"
        self.assertFalse((root / "rules_channel.py").exists())
        self.assertFalse((root / "npcs_channel.py").exists())
        self.assertFalse((root / "gametime_channel.py").exists())

    def test_nothing_follows_the_screen_into_a_rules_or_npc_channel_any_more(self) -> None:
        src = Path(__file__).resolve().parents[1] / "src" / "dmbot" / "dm_screen"
        for name in ("channel.py", "buttons.py", "game_masters.py"):
            text = (src / name).read_text()
            self.assertNotIn("rules_channel", text, name)
            self.assertNotIn("npcs_channel", text, name)
            self.assertNotIn("gametime_channel", text, name)

    async def test_the_peek_warning_names_no_other_channel(self) -> None:
        self.assertFalse(hasattr(messages, "peek_warning"))
        self.assertNotIn("House rules", messages.PEEK_WARNING)
        self.assertNotIn("People and groups", messages.PEEK_WARNING)
        self.assertFalse(hasattr(buttons, "_controlled"))

    async def test_a_failure_removing_old_channels_never_stops_a_start(self) -> None:
        bot: Any = SimpleNamespace(campaigns=SimpleNamespace())
        from unittest.mock import patch

        boom = AsyncMock(side_effect=RuntimeError("x"))
        with (
            patch("dmbot.bot.remove_old_channels", boom),
            self.assertLogs("dmbot.bot", "WARNING") as logged,
        ):
            await DMBot._retire_old_channels(bot, MagicMock(), campaign())
        self.assertIn("RuntimeError", logged.output[0])
        # and a campaign with no old channel costs nothing
        with patch("dmbot.bot.remove_old_channels", AsyncMock()) as called:
            await DMBot._retire_old_channels(bot, MagicMock(), campaign(None, None))
        called.assert_not_awaited()
