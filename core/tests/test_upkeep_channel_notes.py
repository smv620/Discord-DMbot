"""The optional-rule and level-changed notes go to the DM Bot channel, the screen only when there
is none (#3874, group E3): the DM screen is for live play."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.dm_screen import messages as screen_messages
from dmbot.dm_screen import upkeep
from dmbot.rules import optional


def _campaign(bot_channel: int | None, screen: int | None) -> SimpleNamespace:
    return SimpleNamespace(
        dm_bot_channel_id=bot_channel,
        dm_screen_channel_id=screen,
        guild_id=1,
        id="c" * 32,
        dm_screen_level="quiet",
    )


class Channel(unittest.TestCase):
    def test_the_dm_bot_channel_first_then_the_screen_then_nothing(self) -> None:
        self.assertEqual(upkeep.channel_for(_campaign(11, 22)), 11)  # type: ignore[arg-type]
        self.assertEqual(upkeep.channel_for(_campaign(None, 22)), 22)  # type: ignore[arg-type]
        self.assertIsNone(upkeep.channel_for(_campaign(None, None)))  # type: ignore[arg-type]


class Notes(unittest.IsolatedAsyncioTestCase):
    def bot(self, campaign: SimpleNamespace) -> SimpleNamespace:
        return SimpleNamespace(
            post=AsyncMock(return_value=True),
            campaigns=SimpleNamespace(
                set_optional_rule=AsyncMock(),
                set_dm_screen_level=AsyncMock(return_value=campaign),
            ),
            session_lock=lambda guild_id: _NoLock(),
            tables={},
            _ending={},
        )

    async def test_an_optional_rule_note_goes_to_the_dm_bot_channel(self) -> None:
        campaign = _campaign(11, 22)
        bot = self.bot(campaign)
        some = optional.CATALOG[0]  # (any rule: the line is built from its name)
        ok = await DMBot.apply_optional_rule(bot, campaign, some, True, 5)  # type: ignore[arg-type]
        self.assertTrue(ok)
        self.assertEqual(bot.post.await_args.args[0], 11)

    async def test_an_optional_rule_note_falls_back_to_the_screen_or_says_nothing(self) -> None:
        some = optional.CATALOG[0]
        screen_only = _campaign(None, 22)
        bot = self.bot(screen_only)
        await DMBot.apply_optional_rule(bot, screen_only, some, False, 5)  # type: ignore[arg-type]
        self.assertEqual(bot.post.await_args.args[0], 22)
        neither = _campaign(None, None)
        quiet = self.bot(neither)
        self.assertTrue(
            await DMBot.apply_optional_rule(quiet, neither, some, True, 5)  # type: ignore[arg-type]
        )
        quiet.post.assert_not_awaited()

    async def test_a_failed_post_is_false_and_the_note_holds_only_the_rule_and_a_mention(
        self,
    ) -> None:
        some = optional.CATALOG[0]
        campaign = _campaign(11, 22)
        bot = self.bot(campaign)
        bot.post = AsyncMock(return_value=False)
        self.assertFalse(
            await DMBot.apply_optional_rule(bot, campaign, some, True, 5)  # type: ignore[arg-type]
        )
        self.assertEqual(bot.post.await_args.args[1], optional.screen_line(some, True, "<@5>"))

    async def test_a_level_note_goes_to_the_dm_bot_channel_and_else_the_screen(self) -> None:
        for bot_channel, want in ((11, 11), (None, 22)):
            campaign = _campaign(bot_channel, 22)
            bot = self.bot(campaign)
            await DMBot.set_screen_level(bot, 1, campaign.id, "quiet", was="normal")  # type: ignore[arg-type]
            bot.post.assert_awaited_once_with(want, screen_messages.level_changed("quiet"))

    async def test_no_note_when_the_level_did_not_change(self) -> None:
        campaign = _campaign(11, 22)
        bot = self.bot(campaign)
        await DMBot.set_screen_level(bot, 1, campaign.id, "quiet", was="quiet")  # type: ignore[arg-type]
        bot.post.assert_not_awaited()


class _NoLock:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


class PicturesRemoved(unittest.IsolatedAsyncioTestCase):
    """The "pictures removed" count goes privately first; the DM Bot channel is the fallback, not
    the DM screen (#3874)."""

    def bot(self, campaign: SimpleNamespace, private: bool) -> SimpleNamespace:
        return SimpleNamespace(
            _pictures_told=set(),
            campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
            ask_privately=AsyncMock(return_value=private),
            post=AsyncMock(return_value=True),
        )

    async def test_a_dm_who_can_be_reached_gets_it_privately_only(self) -> None:
        bot = self.bot(_campaign(11, 22), True)
        await DMBot._tell_pictures_removed(bot, 1, "c" * 32, 3)  # type: ignore[arg-type]
        bot.post.assert_not_awaited()
        self.assertIn("3 old pictures", bot.ask_privately.await_args.args[2])

    async def test_no_reachable_dm_falls_back_to_the_dm_bot_channel_then_the_screen(self) -> None:
        for bot_channel, want in ((11, 11), (None, 22)):
            bot = self.bot(_campaign(bot_channel, 22), False)
            await DMBot._tell_pictures_removed(bot, 1, "c" * 32, 1)  # type: ignore[arg-type]
            self.assertEqual(bot.post.await_args.args[0], want)
            self.assertIn("1 old picture ", bot.post.await_args.args[1])

    async def test_told_once_for_each_run(self) -> None:
        bot = self.bot(_campaign(11, 22), False)
        await DMBot._tell_pictures_removed(bot, 1, "c" * 32, 1)  # type: ignore[arg-type]
        await DMBot._tell_pictures_removed(bot, 1, "c" * 32, 2)  # type: ignore[arg-type]
        bot.post.assert_awaited_once()
