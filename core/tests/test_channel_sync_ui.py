"""The ⚙️ Manage DMbot card for who sees a campaign's channels (#3891): the two choices, the
hand-added list, and who may use them. Invented ids."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.campaigns import CampaignStore
from dmbot.dm_screen import channel_sync_ui as ui
from tests.pg import DatabaseTest
from tests.test_dm_screen_setup import DM, GUILD

OTHER, EVE = 33, 91


class Card(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        self.campaign = await self.store.create(GUILD, "Frostmaiden", DM)
        self.guild = MagicMock(spec=discord.Guild)
        self.guild.id = GUILD
        self.guild.me = SimpleNamespace(id=2000)
        self.guild.get_member = lambda _i: None
        self.client = SimpleNamespace(campaigns=self.store, memory=None)

    def press(self, user_id: int) -> Any:
        user = MagicMock(spec=discord.Member)
        user.id = user_id
        user.guild_permissions = discord.Permissions.none()
        response = SimpleNamespace(
            defer=AsyncMock(),
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            is_done=lambda: False,
        )
        return SimpleNamespace(
            client=self.client,
            guild=self.guild,
            user=user,
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    def test_the_card_uses_the_owners_words(self) -> None:
        text = ui.channels_text(self.campaign)
        self.assertIn("Only the people in this campaign see its channels.", text)
        self.assertIn("Others in the server don't see them or get notified.", text)

    async def test_a_dm_choosing_everyone_saves_it_and_puts_the_channels_in_step(self) -> None:
        press = self.press(DM)
        with patch.object(ui.channel_sync, "reconcile", new=AsyncMock(return_value=None)) as sync:
            await ui.ChannelsChoiceButton(self.campaign.id, "everyone").callback(press)
        saved = await self.store.get(GUILD, self.campaign.id)
        assert saved is not None
        self.assertEqual(saved.channels_visibility, "everyone")
        sync.assert_awaited_once()
        shown = press.edit_original_response.await_args.kwargs["content"]
        self.assertIn("Everyone in the server can see its channels.", shown)

    async def test_a_missing_permission_is_shown_on_the_card(self) -> None:
        press = self.press(DM)
        said = "I need Manage Roles to set up the DM screen."
        with patch.object(ui.channel_sync, "reconcile", new=AsyncMock(return_value=said)):
            await ui.ChannelsChoiceButton(self.campaign.id, "everyone").callback(press)
        self.assertIn(said, press.edit_original_response.await_args.kwargs["content"])

    async def test_someone_who_is_not_a_dm_changes_nothing(self) -> None:
        press = self.press(OTHER)
        with patch.object(ui.channel_sync, "reconcile", new=AsyncMock()) as sync:
            await ui.ChannelsChoiceButton(self.campaign.id, "everyone").callback(press)
            await ui.PeopleButton(self.campaign.id).callback(press)
        sync.assert_not_awaited()
        saved = await self.store.get(GUILD, self.campaign.id)
        assert saved is not None
        self.assertEqual(saved.channels_visibility, "people")
        self.assertEqual(press.response.send_message.await_count, 2)  # (told, twice)

    async def test_adding_and_removing_someone_by_hand(self) -> None:
        view = ui.PeopleView(self.campaign.id, [], self.guild)
        person = MagicMock(spec=discord.Member)
        person.id, person.bot, person.display_name = EVE, False, "Eve"
        with (
            patch.object(ui.channel_sync, "reconcile", new=AsyncMock(return_value=None)),
            patch.object(
                type(view.add_pick), "values", new=property(lambda _s: [person]), create=True
            ),
        ):
            await view._added(self.press(DM))
        self.assertEqual(await self.store.channel_people_by_hand(GUILD, self.campaign.id), [EVE])
        again = ui.PeopleView(self.campaign.id, [EVE], self.guild)
        with (
            patch.object(ui.channel_sync, "reconcile", new=AsyncMock(return_value=None)),
            patch.object(
                type(again.remove_pick), "values", new=property(lambda _s: [str(EVE)]), create=True
            ),
        ):
            await again._removed(self.press(DM))
        self.assertEqual(await self.store.channel_people_by_hand(GUILD, self.campaign.id), [])

    async def test_a_bot_cannot_be_added(self) -> None:
        view = ui.PeopleView(self.campaign.id, [], self.guild)
        bot = MagicMock(spec=discord.Member)
        bot.id, bot.bot = 5, True
        press = self.press(DM)
        with patch.object(
            type(view.add_pick), "values", new=property(lambda _s: [bot]), create=True
        ):
            await view._added(press)
        self.assertEqual(press.response.send_message.await_args.args[0], ui.NOT_A_PERSON)
        self.assertEqual(await self.store.channel_people_by_hand(GUILD, self.campaign.id), [])
