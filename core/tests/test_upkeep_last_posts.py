"""The last post-game notices go where upkeep goes (#3874): the DM Bot channel, counts and fixed
words only (it is read by the campaign's people), else the DM screen as before. Invented ids."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.recaps.discord import RecapFeatures
from dmbot.ui import recap_audio

SCREEN, BOT = 111, 222
TITLE = "The Silver Lock"


def fake(*, dm_bot: int | None) -> Any:
    campaign = SimpleNamespace(
        guild_id=5, id="c" * 32, dm_screen_channel_id=SCREEN, dm_bot_channel_id=dm_bot
    )
    me = SimpleNamespace(
        post=AsyncMock(),
        post_message=AsyncMock(return_value=None),
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
        recaps=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(title=TITLE))),
        audio_waits=SimpleNamespace(finish=AsyncMock()),
        _audio_seen={},
        settings=SimpleNamespace(site_url=""),
        _recap_refusal=AsyncMock(return_value="you've used this month's recaps; next on 3 Dec."),
    )
    me._upkeep_channel = lambda c: RecapFeatures._upkeep_channel(me, c)
    return me, campaign


class TheNotices(unittest.IsolatedAsyncioTestCase):
    async def test_the_audio_notice_goes_to_the_dm_bot_channel_with_no_title(self) -> None:
        me, _ = fake(dm_bot=BOT)
        await RecapFeatures.recap_audio_told(me, 5, "c" * 32, 100, "ready")
        channel, text = me.post.await_args.args[:2]
        self.assertEqual(channel, BOT)
        self.assertNotIn(TITLE, text)
        self.assertIn("this session's recap", text)

    async def test_with_no_dm_bot_channel_the_dm_screen_gets_it_with_the_title(self) -> None:
        me, _ = fake(dm_bot=None)
        await RecapFeatures.recap_audio_told(me, 5, "c" * 32, 100, "failed")
        channel, text = me.post.await_args.args[:2]
        self.assertEqual(channel, SCREEN)
        self.assertIn(f"**{TITLE}**", text)

    async def test_the_allowance_refusal_goes_to_the_dm_bot_channel(self) -> None:
        me, _ = fake(dm_bot=BOT)
        got = await RecapFeatures._recap_allowance(me, 5, "c" * 32, audio=False)
        self.assertIn("next on", got or "")
        self.assertEqual(me.post.await_args.args[0], BOT)

    async def test_making_the_audio_is_said_in_the_same_place(self) -> None:
        me, campaign = fake(dm_bot=BOT)
        me.audio_waits = _Waits()
        await RecapFeatures.recap_audio_started(me, campaign, 100)
        self.assertEqual(me.post_message.await_args.args[0], BOT)
        self.assertEqual(me.post_message.await_args.args[1], recap_audio.MAKING)


class _Waits:
    def __contains__(self, _key: object) -> bool:
        return False

    async def start(self, *_a: object) -> None:
        return None
