"""The daily "old transcripts deleted" note goes to the DM Bot channel, the screen only when there
is none (#3874): counts only, since the campaign's people read the DM Bot channel."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.recaps.discord import RecapFeatures
from dmbot.transcript.keep import note_text


def _upkeep(campaign: SimpleNamespace) -> tuple[int | None, bool]:
    """The real rule (recaps/discord.py), applied to the stand-in campaign."""
    return RecapFeatures._upkeep_channel(None, campaign)  # type: ignore[arg-type]


def _campaign(bot_channel: int | None, screen: int | None) -> SimpleNamespace:
    return SimpleNamespace(dm_bot_channel_id=bot_channel, dm_screen_channel_id=screen)


class WhereItGoes(unittest.IsolatedAsyncioTestCase):
    async def send(self, bot_channel: int | None, screen: int | None) -> tuple[bool, AsyncMock]:
        bot = SimpleNamespace(post=AsyncMock(return_value=True), _upkeep_channel=_upkeep)
        told = await DMBot._post_upkeep(bot, _campaign(bot_channel, screen), "note")  # type: ignore[arg-type]
        return told, bot.post

    async def test_the_dm_bot_channel_and_not_the_screen(self) -> None:
        told, post = await self.send(11, 22)
        self.assertTrue(told)
        post.assert_awaited_once_with(11, "note")

    async def test_the_screen_when_there_is_no_dm_bot_channel(self) -> None:
        _, post = await self.send(None, 22)
        post.assert_awaited_once_with(22, "note")

    async def test_neither_channel_says_nothing(self) -> None:
        told, post = await self.send(None, None)
        self.assertFalse(told)
        post.assert_not_awaited()

    def test_the_note_takes_only_numbers_so_no_name_can_be_in_it(self) -> None:
        text = note_text(3, 90)
        self.assertIn("3 transcripts", text)


class PeekLines(unittest.TestCase):
    def test_the_channel_line_counts_and_never_names(self) -> None:
        from dmbot.dm_screen.peek_notices import counts_line, notice

        self.assertEqual(counts_line(1), "👀 1 player peeked behind the DM screen.")
        self.assertEqual(counts_line(3), "👀 3 players peeked behind the DM screen.")
        self.assertIn("Sam", notice("Sam"))  # (the private line is the one that names)
        self.assertNotIn("Sam", counts_line(1))


class RunningPeeks(unittest.IsolatedAsyncioTestCase):
    async def test_a_count_in_the_channel_and_each_name_only_to_the_dms(self) -> None:
        from dmbot.dm_screen.peek_notices import Peek

        campaign = SimpleNamespace(dm_user_ids={7, 8})
        bot = SimpleNamespace(
            peek_notices=SimpleNamespace(
                take=AsyncMock(return_value=[Peek("c1", "Sam"), Peek("c1", "Alex")])
            ),
            guilds=[SimpleNamespace(id=1)],
            campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
            _post_upkeep=AsyncMock(return_value=True),
            _dm_user=AsyncMock(return_value=True),
        )
        await DMBot.run_peek_notices(bot)  # type: ignore[arg-type]
        bot._post_upkeep.assert_awaited_once()
        said = bot._post_upkeep.await_args.args[1]
        self.assertIn("2 players peeked", said)
        self.assertNotIn("Sam", said)
        self.assertNotIn("Alex", said)
        sent = [call.args for call in bot._dm_user.await_args_list]
        self.assertEqual(len(sent), 4)  # (two DMs, two names)
        self.assertEqual({user for user, _ in sent}, {7, 8})
        self.assertTrue(all("Sam" in t or "Alex" in t for _, t in sent))
