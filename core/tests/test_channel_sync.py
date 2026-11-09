"""Keeping a campaign's two channels in step with who may see them (#3891): the people from the
stores, the sweep healing a missing overwrite, leaving a right channel alone, and the plain message
when DMbot lacks a permission. Invented ids."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.campaigns import CampaignStore
from dmbot.dm_screen import channel_sync
from dmbot.memory.backup import MemorySection
from dmbot.memory.store import MemoryStore
from tests.pg import DatabaseTest
from tests.test_dm_screen_setup import BOT, BOT_PERMS, DM, GUILD, text_channel

TRANSCRIPT, DMBOT_CHANNEL = 60, 61
EVE, MIRA = 91, 92


class Access(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        self.store.register_section(MemorySection())
        self.memory = MemoryStore(self.db)
        made = await self.store.create(GUILD, "Frostmaiden", DM)
        self.me = MagicMock(spec=discord.Member)
        self.me.id = BOT
        self.me.guild_permissions = BOT_PERMS
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD
        guild.me = self.me
        guild.get_role = lambda _id: None
        guild.get_member = lambda _id: None
        self.transcript = text_channel(TRANSCRIPT, "dmb-transcript-frstmdn", guild)
        self.dm_bot = text_channel(DMBOT_CHANNEL, "dmb-dm-bot-frstmdn", guild)
        for channel in (self.transcript, self.dm_bot):
            channel.pins = AsyncMock(return_value=[])
        guild.get_channel = lambda cid: {
            TRANSCRIPT: self.transcript,
            DMBOT_CHANNEL: self.dm_bot,
        }.get(cid)
        guild.fetch_channel = AsyncMock(side_effect=lambda cid: guild.get_channel(cid))
        self.guild = guild
        await self.store.set_transcript_channel(GUILD, made.id, TRANSCRIPT)
        await self.store.set_dm_bot_channel(GUILD, made.id, DMBOT_CHANNEL)
        self.campaign = await self.store.get(GUILD, made.id)
        assert self.campaign is not None

    def edited(self, channel: Any) -> dict[Any, discord.PermissionOverwrite] | None:
        calls = channel.edit.await_args_list
        return calls[-1].kwargs.get("overwrites") if calls else None

    async def test_the_people_are_the_dms_players_speakers_and_the_by_hand_list(self) -> None:
        await self.store.add_channel_person(GUILD, self.campaign.id, EVE, DM, 1)
        await self.memory.add_entity(
            GUILD, self.campaign.id, type="character", name="Mira", role="player_character",
            played_by=MIRA, status="confirmed", source="dm",
        )  # fmt: skip
        people = await channel_sync.campaign_people(self.store, self.memory, self.campaign, BOT)
        self.assertEqual(people, {DM, EVE, MIRA})

    async def test_a_channel_that_everyone_can_see_is_hidden_from_non_members(self) -> None:
        await self.store.add_channel_person(GUILD, self.campaign.id, EVE, DM, 1)
        problem = await channel_sync.reconcile(self.guild, self.campaign, self.store, self.memory)
        self.assertIsNone(problem)
        for channel in (self.transcript, self.dm_bot):
            sent = self.edited(channel)
            assert sent is not None
            by_id = {key.id: ow for key, ow in sent.items()}
            self.assertIs(by_id[GUILD].view_channel, False)
            self.assertTrue(by_id[EVE].view_channel)
            self.assertTrue(by_id[DM].view_channel)

    async def test_a_channel_already_right_is_left_alone(self) -> None:
        await channel_sync.reconcile(self.guild, self.campaign, self.store, self.memory)
        for channel in (self.transcript, self.dm_bot):
            sent = self.edited(channel)
            assert sent is not None
            channel.overwrites = sent  # (Discord now has what was sent)
            channel.edit.reset_mock()
        await channel_sync.reconcile(self.guild, self.campaign, self.store, self.memory)
        self.transcript.edit.assert_not_awaited()
        self.dm_bot.edit.assert_not_awaited()

    async def test_choosing_everyone_puts_the_channels_back(self) -> None:
        await channel_sync.reconcile(self.guild, self.campaign, self.store, self.memory)
        for channel in (self.transcript, self.dm_bot):
            channel.overwrites = self.edited(channel)
            channel.edit.reset_mock()
        opened = await self.store.set_channels_visibility(GUILD, self.campaign.id, "everyone")
        await channel_sync.reconcile(self.guild, opened, self.store, self.memory)
        sent = self.edited(self.transcript)
        assert sent is not None
        by_id = {key.id: ow for key, ow in sent.items()}
        self.assertTrue(by_id[GUILD].view_channel)
        self.assertNotIn(DM, by_id)

    async def test_a_missing_permission_is_said_in_plain_words_and_nothing_is_changed(self) -> None:
        self.me.guild_permissions = discord.Permissions(
            view_channel=True, send_messages=True, read_message_history=True, manage_channels=True
        )
        problem = await channel_sync.reconcile(self.guild, self.campaign, self.store, self.memory)
        assert problem is not None
        self.assertIn("Manage Roles", problem)
        self.transcript.edit.assert_not_awaited()
        self.dm_bot.edit.assert_not_awaited()

    async def test_a_server_role_that_can_see_a_channel_is_reported_not_changed(self) -> None:
        role = MagicMock(spec=discord.Role)
        role.id = 777
        self.transcript.overwrites = {role: discord.PermissionOverwrite(view_channel=True)}
        found = channel_sync.exposure(self.transcript, self.campaign, BOT, [DM])
        self.assertEqual(found.roles, (777,))

    async def test_a_person_taken_off_the_list_loses_the_overwrite(self) -> None:
        await self.store.add_channel_person(GUILD, self.campaign.id, EVE, DM, 1)
        await channel_sync.reconcile(self.guild, self.campaign, self.store, self.memory)
        for channel in (self.transcript, self.dm_bot):
            channel.overwrites = self.edited(channel)
            channel.edit.reset_mock()
        await self.store.remove_channel_person(GUILD, self.campaign.id, EVE)
        await channel_sync.reconcile(self.guild, self.campaign, self.store, self.memory)
        sent = self.edited(self.transcript)
        assert sent is not None
        self.assertNotIn(EVE, {key.id for key in sent})

    async def test_someone_who_left_the_server_is_not_given_an_overwrite(self) -> None:
        await self.store.add_channel_person(GUILD, self.campaign.id, EVE, DM, 1)
        gone = discord.NotFound(MagicMock(status=404, reason="x"), "gone")
        self.guild.fetch_member = AsyncMock(side_effect=gone)
        channel_sync._seen.clear()
        await channel_sync.reconcile(self.guild, self.campaign, self.store, self.memory)
        sent = self.edited(self.transcript)
        assert sent is not None
        self.assertNotIn(EVE, {key.id for key in sent})
        self.assertIn(DM, {key.id for key in sent})  # (the DM is always one)

    async def test_the_sweep_never_makes_a_channel(self) -> None:
        self.guild.get_channel = lambda _cid: self.transcript  # (cached) ...
        self.guild.fetch_channel = AsyncMock(side_effect=discord.NotFound(MagicMock(), "gone"))
        await channel_sync.reconcile(self.guild, self.campaign, self.store, self.memory)
        self.guild.create_text_channel.assert_not_called()

    async def test_a_role_that_can_see_the_channels_is_counted(self) -> None:
        role = MagicMock(spec=discord.Role)
        role.id = 777
        self.transcript.overwrites = {role: discord.PermissionOverwrite(view_channel=True)}
        self.assertEqual(channel_sync.exposed_roles(self.guild, self.campaign), 1)
        opened = await self.store.set_channels_visibility(GUILD, self.campaign.id, "everyone")
        self.assertEqual(channel_sync.exposed_roles(self.guild, opened), 0)
