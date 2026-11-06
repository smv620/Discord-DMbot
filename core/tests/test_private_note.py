"""The private-first send for suggestion notes (#3210): private chat, then the DM screen."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.dm_screen import private_note


def campaign(screen: int | None = 55) -> Any:
    return SimpleNamespace(guild_id=1, id="c" * 32, dm_screen_channel_id=screen)


class Sending(unittest.IsolatedAsyncioTestCase):
    async def test_the_private_chat_comes_first_and_the_screen_is_left_alone(self) -> None:
        bot = SimpleNamespace(ask_privately=AsyncMock(return_value=True), post_message=AsyncMock())
        self.assertTrue(await private_note.send(bot, campaign(), "note", lambda: object()))  # type: ignore[arg-type,return-value]
        bot.post_message.assert_not_awaited()

    async def test_the_screen_is_the_fallback_with_the_reason_and_its_own_view(self) -> None:
        views: list[object] = []

        def make() -> Any:
            views.append(object())
            return views[-1]

        bot = SimpleNamespace(
            ask_privately=AsyncMock(return_value=False),
            post_message=AsyncMock(return_value=object()),
        )
        self.assertTrue(await private_note.send(bot, campaign(), "note", make))
        channel, text, view = bot.post_message.await_args.args
        self.assertEqual(channel, 55)
        self.assertTrue(text.startswith("note"))
        self.assertIn("couldn't message you privately", text)
        self.assertEqual(len(views), 2)  # one view for each place it was sent
        self.assertIs(view, views[1])

    async def test_nowhere_to_send_is_false(self) -> None:
        bot = SimpleNamespace(ask_privately=AsyncMock(return_value=False), post_message=AsyncMock())
        self.assertFalse(await private_note.send(bot, campaign(None), "n", lambda: object()))  # type: ignore[arg-type,return-value]
        bot = SimpleNamespace(
            ask_privately=AsyncMock(return_value=False), post_message=AsyncMock(return_value=None)
        )
        self.assertFalse(await private_note.send(bot, campaign(), "n", lambda: object()))  # type: ignore[arg-type,return-value]


if __name__ == "__main__":
    unittest.main()
