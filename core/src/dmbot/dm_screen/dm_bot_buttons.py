"""The DM Bot channel's panel and what its buttons do (#1666; docs/PLAN.md, "The DM Bot
channel").

One shared message holds two buttons everyone may press: 👤 My buttons and ❓ Help. 👤 answers
privately with the buttons that person's role gets (`dm_bot_panel.buttons_for`), re-checked from
the campaign as it is now on every press, and those buttons are the menus DMbot already has
(the ⚙️ Menu, 📜 My character sheet, ⚙️ Settings), each of which checks the person again. Every
answer is private; nothing from the DM screen is ever posted here.

The panel message is kept by DMbot: edited in place when it exists, posted again when it was
deleted, so a deleted panel never leaves the channel empty.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot import consent_dm
from dmbot.campaigns import Campaign
from dmbot.dm_screen import dm_bot_panel as panel
from dmbot.dm_screen.dm_bot_panel import Button, Role, Standing
from dmbot.dm_screen.settings import SettingsButton

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
_ID = r"(?P<campaign>[0-9a-f]{32})"
GONE = "DMbot can't find that campaign. Ask your DM."
NOT_HERE = "Press that in the server it belongs to."
CARD_TEXT = {
    Role.GAME_MASTER: "You are a DM here. These are your buttons:",
    Role.PLAYER: "You play here. These are your buttons:",
    Role.MEMBER: "These are your buttons:",
    Role.GUEST: "These are your buttons:",
}


async def standing_of(client: Any, campaign: Campaign, user_id: int) -> Standing:
    """What the role depends on, read now. A failed read counts as nothing (the weakest
    role), never as more, so a hiccup can only hide buttons."""
    in_campaign = False
    memory = getattr(client, "memory", None)
    if memory is not None:
        try:
            in_campaign = await memory.is_player_here(campaign.guild_id, campaign.id, user_id)
        except Exception:
            log.warning("Couldn't tell whether someone plays in a campaign", exc_info=True)
    consented = False
    try:
        consented = await client.recorded(campaign.guild_id, user_id)
    except Exception:
        log.warning("Couldn't read someone's consent for the DM Bot panel", exc_info=True)
    return Standing(
        is_game_master=user_id in campaign.dm_user_ids,
        in_campaign=in_campaign,
        consented=consented,
    )


def role_view(role: Role, campaign: Campaign, *, sheets: bool) -> discord.ui.View:
    """The buttons that role gets, built from `buttons_for` and nothing else."""
    view = discord.ui.View(timeout=None)
    for button in panel.buttons_for(role):
        if button is Button.HELP:
            view.add_item(HelpButton(campaign.id))
        elif button is Button.MY_MENU:
            view.add_item(consent_dm.MenuButton(campaign.guild_id, campaign.id))
        elif button is Button.MY_CHARACTER and sheets:
            view.add_item(consent_dm.sheet_button(campaign.guild_id, campaign.id))
        elif button is Button.SETTINGS:
            view.add_item(SettingsButton(campaign.id))
    return view


async def _campaign_here(interaction: discord.Interaction, campaign_id: str) -> Campaign | None:
    """The campaign, only if it belongs to the server this was pressed in: a panel can never
    act on another server's campaign."""
    guild = interaction.guild
    if guild is None:
        return None
    store = getattr(interaction.client, "campaigns", None)
    if store is None:
        return None
    campaign: Campaign | None = await store.get(guild.id, campaign_id)
    return campaign


class OpenButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:panel:open:{_ID}",
):
    """👤 My buttons: the person's own buttons, privately."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=panel.LABELS[Button.OPEN],
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:panel:open:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> OpenButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        campaign = await _campaign_here(interaction, self.campaign_id)
        if campaign is None:
            await interaction.response.send_message(GONE, ephemeral=True)
            return
        who = await standing_of(interaction.client, campaign, interaction.user.id)
        role = panel.role_of(who)
        sheets = getattr(interaction.client, "sheets", None) is not None
        await interaction.response.send_message(
            CARD_TEXT[role],
            view=role_view(role, campaign, sheets=sheets),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )


class HelpButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:panel:help:{_ID}",
):
    """❓ Help: DMbot's short help, privately, for anyone."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=panel.LABELS[Button.HELP],
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:panel:help:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> HelpButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.ui import logic

        campaign = await _campaign_here(interaction, self.campaign_id)
        if campaign is None:
            await interaction.response.send_message(GONE, ephemeral=True)
            return
        await interaction.response.send_message(
            logic.HELP_TEXT, ephemeral=True, allowed_mentions=NO_PINGS
        )


def panel_view(campaign_id: str) -> discord.ui.View:
    """The shared message's buttons: only what every role may press (`shared_panel`)."""
    view = discord.ui.View(timeout=None)
    for button in panel.shared_panel():
        view.add_item(OpenButton(campaign_id) if button is Button.OPEN else HelpButton(campaign_id))
    return view


async def ensure_panel(client: Any, campaign: Campaign) -> discord.Message | None:
    """Edit the panel in place, or post it again if it was deleted. Returns the message, or
    None if nothing could be shown (a failure here never stops anything)."""
    if campaign.dm_bot_channel_id is None:
        return None
    channel = client.get_channel(campaign.dm_bot_channel_id)
    if not isinstance(channel, discord.abc.Messageable):
        return None
    view = panel_view(campaign.id)
    if campaign.dm_bot_message_id:
        try:
            message = await channel.fetch_message(campaign.dm_bot_message_id)
            await message.edit(content=panel.PANEL_TEXT, view=view, allowed_mentions=NO_PINGS)
            return message
        except discord.NotFound:
            pass  # deleted: post a new one below
        except discord.HTTPException:
            # A hiccup (rate limit, Discord error): don't pile a second panel beside it.
            log.warning("Couldn't edit the DM Bot panel")
            return None
    message = await client.post_message(campaign.dm_bot_channel_id, panel.PANEL_TEXT, view)
    if message is None:
        return None
    await client.campaigns.set_dm_bot_message(campaign.guild_id, campaign.id, message.id)
    return message  # type: ignore[no-any-return]  # post_message is typed loosely in tests
