"""The DM Bot channel (#1666), slice 1: who gets which button (pure), the channel made like the
game-time channel, kept out of backups and never read as table chat. The buttons themselves
come in the next slices."""

from __future__ import annotations

import itertools
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.campaigns import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.dm_screen import dm_bot_panel as panel
from dmbot.dm_screen import messages
from dmbot.dm_screen.dm_bot_channel import dm_bot_channel_name, is_dm_bot_name, setup_dm_bot_channel
from dmbot.dm_screen.dm_bot_panel import Button, Role, Standing
from dmbot.schema import MIGRATIONS
from tests.pg import TEST_URL, DatabaseTest
from tests.test_dm_screen_setup import BOT, BOT_PERMS, GENERAL, text_channel

GUILD = 111
DM = 7
NEW = 680000000000000071  # long: a short id turns up inside timestamps (#1213, #2208)


class Roles(unittest.TestCase):
    def test_a_role_is_the_strongest_thing_that_is_true_of_the_person(self) -> None:
        cases = {
            Standing(True, True, True): Role.GAME_MASTER,
            Standing(True, False, False): Role.GAME_MASTER,
            Standing(False, True, False): Role.PLAYER,
            Standing(False, True, True): Role.PLAYER,
            Standing(False, False, True): Role.MEMBER,
            Standing(False, False, False): Role.GUEST,
        }
        for who, role in cases.items():
            self.assertEqual(panel.role_of(who), role, who)

    def test_only_a_game_master_gets_the_manage_buttons(self) -> None:
        for button in (Button.MANAGE_DMBOT, Button.MANAGE_CAMPAIGN):
            for role in Role:
                self.assertEqual(panel.may_press(role, button), role is Role.GAME_MASTER, role)
                self.assertEqual(button in panel.buttons_for(role), role is Role.GAME_MASTER)

    def test_each_role_has_what_the_weaker_ones_have(self) -> None:
        order = [Role.GUEST, Role.MEMBER, Role.PLAYER, Role.GAME_MASTER]
        for weaker, stronger in itertools.pairwise(order):
            self.assertLessEqual(
                set(panel.buttons_for(weaker)), set(panel.buttons_for(stronger)), stronger
            )

    def test_what_is_drawn_is_what_may_be_pressed(self) -> None:
        for role in Role:
            for button in Button:
                self.assertEqual(
                    button in panel.buttons_for(role), panel.may_press(role, button), (role, button)
                )

    def test_each_role_gets_exactly_its_buttons_in_order_and_never_more_than_four(self) -> None:
        self.assertEqual(panel.buttons_for(Role.GUEST), (Button.HELP,))
        mine = (Button.MY_DATA, Button.HELP)
        self.assertEqual(panel.buttons_for(Role.MEMBER), mine)
        self.assertEqual(panel.buttons_for(Role.PLAYER), mine)
        self.assertEqual(
            panel.buttons_for(Role.GAME_MASTER),
            (Button.MANAGE_DMBOT, Button.MANAGE_CAMPAIGN, Button.MY_DATA, Button.HELP),
        )
        for role in Role:
            self.assertLessEqual(len(panel.buttons_for(role)), 4)

    def test_the_shared_message_holds_only_what_every_role_may_press(self) -> None:
        for button in panel.shared_panel():
            for role in Role:
                self.assertTrue(
                    button is Button.OPEN or panel.may_press(role, button), (role, button)
                )

    def test_every_button_has_a_short_label_and_the_panel_text_is_plain(self) -> None:
        for label in panel.LABELS.values():
            self.assertLessEqual(len(label), 25)
        for button in panel.shared_panel():
            self.assertIn(button, panel.LABELS)  # the shared message's buttons are labelled
        self.assertIn("Only you see the answer", panel.PANEL_TEXT)


class Setup(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        self.campaign = await self.store.create(GUILD, "Rime of the Frostmaiden", DM)
        me = MagicMock(spec=discord.Member)
        me.id = BOT
        me.guild_permissions = BOT_PERMS
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD
        guild.me = me
        guild.get_role = lambda _id: None
        guild.get_member = lambda _id: None
        self.general = text_channel(GENERAL, "general", guild)
        self.new = text_channel(NEW, "dmb-dm-bot-rmfthfrstmdn", guild)
        guild.fetch_channel = AsyncMock(return_value=self.general)
        guild.create_text_channel = AsyncMock(return_value=self.new)
        guild.get_channel = lambda cid: self.new if cid == NEW else None
        self.guild = guild

    async def test_a_new_channel_is_named_saved_and_view_only_for_everyone(self) -> None:
        channel = await setup_dm_bot_channel(self.guild, self.campaign.id, self.store)
        assert channel is self.new
        call = self.guild.create_text_channel.call_args
        self.assertEqual(call.args[0], "dmb-dm-bot-rmfthfrstmdn")
        created: dict[Any, discord.PermissionOverwrite] = call.kwargs["overwrites"]
        everyone = next(ow for key, ow in created.items() if key.id == GUILD)
        self.assertTrue(everyone.view_channel and everyone.read_message_history)
        self.assertFalse(everyone.send_messages)  # only DMbot posts
        self.assertEqual(call.kwargs["topic"], messages.dm_bot_topic(self.campaign.name))
        saved = await self.store.get(GUILD, self.campaign.id)
        assert saved is not None and saved.dm_bot_channel_id == NEW
        self.assertIsNone(saved.dm_bot_message_id)
        self.assertIsNone(saved.gametime_channel_id)  # each channel keeps its own id

    async def test_the_name_is_a_dm_bot_one_and_not_the_dm_screens(self) -> None:
        self.assertEqual(dm_bot_channel_name(self.campaign), "dmb-dm-bot-rmfthfrstmdn")
        self.assertTrue(is_dm_bot_name("dmb-dm-bot-x"))
        self.assertFalse(is_dm_bot_name("dmb-dm-screen-x"))
        self.assertFalse(is_dm_bot_name("general"))

    async def test_a_channel_that_is_not_a_dm_bot_one_is_never_edited(self) -> None:
        await self.store.set_dm_bot_channel(GUILD, self.campaign.id, GENERAL)
        await setup_dm_bot_channel(self.guild, self.campaign.id, self.store)
        self.general.edit.assert_not_awaited()  # it is "general": a new one is made instead
        self.guild.create_text_channel.assert_awaited_once()

    async def test_a_new_channel_forgets_the_old_panel_message(self) -> None:
        await self.store.set_dm_bot_channel(GUILD, self.campaign.id, NEW)
        await self.store.set_dm_bot_message(GUILD, self.campaign.id, 680000000000000093)
        again = await self.store.get(GUILD, self.campaign.id)
        assert again is not None and again.dm_bot_message_id == 680000000000000093
        await self.store.set_dm_bot_channel(GUILD, self.campaign.id, NEW + 1)
        moved = await self.store.get(GUILD, self.campaign.id)
        assert moved is not None
        self.assertEqual((moved.dm_bot_channel_id, moved.dm_bot_message_id), (NEW + 1, None))

    async def test_a_refusal_from_discord_becomes_a_plain_cause_and_nothing_is_saved(self) -> None:
        from dmbot.dm_screen.dm_bot_channel import DmBotChannelError

        self.guild.create_text_channel = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), "no")
        )
        with self.assertRaises(DmBotChannelError):
            await setup_dm_bot_channel(self.guild, self.campaign.id, self.store)
        saved = await self.store.get(GUILD, self.campaign.id)
        assert saved is not None and saved.dm_bot_channel_id is None

    async def test_an_existing_correct_channel_is_not_edited_again(self) -> None:
        await setup_dm_bot_channel(self.guild, self.campaign.id, self.store)
        self.guild.fetch_channel = AsyncMock(return_value=self.new)
        self.new.edit.reset_mock()
        await setup_dm_bot_channel(self.guild, self.campaign.id, self.store)
        self.guild.create_text_channel.assert_awaited_once()  # still the first one

    async def test_a_backup_never_holds_the_ids(self) -> None:
        await self.store.set_dm_bot_channel(GUILD, self.campaign.id, NEW)
        await self.store.set_dm_bot_message(GUILD, self.campaign.id, 680000000000000093)
        exported = repr(await self.store.export(GUILD, self.campaign.id))
        self.assertNotIn(str(NEW), exported)
        self.assertNotIn("680000000000000093", exported)
        self.assertNotIn("dm_bot", exported)

    async def test_it_is_never_picked_as_the_dms_extra_table_chat(self) -> None:
        await self.store.set_dm_bot_channel(GUILD, self.campaign.id, NEW)
        self.assertIn(NEW, await self.store.special_channel_ids(GUILD))

    async def test_another_campaign_keeps_its_own_channel(self) -> None:
        other = await self.store.create(GUILD, "Strahd", DM)
        await self.store.set_dm_bot_channel(GUILD, self.campaign.id, NEW)
        theirs = await self.store.get(GUILD, other.id)
        assert theirs is not None
        self.assertIsNone(theirs.dm_bot_channel_id)


class Migration0120(DatabaseTest):
    async def test_rows_that_exist_are_unchanged_and_the_new_ids_are_empty(self) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0120"]
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        campaigns = CampaignStore(self.db, clock=lambda: 1)
        made = await campaigns.create(GUILD, "Frozen Wastes", DM)
        await self.db.migrate()
        campaign = await campaigns.get(GUILD, made.id)
        assert campaign is not None
        self.assertEqual(campaign.name, "Frozen Wastes")
        self.assertEqual((campaign.dm_bot_channel_id, campaign.dm_bot_message_id), (None, None))
        await campaigns.set_dm_bot_channel(GUILD, made.id, NEW)
        again = await campaigns.get(GUILD, made.id)
        assert again is not None and again.dm_bot_channel_id == NEW


if __name__ == "__main__":
    unittest.main()
