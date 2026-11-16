"""A note with words the DM Bot channel can't hold (#3874): the words go to the DMs privately and
the channel gets a pointer only when a private message really went out."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from dmbot.dm_screen import pointer_note

POINTER = "📄 something is waiting"


def campaign(bot_channel: int | None = 77, screen: int | None = 99) -> SimpleNamespace:
    return SimpleNamespace(
        guild_id=1, id="c" * 32, dm_bot_channel_id=bot_channel, dm_screen_channel_id=screen
    )


def bot(private: bool, posted: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        ask_privately=AsyncMock(return_value=private),
        post_message=AsyncMock(return_value=object() if posted else None),
        post=AsyncMock(return_value=True),
    )


async def run(b: SimpleNamespace, c: SimpleNamespace, pointer: str | None = POINTER) -> bool:
    return await pointer_note.send(b, c, "the words", lambda: discord.ui.View(), pointer)


class Sending(unittest.IsolatedAsyncioTestCase):
    async def test_private_first_then_a_pointer_with_no_words_in_it(self) -> None:
        b = bot(True)
        self.assertTrue(await run(b, campaign()))
        b.post_message.assert_not_called()
        b.post.assert_awaited_once_with(77, POINTER)

    async def test_no_reachable_dm_means_the_screen_and_no_pointer(self) -> None:
        b = bot(False)
        self.assertTrue(await run(b, campaign()))
        self.assertEqual(b.post_message.await_args.args[0], 99)
        b.post.assert_not_called()

    async def test_nowhere_to_send_is_false(self) -> None:
        b = bot(False)
        self.assertFalse(await run(b, campaign(screen=None)))
        self.assertFalse(await run(bot(False, posted=False), campaign()))

    async def test_no_dm_bot_channel_or_no_pointer_posts_no_pointer(self) -> None:
        b = bot(True)
        await run(b, campaign(bot_channel=None))
        await run(b, campaign(), pointer=None)
        b.post.assert_not_called()

    async def test_a_failing_pointer_does_not_undo_the_words(self) -> None:
        b = bot(True)
        b.post = AsyncMock(side_effect=RuntimeError("down"))
        self.assertTrue(await run(b, campaign()))

    async def test_the_private_attempt_gets_a_copy_so_the_screen_view_stays_clean(self) -> None:
        b = bot(False)
        view = discord.ui.View()
        button: discord.ui.Button[discord.ui.View] = discord.ui.Button(label="Undo")
        view.add_item(button)
        await pointer_note.send(b, campaign(), "words", lambda: view, None)
        sent = b.ask_privately.await_args.args[3]
        self.assertIsNot(sent, view)
        self.assertEqual(len(b.post_message.await_args.args[2].children), 1)
