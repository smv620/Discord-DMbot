"""setup_dm_screen against a fake Discord server: what it creates, edits and saves."""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.campaigns import CampaignStore
from dmbot.dm_screen import setup_dm_screen
from tests.pg import DatabaseTest

GUILD, BOT, DM, GENERAL, NEW = 1, 2, 3, 40, 50
BOT_PERMS = discord.Permissions(
    view_channel=True,
    send_messages=True,
    read_message_history=True,
    manage_channels=True,
    manage_roles=True,
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
        assert old.edit.call_args.kwargs["name"] == "dmb-dm-screen-frostmaiden"

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
