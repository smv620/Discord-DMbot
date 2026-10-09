"""How the bot hands the DM sidebar its messages and keeps its rules (#935). No database:
the stores are stand-ins."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.audio.segmenter import Segmenter
from dmbot.bot import DMBot, Table
from dmbot.config import Settings
from dmbot.transcript.models import SIDEBAR_QUESTION, Line

GUILD, DM, PLAYER = 1, 7, 8


def make_bot(*, transcripts: object | None = None) -> DMBot:
    return DMBot(
        Settings(discord_token="t", ears_secret="s"),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        transcripts=transcripts,  # type: ignore[arg-type]
    )


def make_table() -> Table:
    return Table(
        guild_id=GUILD,
        voice_channel_id=2,
        screen_channel_id=3,
        dm_user_id=DM,
        segmenter=Segmenter(GUILD),
        campaign_id="c1",
        campaign_name="Frostmaiden",
        dm_user_ids=frozenset({DM}),
    )


class Glue(unittest.IsolatedAsyncioTestCase):
    def test_only_the_private_message_intent_is_added(self) -> None:
        bot = make_bot()
        self.assertTrue(bot.intents.dm_messages)
        self.assertFalse(bot.intents.message_content)  # DMs need no special permission
        self.assertFalse(bot.intents.guild_messages)

    async def test_a_private_message_goes_to_the_sidebar_and_a_failure_is_survived(self) -> None:
        bot = make_bot()
        bot.sidebar.on_dm_message = AsyncMock()  # type: ignore[method-assign]
        message = object()
        await bot.on_message(message)  # type: ignore[arg-type]
        bot.sidebar.on_dm_message.assert_awaited_once_with(message)
        bot.sidebar.on_dm_message.side_effect = RuntimeError("boom")
        await bot.on_message(message)  # type: ignore[arg-type]  # logged, not raised

    def test_sidebar_lines_are_saved_only_for_the_running_session(self) -> None:
        bot = make_bot(transcripts=MagicMock())
        table = make_table()
        line = Line(1, DM, "q", "q", sidebar=SIDEBAR_QUESTION)
        bot.sidebar_save(table, line)  # not a running session
        self.assertEqual(len(table.unsaved), 0)
        bot.tables[GUILD] = table
        bot.sidebar_save(table, line)
        self.assertEqual(len(table.unsaved), 1)
        bot.sidebar_save(make_table(), line)  # a different (stopped) session's table
        self.assertEqual(len(table.unsaved), 1)

    def test_without_stored_transcripts_nothing_is_kept(self) -> None:
        bot = make_bot(transcripts=None)
        table = make_table()
        bot.tables[GUILD] = table
        bot.sidebar_save(table, Line(1, DM, "q", "q", sidebar=SIDEBAR_QUESTION))
        self.assertEqual(len(table.unsaved), 0)

    def test_stopping_the_recording_of_someone_clears_what_they_said_for_the_scene(self) -> None:
        bot = make_bot()
        table = make_table()
        bot.tables[GUILD] = table
        table.recent.add(PLAYER, "I draw my bow.", 1.0)
        table.recent.add(DM, "The goblin ducks.", 2.0)
        bot.stop_recording(GUILD, PLAYER)
        self.assertEqual([u for _, u, _ in table.recent.lines], [DM])

    async def test_a_private_message_that_is_closed_is_told_apart(self) -> None:
        bot = make_bot()
        user = SimpleNamespace(
            send=AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
        )
        bot.get_user = lambda user_id: user  # type: ignore[method-assign,assignment,return-value]
        self.assertEqual(await bot.sidebar_send_dm(DM, "hi"), "forbidden")
        user.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500), "oops"))
        self.assertEqual(await bot.sidebar_send_dm(DM, "hi"), "failed")
        user.send = AsyncMock()
        self.assertEqual(await bot.sidebar_send_dm(DM, "hi"), "sent")
        mentions = user.send.await_args.kwargs["allowed_mentions"]
        self.assertEqual(mentions.to_dict(), discord.AllowedMentions.none().to_dict())
