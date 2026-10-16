"""What retention does to a deleted campaign's Discord channels (#1598): the DM screen and the
game-time channel are deleted; the transcript and dmb-chat channels are closed with one note and
DMbot's own buttons removed; only the channels recorded on the campaign, in its own server; a
missing channel, a permission error or a slow one is skipped, never an error."""

from __future__ import annotations

import asyncio
import logging
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot import retention_channels as rc

GUILD, OTHER_GUILD, ME, SOMEONE = 11, 12, 99, 5
SCREEN, GAMETIME, TRANSCRIPT, CHAT = 101, 102, 103, 104
NOW = 1_700_000_000


def campaign(**ids: int | None) -> Any:
    base = {
        "guild_id": GUILD,
        "dm_screen_channel_id": SCREEN,
        "gametime_channel_id": GAMETIME,
        "transcript_channel_id": TRANSCRIPT,
        "dmbchat_channel_id": CHAT,
    }
    return SimpleNamespace(**{**base, **ids})


def message(author: int, buttons: bool, mid: int) -> Any:
    return SimpleNamespace(
        id=mid, author=SimpleNamespace(id=author), components=[object()] if buttons else [],
        edit=AsyncMock(),
    )  # fmt: skip


def channel(
    cid: int, *, guild: int = GUILD, pins: list[Any] | None = None, recent: list[Any] | None = None
) -> Any:
    ch = MagicMock(spec=discord.TextChannel)
    ch.id = cid
    ch.guild = SimpleNamespace(id=guild)
    ch.delete = AsyncMock()
    ch.send = AsyncMock()
    ch.pins = AsyncMock(return_value=list(pins or []))

    async def history(limit: int = 100) -> Any:
        for m in recent or []:
            yield m

    ch.history = history
    return ch


def guild_with(channels: dict[int, Any]) -> Any:
    async def fetch(cid: int) -> Any:
        if cid not in channels:
            raise discord.NotFound(MagicMock(status=404), "gone")
        found = channels[cid]
        if isinstance(found, Exception):
            raise found
        return found

    return SimpleNamespace(id=GUILD, fetch_channel=fetch)


class Retire(unittest.IsolatedAsyncioTestCase):
    def all_four(self) -> dict[int, Any]:
        return {cid: channel(cid) for cid in (SCREEN, GAMETIME, TRANSCRIPT, CHAT)}

    async def test_the_dm_screen_and_game_time_are_deleted_the_others_closed(self) -> None:
        chans = self.all_four()
        got = await rc.retire_channels(guild_with(chans), campaign(), ME, NOW)
        self.assertEqual((got.deleted, got.closed, got.skipped), (2, 2, 0))
        chans[SCREEN].delete.assert_awaited_once()
        chans[GAMETIME].delete.assert_awaited_once()
        for cid in (TRANSCRIPT, CHAT):
            chans[cid].delete.assert_not_awaited()  # people's words are not DMbot's to delete
            (call,) = chans[cid].send.await_args_list
            self.assertIn("This campaign was deleted on <t:1700000000:D>", call.args[0])
            self.assertIn("A server admin can delete it.", call.args[0])
            self.assertIs(call.kwargs["allowed_mentions"], rc.NO_PINGS)  # nobody is pinged

    async def test_dmbots_own_buttons_go_and_other_peoples_messages_are_untouched(self) -> None:
        mine_pinned = message(ME, True, 1)
        mine_recent = message(ME, True, 2)
        mine_plain = message(ME, False, 3)
        theirs = message(SOMEONE, True, 4)  # another bot's or a person's: never edited
        chans = self.all_four()
        chans[TRANSCRIPT] = channel(
            TRANSCRIPT, pins=[mine_pinned], recent=[mine_recent, mine_plain, theirs, mine_pinned]
        )
        await rc.retire_channels(guild_with(chans), campaign(), ME, NOW)
        mine_pinned.edit.assert_awaited_once_with(view=None)  # once, though seen twice
        mine_recent.edit.assert_awaited_once_with(view=None)
        mine_plain.edit.assert_not_awaited()
        theirs.edit.assert_not_awaited()

    async def test_a_missing_channel_is_skipped_and_the_rest_still_done(self) -> None:
        chans = self.all_four()
        del chans[SCREEN]
        with self.assertLogs("dmbot.retention_channels", logging.INFO) as logged:
            got = await rc.retire_channels(guild_with(chans), campaign(), ME, NOW)
        self.assertEqual((got.deleted, got.closed, got.skipped), (1, 2, 1))
        self.assertEqual(len(logged.records), 1)  # said once per campaign
        self.assertNotIn(str(SCREEN), logged.output[0])  # no ids in the log

    async def test_a_permission_error_is_skipped_not_raised(self) -> None:
        chans = self.all_four()
        chans[GAMETIME].delete = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), "no")
        )
        chans[CHAT].send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
        got = await rc.retire_channels(guild_with(chans), campaign(), ME, NOW)
        self.assertEqual((got.deleted, got.closed, got.skipped), (1, 1, 2))

    async def test_a_slow_channel_is_skipped(self) -> None:
        chans = self.all_four()

        async def slow(**_: Any) -> None:
            await asyncio.sleep(5)

        chans[SCREEN].delete = slow
        with patch.object(rc, "CHANNEL_TIMEOUT_S", 0.05):
            got = await rc.retire_channels(guild_with(chans), campaign(), ME, NOW)
        self.assertEqual((got.deleted, got.closed, got.skipped), (1, 2, 1))

    async def test_a_channel_of_another_server_is_never_touched(self) -> None:
        chans = self.all_four()
        chans[SCREEN] = channel(SCREEN, guild=OTHER_GUILD)
        chans[TRANSCRIPT] = channel(TRANSCRIPT, guild=OTHER_GUILD)
        got = await rc.retire_channels(guild_with(chans), campaign(), ME, NOW)
        self.assertEqual((got.deleted, got.closed, got.skipped), (1, 1, 2))
        chans[SCREEN].delete.assert_not_awaited()
        chans[TRANSCRIPT].send.assert_not_awaited()

    async def test_something_that_is_not_a_text_channel_is_left_alone(self) -> None:
        chans = self.all_four()
        chans[SCREEN] = MagicMock(spec=discord.VoiceChannel)
        chans[SCREEN].guild = SimpleNamespace(id=GUILD)
        got = await rc.retire_channels(guild_with(chans), campaign(), ME, NOW)
        self.assertEqual(got.skipped, 1)
        chans[SCREEN].delete.assert_not_called()

    async def test_only_the_recorded_channels_are_looked_at(self) -> None:
        chans = {GAMETIME: channel(GAMETIME)}
        got = await rc.retire_channels(
            guild_with(chans),
            campaign(
                dm_screen_channel_id=None, transcript_channel_id=None, dmbchat_channel_id=None
            ),
            ME,
            NOW,
        )
        self.assertEqual((got.deleted, got.closed, got.skipped), (1, 0, 0))

    async def test_a_server_the_bot_has_left_skips_everything(self) -> None:
        got = await rc.retire_channels(None, campaign(), ME, NOW)
        self.assertEqual((got.deleted, got.closed, got.skipped), (0, 0, 4))


if __name__ == "__main__":
    unittest.main()
