"""setup_dm_screen against a fake Discord server: what it creates, edits and saves."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.campaigns import CampaignStore
from dmbot.dm_screen import messages, setup_dm_screen
from tests.pg import DatabaseTest

GUILD, BOT, DM, GENERAL, NEW, PLAYER = 1, 2, 3, 40, 50, 8
FORBIDDEN = discord.Forbidden(MagicMock(status=403), "Missing Permissions")
BOT_PERMS = discord.Permissions(
    view_channel=True,
    send_messages=True,
    read_message_history=True,
    manage_channels=True,
    manage_roles=True,
    # As in production: the bot's role inherits it from @everyone. Without it, setup
    # drops the command setting from every overwrite and #188's tests prove nothing.
    use_application_commands=True,
)


async def no_history(**_: Any) -> AsyncIterator[discord.Message]:
    return
    yield  # an async generator with nothing in it


def text_channel(channel_id: int, name: str, guild: Any) -> Any:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = name
    channel.guild = guild
    channel.overwrites = {}
    channel.edit = AsyncMock()
    channel.history = no_history
    channel.send = AsyncMock(return_value=MagicMock(pin=AsyncMock()))
    return channel


class SetupTests(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        self.campaign = await self.store.create(GUILD, "Frostmaiden", DM)

        me = MagicMock(spec=discord.Member)
        me.id = BOT
        me.guild_permissions = BOT_PERMS
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD
        guild.me = me
        guild.channels = []
        guild.get_role = lambda _id: None
        guild.get_member = lambda _id: None
        self.general = text_channel(GENERAL, "general", guild)
        self.new = text_channel(NEW, "dmb-dm-screen-frostmaiden", guild)
        guild.fetch_channel = AsyncMock(return_value=self.general)
        guild.create_text_channel = AsyncMock(return_value=self.new)
        guild.get_channel = lambda cid: self.new if cid == NEW else None
        self.guild = guild

    def created_overwrite_for(self, target_id: int) -> discord.PermissionOverwrite:
        overwrites: dict[Any, discord.PermissionOverwrite] = (
            self.guild.create_text_channel.call_args.kwargs["overwrites"]
        )
        return next(ow for key, ow in overwrites.items() if key.id == target_id)

    async def test_an_ordinary_saved_channel_is_never_taken_over(self) -> None:
        # A channel saved before DMbot made screens itself, e.g. #general.
        await self.store.set_dm_screen(GUILD, self.campaign.id, GENERAL)
        result = await setup_dm_screen(self.guild, self.campaign.id, self.store)
        self.general.edit.assert_not_called()
        self.guild.create_text_channel.assert_awaited_once()
        assert result.channel is self.new
        saved = await self.store.get(GUILD, self.campaign.id)
        assert saved is not None and saved.dm_screen_channel_id == NEW

    async def test_new_screen_is_hidden_from_everyone_and_open_to_the_dm(self) -> None:
        await setup_dm_screen(self.guild, self.campaign.id, self.store)
        assert self.created_overwrite_for(GUILD).view_channel is False  # @everyone
        assert self.created_overwrite_for(DM).send_messages is True
        assert self.created_overwrite_for(BOT).view_channel is True

    async def test_visibility_is_saved_and_applied_from_a_fresh_read(self) -> None:
        result = await setup_dm_screen(self.guild, self.campaign.id, self.store, visibility="open")
        assert result.campaign.dm_screen_visibility == "open"
        stored = await self.store.get(GUILD, self.campaign.id)
        assert stored is not None and stored.dm_screen_visibility == "open"
        everyone = self.created_overwrite_for(GUILD)
        assert everyone.view_channel is True and everyone.send_messages is False

    async def test_view_channel_is_recognised_under_discord_pys_old_name(self) -> None:
        # Permissions(view_channel=True) iterates as "read_messages"; setup must not
        # think the bot lacks View Channels.
        result = await setup_dm_screen(self.guild, self.campaign.id, self.store)
        assert result.channel is self.new

    async def test_an_existing_screen_is_updated_not_recreated(self) -> None:
        await self.store.set_dm_screen(GUILD, self.campaign.id, NEW)
        self.guild.fetch_channel = AsyncMock(return_value=self.new)
        await setup_dm_screen(self.guild, self.campaign.id, self.store, visibility="private")
        self.new.edit.assert_awaited_once()
        self.guild.create_text_channel.assert_not_called()
        # Already correctly named, so no rename (Discord rate-limits renames).
        assert "name" not in self.new.edit.call_args.kwargs

    async def test_peekers_get_their_old_command_block_lifted(self) -> None:
        # Before #190, peeks and open screens denied slash commands, which then hung.
        old = discord.PermissionOverwrite(
            view_channel=True, send_messages=False, use_application_commands=False
        )
        self.new.overwrites = {discord.Object(PLAYER, type=discord.User): old}
        await self.store.set_dm_screen(GUILD, self.campaign.id, NEW)
        self.guild.fetch_channel = AsyncMock(return_value=self.new)
        await setup_dm_screen(self.guild, self.campaign.id, self.store, visibility="peek")
        sent = self.new.edit.call_args.kwargs["overwrites"]
        peeker = next(ow for key, ow in sent.items() if key.id == PLAYER)
        assert peeker.view_channel is True and peeker.send_messages is False  # still a peek
        assert peeker.use_application_commands is None  # the server's setting applies

    async def test_an_open_screen_leaves_slash_commands_alone(self) -> None:
        await setup_dm_screen(self.guild, self.campaign.id, self.store, visibility="open")
        everyone = self.created_overwrite_for(GUILD)
        assert everyone.send_messages is False  # still view-only
        assert everyone.use_application_commands is None

    async def test_new_screen_gets_the_dmb_name(self) -> None:
        await setup_dm_screen(self.guild, self.campaign.id, self.store)
        assert self.guild.create_text_channel.call_args.args[0] == "dmb-dm-screen-frostmaiden"
        stored = await self.store.get(GUILD, self.campaign.id)
        assert stored is not None and stored.channel_number == 1

    async def test_an_old_style_screen_is_renamed(self) -> None:
        old = text_channel(NEW, "dm-screen-frostmaiden", self.guild)  # made before dmb-
        await self.store.set_dm_screen(GUILD, self.campaign.id, NEW)
        self.guild.fetch_channel = AsyncMock(return_value=old)
        await setup_dm_screen(self.guild, self.campaign.id, self.store)
        self.guild.create_text_channel.assert_not_called()
        permissions, rename = old.edit.call_args_list
        assert "overwrites" in permissions.kwargs and "name" not in permissions.kwargs
        assert rename.kwargs["name"] == "dmb-dm-screen-frostmaiden"

    async def test_a_failed_rename_still_applies_permissions(self) -> None:
        old = text_channel(NEW, "dm-screen-frostmaiden", self.guild)

        async def edit(**kwargs: Any) -> None:
            if "name" in kwargs:  # Discord's rename rate limit, say
                raise discord.HTTPException(MagicMock(status=429), "rate limited")

        old.edit = AsyncMock(side_effect=edit)
        await self.store.set_dm_screen(GUILD, self.campaign.id, NEW)
        self.guild.fetch_channel = AsyncMock(return_value=old)
        result = await setup_dm_screen(
            self.guild, self.campaign.id, self.store, visibility="private"
        )
        assert result.channel is old  # no error for the DM
        assert any("overwrites" in c.kwargs for c in old.edit.call_args_list)

    async def test_a_rename_that_waits_too_long_is_skipped(self) -> None:
        old = text_channel(NEW, "dm-screen-frostmaiden", self.guild)

        async def edit(**kwargs: Any) -> None:
            if "name" in kwargs:
                await asyncio.sleep(60)  # discord.py waiting out a rate limit

        old.edit = AsyncMock(side_effect=edit)
        await self.store.set_dm_screen(GUILD, self.campaign.id, NEW)
        self.guild.fetch_channel = AsyncMock(return_value=old)
        with patch("dmbot.dm_screen.channel.RENAME_TIMEOUT_S", 0.05):
            await setup_dm_screen(self.guild, self.campaign.id, self.store)
        assert any("overwrites" in c.kwargs for c in old.edit.call_args_list)

    async def test_a_screen_shared_by_two_campaigns_is_never_renamed(self) -> None:
        shared = text_channel(NEW, "dm-screen", self.guild)  # a hand-made #dm-screen
        other = await self.store.create(GUILD, "Curse of Strahd", DM)
        await self.store.set_dm_screen(GUILD, self.campaign.id, NEW)
        await self.store.set_dm_screen(GUILD, other.id, NEW)
        self.guild.fetch_channel = AsyncMock(return_value=shared)
        await setup_dm_screen(self.guild, self.campaign.id, self.store)
        assert all("name" not in c.kwargs for c in shared.edit.call_args_list)

    async def test_renaming_the_campaign_renames_its_screen_at_next_setup(self) -> None:
        await self.store.set_dm_screen(GUILD, self.campaign.id, NEW)
        self.guild.fetch_channel = AsyncMock(return_value=self.new)
        await self.store.rename(GUILD, self.campaign.id, "Ice Queen")
        await setup_dm_screen(self.guild, self.campaign.id, self.store)
        assert self.new.edit.call_args.kwargs["name"] == "dmb-dm-screen-ice-queen"

    async def test_a_stored_number_is_not_overwritten(self) -> None:
        first = await self.store.set_channel_number(GUILD, self.campaign.id, 2)
        again = await self.store.set_channel_number(GUILD, self.campaign.id, 3)
        assert first.channel_number == again.channel_number == 2

    async def test_a_clashing_campaign_gets_number_2_on_its_screen(self) -> None:
        first = await self.store.create(GUILD, "Frozen Sick", DM)
        await setup_dm_screen(self.guild, first.id, self.store)
        second = await self.store.create(GUILD, "Frozens Cake", DM)  # also shortens to frznsck
        await setup_dm_screen(self.guild, second.id, self.store)
        assert self.guild.create_text_channel.call_args.args[0] == "dmb-dm-screen-2frozens-cake"
        stored = await self.store.get(GUILD, second.id)
        assert stored is not None and stored.channel_number == 2

    async def test_channel_number_is_not_in_backups(self) -> None:
        await setup_dm_screen(self.guild, self.campaign.id, self.store)
        backup = await self.store.export(GUILD, self.campaign.id)
        assert "channel_number" not in backup["campaign"]

    async def with_screen(self, *existing: Any) -> None:
        """Make the screen exist already, with these bot messages in it (newest first)."""
        await self.store.set_dm_screen(GUILD, self.campaign.id, NEW)
        self.guild.fetch_channel = AsyncMock(return_value=self.new)

        async def history(**_: Any) -> AsyncIterator[discord.Message]:
            for message in existing:
                yield message

        self.new.history = history

    def bot_message(self, content: str, *, pinned: bool = False) -> Any:
        message = MagicMock(spec=discord.Message)
        message.author.id = BOT
        message.content = content
        message.pinned = pinned
        message.pin = AsyncMock()
        message.delete = AsyncMock()
        return message

    def current_card(self, *, pinned: bool) -> Any:
        text = messages.help_card("Frostmaiden", self.campaign.dm_screen_visibility)
        return self.bot_message(text, pinned=pinned)

    def new_card_pin_fails(self, exc: discord.HTTPException) -> None:
        self.new.send.return_value.pin = AsyncMock(side_effect=exc)

    async def test_a_new_help_card_is_pinned(self) -> None:
        result = await setup_dm_screen(self.guild, self.campaign.id, self.store)
        self.new.send.return_value.pin.assert_awaited_once()
        assert result.warning is None

    async def test_a_card_that_cant_be_pinned_tells_the_dm_how_to_fix_it(self) -> None:
        self.new_card_pin_fails(FORBIDDEN)
        result = await setup_dm_screen(self.guild, self.campaign.id, self.store)
        assert result.channel is self.new  # setup still works
        assert result.warning == messages.CANT_PIN

    async def test_a_pin_failure_the_dm_cant_fix_is_only_logged(self) -> None:
        # E.g. the channel's pin limit: "turn on Pin Messages" would be wrong advice.
        self.new_card_pin_fails(
            discord.HTTPException(MagicMock(status=400), "Maximum number of pins reached")
        )
        result = await setup_dm_screen(self.guild, self.campaign.id, self.store)
        assert result.channel is self.new
        assert result.warning is None

    async def test_pin_note_and_exposure_warning_are_both_given(self) -> None:
        exposed = MagicMock(spec=discord.Role)
        exposed.id = 77
        self.new.overwrites = {exposed: discord.PermissionOverwrite(view_channel=True)}
        await self.with_screen()
        self.new_card_pin_fails(FORBIDDEN)
        result = await setup_dm_screen(self.guild, self.campaign.id, self.store)
        assert result.warning is not None
        exposure, pin = result.warning.split("\n\n")
        assert "<@&77>" in exposure
        assert pin == messages.CANT_PIN

    async def test_an_unpinned_current_card_is_pinned_without_reposting(self) -> None:
        # Pin Messages was turned on after the card was posted.
        card = self.current_card(pinned=False)
        await self.with_screen(card)
        result = await setup_dm_screen(self.guild, self.campaign.id, self.store)
        card.pin.assert_awaited_once()
        self.new.send.assert_not_called()
        assert result.warning is None

    async def test_the_old_note_is_deleted_once_the_card_is_pinned(self) -> None:
        card, note = self.current_card(pinned=False), self.bot_message(messages.CANT_PIN)
        await self.with_screen(note, card)
        await setup_dm_screen(self.guild, self.campaign.id, self.store)
        note.delete.assert_awaited_once()

    async def test_a_note_shared_with_another_warning_is_kept(self) -> None:
        card = self.current_card(pinned=True)
        note = self.bot_message("⚠️ These can see this DM screen: x.\n\n" + messages.CANT_PIN)
        await self.with_screen(note, card)
        await setup_dm_screen(self.guild, self.campaign.id, self.store)
        note.delete.assert_not_called()

    async def test_the_note_isnt_repeated_while_the_screen_shows_it(self) -> None:
        # Each visibility change reposts the card; the DM was already told.
        old_card = self.bot_message(messages.HELP_CARD_TITLE + "old")
        note = self.bot_message(messages.CANT_PIN)
        await self.with_screen(note, old_card)
        self.new_card_pin_fails(FORBIDDEN)
        result = await setup_dm_screen(self.guild, self.campaign.id, self.store)
        old_card.delete.assert_awaited_once()
        self.new.send.assert_awaited_once()
        note.delete.assert_not_called()
        assert result.warning is None

    async def test_an_unpinned_card_from_before_this_note_existed_gets_one(self) -> None:
        # A screen made by an older DMbot: card unpinned, DM never told.
        card = self.current_card(pinned=False)
        card.pin.side_effect = FORBIDDEN
        await self.with_screen(card)
        result = await setup_dm_screen(self.guild, self.campaign.id, self.store)
        self.new.send.assert_not_called()
        assert result.warning == messages.CANT_PIN

    async def test_a_pinned_current_card_is_left_alone(self) -> None:
        card = self.current_card(pinned=True)
        await self.with_screen(card)
        result = await setup_dm_screen(self.guild, self.campaign.id, self.store)
        card.pin.assert_not_called()
        self.new.send.assert_not_called()
        assert result.warning is None
