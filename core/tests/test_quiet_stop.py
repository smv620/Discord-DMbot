"""The automatic stop (#2034) around the bot: the setting, the warning, the stop and Keep
listening. The clock itself is in test_idle_stop; this is the part that talks to Discord and the
database, with stand-ins."""

from __future__ import annotations

import asyncio
import time
import unittest
import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.bot import DMBot
from dmbot.campaigns import CampaignStore
from dmbot.campaigns.models import CampaignError
from dmbot.campaigns.store import QUIET_STOP_CHOICES
from dmbot.db import Database, drop_schema
from dmbot.dm_screen import quiet_stop
from dmbot.dm_screen.settings import QuietStopButton, settings_text, settings_view
from dmbot.idle_stop import EMPTY_S, WARN_S, IdleWatch
from dmbot.schema import MIGRATIONS
from tests import recap_fixtures as fx
from tests.pg import TEST_URL, DatabaseTest
from tests.test_dm_screen_settings import CAMPAIGN, DM, GUILD, campaign, press

SESSION = 5


def fake_bot(*, people: list[int], minutes: int = 10, now: float = 0.0) -> Any:
    idle = IdleWatch(now)
    table = SimpleNamespace(
        guild_id=GUILD,
        campaign_id=CAMPAIGN,
        listening=True,
        screen_channel_id=66,
        voice_channel_id=55,
        dm_user_id=DM,
        dm_user_ids=frozenset(),
        campaign_name="Frostmaiden",
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
            get=AsyncMock(
                return_value=SimpleNamespace(quiet_stop_minutes=minutes, dm_bot_channel_id=777)
            )
        ),
        _dm_user=AsyncMock(return_value=True),
        post_message=AsyncMock(return_value=sent),
        post=AsyncMock(),
        stop_session=AsyncMock(return_value="Stopped listening."),
        sent=sent,
    )
    bot.get_channel = lambda _id: MagicMock(spec=discord.VoiceChannel)
    bot._quiet_minutes = DMBot._quiet_minutes.__get__(bot)
    bot._remove_idle_warning = DMBot._remove_idle_warning
    bot._idle_check = DMBot._idle_check.__get__(bot)
    bot.keep_listening = DMBot.keep_listening.__get__(bot)
    bot._tell_auto_stop = DMBot._tell_auto_stop.__get__(bot)
    bot._track = lambda coro, _name: asyncio.ensure_future(coro)

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
        self.assertIn("nobody is in the voice channel", call.args[1])
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
        self.assertIn("nobody was in the voice channel for 5 minutes", told.args[1])
        self.assertIn("/dmbot start", told.args[1])

    async def test_a_quiet_table_says_how_long_and_a_bot_only_channel_is_empty(self) -> None:
        bot = fake_bot(people=[1, 2], minutes=15)
        self.table = bot.tables[GUILD]
        await self.check(bot, 899)
        bot.post_message.assert_not_awaited()
        await self.check(bot, 900)
        self.assertIn("nobody has spoken for 15 minutes", bot.post_message.await_args.args[1])
        await self.check(bot, 900 + WARN_S)
        self.assertEqual(bot.stop_session.await_args.kwargs["auto"], "quiet")
        self.assertIn("nobody spoke for 15 minutes", bot.post.await_args.args[1])

    async def test_an_automatic_stop_messages_the_dm_and_each_person_in_voice_once(self) -> None:
        bot = fake_bot(people=[DM, 11, 12, 11], minutes=10)
        self.table = bot.tables[GUILD]
        await self.check(bot, 600)
        await self.check(bot, 600 + WARN_S)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        sent = {c.args[0]: c.args[1] for c in bot._dm_user.await_args_list}
        self.assertEqual(sorted(sent), [DM, 11, 12])  # each once; the DM gets just the DM's
        self.assertEqual(bot._dm_user.await_count, 3)
        self.assertIn("**Frostmaiden**", sent[DM])
        self.assertIn("nobody spoke for 10 minutes", sent[DM])
        self.assertIn("<#777>", sent[DM])  # where Settings is
        self.assertIn("Stop when quiet", sent[DM])
        self.assertIn("/dmbot start", sent[DM])
        self.assertIn("Your DM can start it again.", sent[11])
        self.assertNotIn("Stop when quiet", sent[11])

    async def test_a_closed_inbox_never_breaks_the_stop_or_the_other_messages(self) -> None:
        bot = fake_bot(people=[11, 12], minutes=10)
        bot._dm_user = AsyncMock(side_effect=[False, True, True])
        self.table = bot.tables[GUILD]
        await self.check(bot, 600)
        await self.check(bot, 600 + WARN_S)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        bot.stop_session.assert_awaited_once()
        bot.post.assert_awaited_once()  # the DM-screen line stays
        self.assertEqual(bot._dm_user.await_count, 3)

    async def test_a_stop_that_was_not_this_ones_sends_no_messages(self) -> None:
        bot = fake_bot(people=[11], minutes=10)
        bot.stop_session.side_effect = None
        bot.stop_session.return_value = "Someone already stopped it."
        self.table = bot.tables[GUILD]
        await self.check(bot, 600)
        await self.check(bot, 600 + WARN_S)
        await asyncio.sleep(0)
        bot._dm_user.assert_not_awaited()

    async def test_there_is_no_off(self) -> None:
        self.assertEqual(QUIET_STOP_CHOICES, (5, 10, 15, 30))
        self.assertNotIn("Off", quiet_stop.line(10))

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

    async def test_a_warning_that_could_not_be_posted_never_leads_to_a_stop(self) -> None:
        bot = fake_bot(people=[])
        self.table = bot.tables[GUILD]
        bot.post_message.side_effect = discord.HTTPException(
            SimpleNamespace(status=403, reason=""), "no"
        )
        await self.check(bot, EMPTY_S)
        await self.check(bot, EMPTY_S + WARN_S)  # would have stopped; it was never warned
        bot.stop_session.assert_not_awaited()
        bot.post_message.side_effect = None
        await self.check(bot, EMPTY_S + WARN_S + 60)  # the next look warns again
        self.assertEqual(bot.post_message.await_count, 3)

    async def test_a_stop_that_lost_a_race_is_not_announced(self) -> None:
        for answer in ("DMbot isn't listening right now.", "That session already ended."):
            bot = fake_bot(people=[])
            self.table = bot.tables[GUILD]
            await self.check(bot, EMPTY_S)
            bot.stop_session.side_effect = None
            bot.stop_session.return_value = answer
            await self.check(bot, EMPTY_S + WARN_S)
            bot.post.assert_not_awaited()

    async def test_the_stop_says_what_the_warning_said(self) -> None:
        bot = fake_bot(people=[1], minutes=30)
        self.table = bot.tables[GUILD]
        await self.check(bot, 30 * 60)  # warned for silence
        bot.people = []  # then everyone leaves
        await self.check(bot, 30 * 60 + WARN_S)
        self.assertEqual(bot.stop_session.await_args.kwargs["auto"], "quiet")

    async def test_an_unknown_voice_channel_is_not_an_empty_one(self) -> None:
        bot = fake_bot(people=[])
        self.table = bot.tables[GUILD]
        bot.get_channel = lambda _id: None  # a gateway reconnect
        await self.check(bot, 10 * EMPTY_S)
        bot.post_message.assert_not_awaited()

    async def test_a_session_replaced_while_the_setting_is_read_is_left_alone(self) -> None:
        bot = fake_bot(people=[])
        self.table = bot.tables[GUILD]

        async def replaced(*args: Any) -> Any:
            bot.tables[GUILD] = SimpleNamespace()  # a new session took its place
            return SimpleNamespace(quiet_stop_minutes=30)

        bot.campaigns.get.side_effect = replaced
        await bot._idle_check(self.table)
        bot.post_message.assert_not_awaited()

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
        self.assertIsNone(await bot._quiet_minutes(bot.tables[GUILD]))  # skipped, not guessed


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
        self.assertNotIn("Off", quiet_stop.line(30))


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
        for minutes in (5, 10, 15, 30, 0, 60, 90):  # (the last three: buttons from before #2288)
            button = QuietStopButton(CAMPAIGN, minutes)
            self.assertLessEqual(len(button.item.label or ""), 25)

    async def test_a_press_saves_the_next_choice_in_order(self) -> None:
        seen = []
        for current, expected in ((5, 10), (10, 15), (15, 30), (30, 5), (60, 5), (0, 5)):
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
    async def test_it_defaults_to_ten_is_saved_and_refuses_anything_else(self) -> None:
        store = CampaignStore(self.db)
        made = await store.create(GUILD, "One", fx.DM)
        self.assertEqual(made.quiet_stop_minutes, 10)
        for minutes in (5, 15, 30, 10):
            saved = await store.set_quiet_stop(GUILD, made.id, minutes)
            self.assertEqual(saved.quiet_stop_minutes, minutes)
        for refused in (0, 45, 60, 90):
            with self.assertRaises(CampaignError):
                await store.set_quiet_stop(GUILD, made.id, refused)
        got = await store.get(GUILD, made.id)
        assert got is not None
        self.assertEqual(got.quiet_stop_minutes, 10)


class TheMigration(unittest.IsolatedAsyncioTestCase):
    async def test_off_60_and_90_become_30_and_30_stays_on_a_table_holding_rows(self) -> None:
        if not TEST_URL:
            self.skipTest("needs the test database")
        schema = f"t_{uuid.uuid4().hex[:16]}"
        names = [n for n, _ in MIGRATIONS]
        before = MIGRATIONS[: names.index("0166_quiet_stop_choices")]
        db = await Database.open(TEST_URL, schema=schema, max_size=2, migrations=before)
        try:
            store = CampaignStore(db)
            made = {}
            for minutes in (0, 30, 60, 90):
                camp = await store.create(GUILD, f"At {minutes}", fx.DM)
                async with db.guild(GUILD) as conn:
                    await conn.execute(
                        "UPDATE campaigns SET quiet_stop_minutes = %s WHERE id = %s",
                        (minutes, camp.id),
                    )
                made[minutes] = camp.id
            await db.migrate(MIGRATIONS)
            for minutes, cid in made.items():
                got = await store.get(GUILD, cid)
                assert got is not None
                self.assertEqual(got.quiet_stop_minutes, 30, minutes)
            fresh = await store.create(GUILD, "New", fx.DM)
            self.assertEqual(fresh.quiet_stop_minutes, 10)
        finally:
            await db.close()
            await drop_schema(TEST_URL, schema)


class TheBackup(DatabaseTest):
    async def restored_from(self, minutes: object, n: int = 0) -> Any:
        store = CampaignStore(self.db)
        made = await store.create(GUILD, f"Backed up {n}", fx.DM)
        backup = await store.export(GUILD, made.id)
        backup["campaign"]["quiet_stop_minutes"] = minutes
        return store, backup

    async def test_a_backup_holding_off_60_or_90_loads_as_30(self) -> None:
        for n, old in enumerate((0, 60, 90)):
            store, backup = await self.restored_from(old, n)
            restored = await store.import_backup(GUILD + 10 + n, backup, fx.DM)
            self.assertEqual(restored.quiet_stop_minutes, 30, old)

    async def test_a_backup_with_a_current_choice_keeps_it_and_nonsense_is_refused(self) -> None:
        store, backup = await self.restored_from(15, 5)
        restored = await store.import_backup(GUILD + 2, backup, fx.DM)
        self.assertEqual(restored.quiet_stop_minutes, 15)
        for n, bad in enumerate((7, "10", None, True), start=20):
            store, backup = await self.restored_from(bad, n)
            with self.assertRaises(CampaignError, msg=repr(bad)):
                await store.import_backup(GUILD + 30 + n, backup, fx.DM)


_ = (cast, discord, time)  # (imports kept for the stand-ins above)

if __name__ == "__main__":
    unittest.main()
