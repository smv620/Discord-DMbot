"""The daily "old transcripts deleted" note goes to the DM Bot channel, the screen only when there
is none (#3874): counts only, since the campaign's people read the DM Bot channel."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.transcript.keep import note_text


def _campaign(bot_channel: int | None, screen: int | None) -> SimpleNamespace:
    return SimpleNamespace(dm_bot_channel_id=bot_channel, dm_screen_channel_id=screen)


class WhereItGoes(unittest.IsolatedAsyncioTestCase):
    async def send(self, bot_channel: int | None, screen: int | None) -> tuple[bool, AsyncMock]:
        bot = SimpleNamespace(post=AsyncMock(return_value=True))
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
