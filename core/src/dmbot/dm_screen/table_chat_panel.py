"""💬 Table chat on ⚙️ Settings (#1118): which typed chat DMbot reads, and the DM's switch.

DMbot always reads the table voice channel's own text chat while it listens (from people who
agreed), and at most one more text channel the DM picks here. Least privilege: the panel says
exactly which channels are read, one tap turns it all off, and nothing else is ever read. A
running session follows the change from now on (`DMBot.after_chat_change`). Only the
campaign's DMs, or a server manager, may open or change it.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.dm_screen import messages
from dmbot.logs import set_log_context
from dmbot.mentions import NO_PINGS

log = logging.getLogger(__name__)

_ID = r"(?P<campaign>[0-9a-f]{32})"
ONLY_DM = (
    "Only this campaign's DM (or a server manager) can change table chat. "
    "Ask them to open ⚙️ Settings."
)
FAILED = "Sorry, that didn't save. Please try again."
NOT_THAT_ONE = (
    "That channel is for the DM screen or the transcript, so it can't be table chat. "
    "Pick a different text channel."
)
TEXT_TYPES = [discord.ChannelType.text]


def line(campaign: Campaign) -> str:
    """The one-line status on the settings card."""
    if not campaign.table_chat:
        return "• **Table chat: Off.** DMbot doesn't read what people type."
    extra = f" and <#{campaign.table_chat_channel_id}>" if campaign.table_chat_channel_id else ""
    return (
        "• **Table chat: On.** Reads typing from people who agreed, in the voice channel's "
        f"chat{extra}."
    )


def _where(campaign: Campaign) -> str:
    extra = f" and <#{campaign.table_chat_channel_id}>" if campaign.table_chat_channel_id else ""
    return (
        "While it's listening, DMbot reads what people who agreed to be recorded type in the "
        f"voice channel's chat{extra}. No other channel."
    )


def panel_text(campaign: Campaign) -> str:
    if not campaign.table_chat:
        return (
            "💬 **Table chat is off.** DMbot doesn't read what people type. "
            'Turn it on to let DMbot also pick up "we take a long rest" typed in chat. '
            "Only people who agreed to the new consent message are read. Typed lines are in "
            "transcripts the whole server can read."
        )
    return (
        f"💬 **Table chat is on.** {_where(campaign)}\n"
        "It uses typed lines the same way as spoken ones, and they show in the transcript "
        "marked (typed). DMbot only advises you. "
        "Pick one more text channel below, or turn it off."
    )


class _Channels(discord.ui.ChannelSelect["TableChatPanel"]):
    def __init__(self, campaign: Campaign) -> None:
        default = (
            [
                discord.SelectDefaultValue(
                    id=campaign.table_chat_channel_id, type=discord.SelectDefaultValueType.channel
                )
            ]
            if campaign.table_chat_channel_id
            else []
        )
        super().__init__(
            channel_types=TEXT_TYPES,
            placeholder="Also read one text channel",
            default_values=default,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.save(interaction, channel_id=self.values[0].id)


class TableChatPanel(discord.ui.View):
    """A private panel; it can't outlive its message, so it needs no stored button IDs."""

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(timeout=600)
        self.campaign_id = campaign.id
        self.add_item(_Channels(campaign))
        switch: discord.ui.Button[TableChatPanel] = discord.ui.Button(
            label="Turn table chat off" if campaign.table_chat else "Turn table chat on",
            style=discord.ButtonStyle.secondary
            if campaign.table_chat
            else discord.ButtonStyle.primary,
            row=1,
        )
        switch.callback = self._switch  # type: ignore[method-assign]
        self.add_item(switch)
        self.turn_on = not campaign.table_chat
        if campaign.table_chat_channel_id:
            only: discord.ui.Button[TableChatPanel] = discord.ui.Button(
                label="Voice chat only", style=discord.ButtonStyle.secondary, row=1
            )
            only.callback = self._only_voice  # type: ignore[method-assign]
            self.add_item(only)

    async def _switch(self, interaction: discord.Interaction) -> None:
        await self.save(interaction, turn_on=self.turn_on)

    async def _only_voice(self, interaction: discord.Interaction) -> None:
        await self.save(interaction, clear_channel=True)

    async def save(
        self,
        interaction: discord.Interaction,
        *,
        turn_on: bool | None = None,
        channel_id: int | None = None,
        clear_channel: bool = False,
    ) -> None:
        set_log_context(guild_id=interaction.guild_id)
        guild = interaction.guild
        store = getattr(interaction.client, "campaigns", None)
        if guild is None or store is None:
            await interaction.response.send_message(FAILED, ephemeral=True)
            return
        try:
            campaign: Campaign | None = await store.get(guild.id, self.campaign_id)
        except Exception:
            log.exception("Couldn't load a campaign for table chat")
            await interaction.response.send_message(FAILED, ephemeral=True)
            return
        if campaign is None:
            await interaction.response.send_message(messages.DM_CAMPAIGN_GONE, ephemeral=True)
            return
        if not may_change(interaction, campaign):  # checked again: the panel may be old
            await interaction.response.send_message(ONLY_DM, ephemeral=True)
            return
        if channel_id is not None:
            chosen = guild.get_channel(channel_id)  # only a channel of this server
            if not isinstance(chosen, discord.TextChannel):
                await interaction.response.send_message(FAILED, ephemeral=True)
                return
            # DM-screen content never goes into transcripts, so no campaign's DM screen,
            # transcript channel in this server is ever read as chat.
            try:
                special = await store.special_channel_ids(guild.id)
            except Exception:
                log.exception("Couldn't list the channels that can't be table chat")
                await interaction.response.send_message(FAILED, ephemeral=True)
                return
            if channel_id in special:
                await interaction.response.send_message(NOT_THAT_ONE, ephemeral=True)
                return
        try:
            saved: Campaign = await store.set_table_chat(
                guild.id,
                campaign.id,
                on=campaign.table_chat if turn_on is None else turn_on,
                channel_id=None
                if clear_channel
                else channel_id
                if channel_id is not None
                else campaign.table_chat_channel_id,
            )
        except Exception:
            log.exception("Couldn't save table chat")
            await interaction.response.send_message(FAILED, ephemeral=True)
            return
        follow = getattr(interaction.client, "after_chat_change", None)
        if follow is not None:
            follow(saved)  # a running session follows from now on
        await interaction.response.edit_message(
            content=panel_text(saved), view=TableChatPanel(saved), allowed_mentions=NO_PINGS
        )


def may_change(interaction: discord.Interaction, campaign: Campaign) -> bool:
    perms = getattr(interaction.user, "guild_permissions", None)
    return interaction.user.id in campaign.dm_user_ids or bool(
        perms is not None and perms.manage_guild
    )


class TableChatButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:tablechat:{_ID}",
):
    """💬 Table chat, on ⚙️ Settings: opens the private panel above."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Table chat",
                emoji="💬",
                style=discord.ButtonStyle.secondary,
                row=4,
                custom_id=f"dmbot:tablechat:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> TableChatButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        guild = interaction.guild
        store = getattr(interaction.client, "campaigns", None)
        campaign: Campaign | None = None
        if guild is not None and store is not None:
            try:
                campaign = await store.get(guild.id, self.campaign_id)
            except Exception:
                log.exception("Couldn't load a campaign for table chat")
                await interaction.response.send_message(messages.LOAD_FAILED, ephemeral=True)
                return
        if campaign is None:
            await interaction.response.send_message(messages.DM_CAMPAIGN_GONE, ephemeral=True)
            return
        if not may_change(interaction, campaign):
            await interaction.response.send_message(ONLY_DM, ephemeral=True)
            return
        await interaction.response.send_message(
            panel_text(campaign),
            view=TableChatPanel(campaign),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )
