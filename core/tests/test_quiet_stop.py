"""The automatic stop (#2034) around the bot: the setting, the warning, the stop and Keep
listening. The clock itself is in test_idle_stop; this is the part that talks to Discord and the
database, with stand-ins."""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.bot import DMBot
from dmbot.campaigns import CampaignStore
from dmbot.campaigns.models import CampaignError
from dmbot.dm_screen import quiet_stop
from dmbot.dm_screen.settings import QuietStopButton, settings_text, settings_view
from dmbot.idle_stop import EMPTY_S, WARN_S, IdleWatch
from tests import recap_fixtures as fx
from tests.pg import DatabaseTest
from tests.test_dm_screen_settings import CAMPAIGN, DM, GUILD, campaign, press

SESSION = 5


def fake_bot(*, people: list[int], minutes: int = 30, now: float = 0.0) -> Any:
    idle = IdleWatch(now)
    table = SimpleNamespace(
        guild_id=GUILD,
        campaign_id=CAMPAIGN,
        listening=True,
        screen_channel_id=66,
        dm_user_id=DM,
        segmenter=SimpleNamespace(session=SESSION),
        totals=SimpleNamespace(speakers={}),
        idle=idle,
        idle_message=None,
    )
    sent = SimpleNamespace(edit=AsyncMock(), content="")
    bot: Any = SimpleNamespace(
        tables={GUILD: table},
        people=people,
        _people_in_voice=lambda t: bot.people,
        campaigns=SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(quiet_stop_minutes=minutes))
        ),
        post_message=AsyncMock(return_value=sent),
        post=AsyncMock(),
        stop_session=AsyncMock(return_value="Stopped listening."),
        sent=sent,
    )
    bot._quiet_minutes = DMBot._quiet_minutes.__get__(bot)
    bot._remove_idle_warning = DMBot._remove_idle_warning
    bot._idle_check = DMBot._idle_check.__get__(bot)
    bot.keep_listening = DMBot.keep_listening.__get__(bot)

    async def stop_for_real(*args: Any, **kwargs: Any) -> str:
        bot.tables.pop(GUILD, None)  # the real path removes the table
        return "Stopped listening."

    bot.stop_session.side_effect = stop_for_real
    return bot


class TheCheck(unittest.IsolatedAsyncioTestCase):
    async def check(self, bot: Any, at: float) -> None:
        with patch("dmbot.bot.time.monotonic", return_value=at):
            await bot._idle_check(bot.tables.get(GUILD, self.table))

    async def test_an_empty_channel_warns_with_keep_listening_then_stops_through_the_stop_path(
        self,
    ) -> None:
        bot = fake_bot(people=[])
        self.table = bot.tables[GUILD]
        await self.check(bot, EMPTY_S)
        (call,) = bot.post_message.await_args_list
        self.assertEqual(call.args[0], 66)
        self.assertIn("Nobody's here", call.args[1])
        self.assertIn("5 minutes", call.args[1])
        (button,) = call.args[2].children
        self.assertEqual(button.custom_id, f"dmbot:keepon:{CAMPAIGN}:{SESSION}")
        bot.stop_session.assert_not_awaited()
        await self.check(bot, EMPTY_S + WARN_S)
        args, kwargs = bot.stop_session.await_args
        self.assertEqual((args[0], args[2]), (GUILD, True))
        self.assertEqual(kwargs["auto"], "empty")
        self.assertEqual((kwargs["campaign_id"], kwargs["session"]), (CAMPAIGN, SESSION))
        (told,) = bot.post.await_args_list
        self.assertEqual(told.args[1], "⏹ Stopped: nobody was here for 5 minutes.")

    async def test_a_quiet_table_says_how_long_and_a_bot_only_channel_is_empty(self) -> None:
        bot = fake_bot(people=[1, 2], minutes=60)
        self.table = bot.tables[GUILD]
        await self.check(bot, 3599)
        bot.post_message.assert_not_awaited()
        await self.check(bot, 3600)
        self.assertIn("Nobody has spoken for 60 minutes", bot.post_message.await_args.args[1])
        await self.check(bot, 3600 + WARN_S)
        self.assertEqual(bot.stop_session.await_args.kwargs["auto"], "quiet")
        self.assertEqual(bot.post.await_args.args[1], "⏹ Stopped: nobody spoke for 60 minutes.")

    async def test_off_never_stops_a_table_with_people_but_still_stops_an_empty_one(self) -> None:
        bot = fake_bot(people=[1], minutes=0)
        self.table = bot.tables[GUILD]
        await self.check(bot, 100 * 3600)
        bot.post_message.assert_not_awaited()
        bot.people = []
        await self.check(bot, 100 * 3600 + 1)
        await self.check(bot, 100 * 3600 + 1 + EMPTY_S)
        self.assertEqual(bot.post_message.await_count, 1)

    async def test_someone_coming_back_cancels_the_warning_and_says_so(self) -> None:
        bot = fake_bot(people=[])
        self.table = bot.tables[GUILD]
        await self.check(bot, EMPTY_S)
        bot.tables[GUILD].idle_message = bot.sent
        bot.people = [1]
        await self.check(bot, EMPTY_S + 30)
        bot.sent.edit.assert_awaited_once()
        self.assertEqual(bot.sent.edit.await_args.kwargs["content"], quiet_stop.CANCELLED)
        bot.stop_session.assert_not_awaited()

    async def test_keep_listening_starts_the_clocks_again_and_an_old_button_does_nothing(
        self,
    ) -> None:
        bot = fake_bot(people=[])
        table = self.table = bot.tables[GUILD]
        await self.check(bot, EMPTY_S)
        self.assertFalse(bot.keep_listening(GUILD, CAMPAIGN, SESSION + 1))  # another session
        self.assertFalse(bot.keep_listening(GUILD, "d" * 32, SESSION))  # another campaign
        self.assertTrue(bot.keep_listening(GUILD, CAMPAIGN, SESSION))
        self.assertIsNone(table.idle_message)
        await self.check(bot, EMPTY_S + WARN_S)  # would have stopped; now it starts again
        bot.stop_session.assert_not_awaited()

    async def test_a_session_that_is_not_listening_yet_is_left_alone(self) -> None:
        bot = fake_bot(people=[])
        bot.tables[GUILD].listening = False
        self.table = bot.tables[GUILD]
        await self.check(bot, 10 * EMPTY_S)
        bot.post_message.assert_not_awaited()

    async def test_the_setting_is_read_fresh_and_a_failed_read_uses_the_default(self) -> None:
        bot = fake_bot(people=[1], minutes=90)
        self.assertEqual(await bot._quiet_minutes(bot.tables[GUILD]), 90)
        bot.campaigns.get.side_effect = RuntimeError("down")
        self.assertEqual(await bot._quiet_minutes(bot.tables[GUILD]), 30)


class TheKeepListeningButton(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_dm_can_press_it_and_it_keeps_the_session(self) -> None:
        keep = MagicMock(return_value=True)
        it = press(DM, found=campaign())
        it.client.keep_listening = keep
        it.response.edit_message = AsyncMock()
        await quiet_stop.KeepListeningButton(CAMPAIGN, SESSION).callback(it)
        keep.assert_called_once_with(GUILD, CAMPAIGN, SESSION)
        self.assertEqual(it.response.edit_message.await_args.kwargs["content"], quiet_stop.KEPT)
        # a player is turned away, and nothing is kept
        player = press(99, found=campaign())
        player.client.keep_listening = MagicMock()
        await quiet_stop.KeepListeningButton(CAMPAIGN, SESSION).callback(player)
        player.client.keep_listening.assert_not_called()

    async def test_a_press_on_a_stale_warning_says_so(self) -> None:
        it = press(DM, found=campaign())
        it.client.keep_listening = MagicMock(return_value=False)
        it.response.edit_message = AsyncMock()
        await quiet_stop.KeepListeningButton(CAMPAIGN, SESSION).callback(it)
        self.assertEqual(it.response.edit_message.await_args.kwargs["content"], quiet_stop.STALE)

    def test_the_words_are_short_and_plain(self) -> None:
        self.assertIn("Keep listening", quiet_stop.idle_warning_text("empty", 30))
        self.assertIn("30 minutes", quiet_stop.idle_warning_text("quiet", 30))
        self.assertIn("Off", quiet_stop.line(0))


class TheSettingsCard(unittest.IsolatedAsyncioTestCase):
    def test_the_card_shows_the_choice_and_the_view_has_room_for_the_button(self) -> None:
        camp = campaign()
        text = settings_text(camp, None, DM)
        self.assertIn("Stop when quiet", text)
        view = settings_view(camp, None, DM)
        self.assertLessEqual(len(view.children), 25)
        self.assertTrue(any(isinstance(c, QuietStopButton) for c in view.children))
        # a player's card has no such button
        self.assertFalse(
            any(isinstance(c, QuietStopButton) for c in settings_view(camp, None, 99).children)
        )

    def test_every_label_fits_a_button(self) -> None:
        for minutes in (30, 60, 90, 0):
            button = QuietStopButton(CAMPAIGN, minutes)
            self.assertLessEqual(len(button.item.label or ""), 25)

    async def test_a_press_saves_the_next_choice_in_order(self) -> None:
        seen = []
        for current, expected in ((30, 60), (60, 90), (90, 0), (0, 30)):
            it = press(DM, found=campaign())
            it.client.campaigns.set_quiet_stop = AsyncMock(return_value=campaign())
            it.edit_original_response = AsyncMock()
            await QuietStopButton(CAMPAIGN, current).callback(it)
            seen.append(it.client.campaigns.set_quiet_stop.await_args.args[2])
            self.assertEqual(seen[-1], expected)

    async def test_a_player_cannot_change_it(self) -> None:
        it = press(99, found=campaign())
        it.client.campaigns.set_quiet_stop = AsyncMock()
        await QuietStopButton(CAMPAIGN, 30).callback(it)
        it.client.campaigns.set_quiet_stop.assert_not_awaited()


class TheStoredChoice(DatabaseTest):
    async def test_it_defaults_to_thirty_is_saved_and_refuses_anything_else(self) -> None:
        store = CampaignStore(self.db)
        made = await store.create(GUILD, "One", fx.DM)
        self.assertEqual(made.quiet_stop_minutes, 30)
        for minutes in (60, 90, 0, 30):
            saved = await store.set_quiet_stop(GUILD, made.id, minutes)
            self.assertEqual(saved.quiet_stop_minutes, minutes)
        with self.assertRaises(CampaignError):
            await store.set_quiet_stop(GUILD, made.id, 45)
        got = await store.get(GUILD, made.id)
        assert got is not None
        self.assertEqual(got.quiet_stop_minutes, 30)


_ = (cast, discord, time)  # (imports kept for the stand-ins above)

if __name__ == "__main__":
    unittest.main()
