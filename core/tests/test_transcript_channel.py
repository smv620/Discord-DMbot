"""setup_transcript_channel against a fake Discord server (#124)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.campaigns import CampaignStore
from dmbot.dm_screen import messages
from dmbot.dm_screen.rules import READ_ONLY
from dmbot.dm_screen.transcript_channel import (
    TranscriptChannelError,
    is_transcript_name,
    setup_transcript_channel,
    transcript_channel_name,
)
from tests.pg import DatabaseTest
from tests.test_dm_screen_setup import (
    BOT,
    BOT_PERMS,
    DM,
    FORBIDDEN,
    GENERAL,
    GUILD,
    text_channel,
)

NEW = 60


class TranscriptChannelTests(DatabaseTest):
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
        self.new = text_channel(NEW, "dmb-transcript-rmfthfrstmdn", guild)
        self.new.pins = AsyncMock(return_value=[])
        guild.fetch_channel = AsyncMock(return_value=self.general)
        guild.create_text_channel = AsyncMock(return_value=self.new)
        guild.get_channel = lambda cid: self.new if cid == NEW else None
        self.guild = guild

    def created(self) -> dict[str, Any]:
        return dict(self.guild.create_text_channel.call_args.kwargs)

    def overwrite_for(self, target_id: int) -> discord.PermissionOverwrite:
        overwrites: dict[Any, discord.PermissionOverwrite] = self.created()["overwrites"]
        return next(ow for key, ow in overwrites.items() if key.id == target_id)

    async def test_a_new_channel_is_named_saved_and_view_only_for_everyone(self) -> None:
        channel = await setup_transcript_channel(self.guild, self.campaign.id, self.store)
        assert channel is self.new
        name = self.guild.create_text_channel.call_args.args[0]
        self.assertEqual(name, "dmb-transcript-rmfthfrstmdn")
        everyone = self.overwrite_for(GUILD)  # @everyone's ID is the server's
        self.assertTrue(everyone.view_channel and everyone.read_message_history)
        self.assertFalse(everyone.send_messages)
        self.assertFalse(everyone.add_reactions)
        self.assertFalse(everyone.create_public_threads)
        # Slash commands aren't blocked (#188): /consent revoke must work here.
        self.assertIsNone(everyone.use_application_commands)
        self.assertTrue(self.overwrite_for(BOT).send_messages)
        self.assertIn("Anyone in this server can read it", self.created()["topic"])
        saved = await self.store.get(GUILD, self.campaign.id)
        assert saved is not None and saved.transcript_channel_id == NEW

    async def test_the_card_is_posted_and_pinned(self) -> None:
        await setup_transcript_channel(self.guild, self.campaign.id, self.store)
        card = self.new.send.await_args.args[0]
        self.assertTrue(self.new.send.await_args.kwargs["silent"])  # no pop-ups
        self.assertTrue(card.startswith(messages.TRANSCRIPT_CARD_TITLE))
        self.assertIn("Only DMbot posts here", card)
        self.assertIn("No pop-ups from here", card)
        self.assertIn("`/consent revoke` here or in any channel", card)  # works here (#188)
        self.new.send.return_value.pin.assert_awaited_once()

    async def test_an_existing_channel_is_made_view_only_again_not_recreated(self) -> None:
        await self.store.set_transcript_channel(GUILD, self.campaign.id, NEW)
        self.guild.fetch_channel = AsyncMock(return_value=self.new)
        await setup_transcript_channel(self.guild, self.campaign.id, self.store)
        self.guild.create_text_channel.assert_not_called()
        self.new.edit.assert_awaited()
        sent = self.new.edit.await_args_list[0].kwargs["overwrites"]
        everyone = next(ow for key, ow in sent.items() if key.id == GUILD)
        self.assertFalse(everyone.send_messages)

    async def test_a_channel_made_before_188_gets_its_command_block_lifted(self) -> None:
        self.new.overwrites = {
            discord.Object(GUILD, type=discord.Role): discord.PermissionOverwrite(
                **READ_ONLY, use_application_commands=False
            )
        }
        await self.store.set_transcript_channel(GUILD, self.campaign.id, NEW)
        self.guild.fetch_channel = AsyncMock(return_value=self.new)
        await setup_transcript_channel(self.guild, self.campaign.id, self.store)
        sent = self.new.edit.await_args_list[0].kwargs["overwrites"]
        everyone = next(ow for key, ow in sent.items() if key.id == GUILD)
        self.assertIsNone(everyone.use_application_commands)  # inherited again
        self.assertFalse(everyone.send_messages)  # still view-only

    async def test_an_ordinary_saved_channel_is_never_taken_over(self) -> None:
        await self.store.set_transcript_channel(GUILD, self.campaign.id, GENERAL)
        await setup_transcript_channel(self.guild, self.campaign.id, self.store)
        self.general.edit.assert_not_called()
        self.guild.create_text_channel.assert_awaited_once()

    async def test_a_pinned_card_far_back_is_not_posted_again(self) -> None:
        await self.store.set_transcript_channel(GUILD, self.campaign.id, NEW)
        self.guild.fetch_channel = AsyncMock(return_value=self.new)
        card = MagicMock(spec=discord.Message)
        card.id, card.pinned = 1, True
        card.author = SimpleNamespace(id=BOT)
        campaign = await self.store.get(GUILD, self.campaign.id)
        assert campaign is not None
        card.content = messages.transcript_card(campaign.name)
        self.new.pins = AsyncMock(return_value=[card])  # thousands of lines since
        await setup_transcript_channel(self.guild, self.campaign.id, self.store)
        self.new.send.assert_not_called()

    async def test_nothing_is_edited_when_nothing_changed(self) -> None:
        await self.store.set_transcript_channel(GUILD, self.campaign.id, NEW)
        self.guild.fetch_channel = AsyncMock(return_value=self.new)
        await setup_transcript_channel(self.guild, self.campaign.id, self.store)
        first = self.new.edit.await_args_list[0].kwargs
        # Discord now has what DMbot sent; a second start changes nothing.
        self.new.topic = first["topic"]
        self.new.overwrites = first["overwrites"]
        self.new.edit.reset_mock()
        await setup_transcript_channel(self.guild, self.campaign.id, self.store)
        self.new.edit.assert_not_called()

    async def test_a_forbidden_create_gives_a_plain_cause(self) -> None:
        self.guild.create_text_channel = AsyncMock(side_effect=FORBIDDEN)
        with self.assertRaisesRegex(TranscriptChannelError, "Manage Channels"):
            await setup_transcript_channel(self.guild, self.campaign.id, self.store)

    async def test_a_numbered_campaign_numbers_its_transcript_too(self) -> None:
        campaign = await self.store.set_channel_number(GUILD, self.campaign.id, 2)
        self.assertEqual(transcript_channel_name(campaign), "dmb-transcript-2rmfthfrstmdn")
        self.assertTrue(is_transcript_name("dmb-transcript-2rmfthfrstmdn"))
        self.assertFalse(is_transcript_name("general"))
