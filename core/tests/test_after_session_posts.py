"""The after-session counts go to the DM Bot channel (#3874, group E1, part a): quiet story
threads, private scenes and continuity heads-ups. Each is a count and a button only the DM can use,
so the campaign's people, who read that channel, never see a name. With no DM Bot channel the note
goes to the DM screen as before. Stand-in bots, no database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.ui import continuity as continuity_ui
from dmbot.ui import private_scenes_list
from dmbot.ui import threads as threads_ui

GUILD, SCREEN, UPKEEP = 1, 50, 60


def bot(*, dm_bot_channel: int | None) -> Any:
    campaign = SimpleNamespace(dm_bot_channel_id=dm_bot_channel, dm_screen_channel_id=SCREEN)
    fake: Any = SimpleNamespace(
        post=AsyncMock(return_value=True),
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
    )
    fake._upkeep_channel = lambda c: DMBot._upkeep_channel(fake, c)
    return fake


def table() -> Any:
    return SimpleNamespace(guild_id=GUILD, campaign_id="c" * 32, screen_channel_id=SCREEN)


class TheNote(unittest.IsolatedAsyncioTestCase):
    async def test_it_goes_to_the_dm_bot_channel_when_the_campaign_has_one(self) -> None:
        fake = bot(dm_bot_channel=UPKEEP)
        view: Any = object()
        self.assertTrue(await DMBot._post_after_session(fake, table(), "3 things", view))
        fake.post.assert_awaited_once_with(UPKEEP, "3 things", view)

    async def test_it_goes_to_the_dm_screen_when_there_is_no_dm_bot_channel(self) -> None:
        fake = bot(dm_bot_channel=None)
        await DMBot._post_after_session(fake, table(), "3 things")
        fake.post.assert_awaited_once_with(SCREEN, "3 things", None)

    async def test_with_no_campaign_it_falls_back_to_the_tables_screen(self) -> None:
        fake = bot(dm_bot_channel=UPKEEP)
        fake.campaigns.get = AsyncMock(return_value=None)
        await DMBot._post_after_session(fake, table(), "x")
        fake.post.assert_awaited_once_with(SCREEN, "x", None)

    async def test_with_no_channel_at_all_nothing_is_posted_and_it_says_so(self) -> None:
        fake = bot(dm_bot_channel=None)
        fake.campaigns.get = AsyncMock(return_value=None)
        gone: Any = SimpleNamespace(guild_id=GUILD, campaign_id="c" * 32, screen_channel_id=None)
        self.assertFalse(await DMBot._post_after_session(fake, gone, "x"))
        fake.post.assert_not_awaited()


class TheWords(unittest.TestCase):
    """What goes into a channel the campaign's people read: counts, never what or who."""

    def test_quiet_threads_say_how_many_and_how_long(self) -> None:
        text = threads_ui.waiting_text(3, 4, 1)
        self.assertIn("3", text)
        self.assertNotIn("<@", text)

    def test_continuity_says_how_many_and_never_what(self) -> None:
        text = continuity_ui.waiting_text(2, 1)
        self.assertIn("2 heads-ups", text)
        self.assertIn("1 goes against a pin", text)

    def test_private_scenes_keep_only_the_times(self) -> None:
        self.assertIn("never the words", private_scenes_list.notice(2))


class TheDidTheyMeanCount(unittest.IsolatedAsyncioTestCase):
    """(#3874 E1) the "Did they mean…?" count goes with the others"""

    async def test_the_count_goes_through_the_after_session_post_with_its_button(self) -> None:
        store = SimpleNamespace(
            save=AsyncMock(), count=AsyncMock(return_value=2), forget_speaker=AsyncMock()
        )
        fake: Any = SimpleNamespace(
            heard_questions=store,
            consent=SimpleNamespace(consenting=AsyncMock(return_value={7})),
            _post_after_session=AsyncMock(return_value=True),
        )
        asked = SimpleNamespace(speaker=7, started_ms=1, heard="Zed", options=[("e1", "Zed")])
        tbl = SimpleNamespace(
            guild_id=GUILD,
            campaign_id="c" * 32,
            transcript_session_id="s1",
            unanswered=[asked],
            screen_channel_id=SCREEN,
        )
        await DMBot.save_unanswered(fake, tbl)  # type: ignore[arg-type]
        fake._post_after_session.assert_awaited_once()
        args = fake._post_after_session.await_args.args
        self.assertIs(args[0], tbl)
        self.assertIn("2", args[1])
        self.assertNotIn("Zed", args[1])  # (a count: never the word that was heard)
        self.assertIsNotNone(args[2])  # (the DM-only review button)
