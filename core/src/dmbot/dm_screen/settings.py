"""⚙️ Settings on the DM screen (#515): the campaign's settings, privately, one tap per
change. It sits on the "Listening" message next to Stop listening. Only the campaign's
DMs, or a server manager, may open it or change anything.

- **How much DMbot says** (Quiet / Normal, #504): saved for the campaign, and a running
  session follows it from the next line (`DMBot.set_screen_level`).
- **Who can see the DM screen:** the help card's own buttons (`VisibilityButton`).
- **Saved transcripts:** where to find them (there's nothing to change).

The buttons' IDs carry the campaign, so they work after a restart. Register with
`bot.add_dynamic_items(SettingsButton, LevelButton)`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.campaigns import Campaign, CampaignStore
from dmbot.campaigns.models import (
    DEFAULT_DM_SCREEN_LEVEL,
    DM_SCREEN_LEVELS,
    DM_SCREEN_LEVELS_OFFERED,
    CampaignError,
)
from dmbot.dm_screen import messages
from dmbot.dm_screen.buttons import VisibilityButton

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
_ID = r"(?P<campaign>[0-9a-f]{32})"
SETTINGS_LABEL = "Settings"
ONLY_DM = "Only this campaign's DM (or a server manager) can change its settings."
FAILED = "Sorry, that didn't save. Please try again."


def settings_text(campaign: Campaign) -> str:
    """The settings card (only the person who opened it sees it)."""
    level = campaign.dm_screen_level
    recommended = " (recommended)" if level == DEFAULT_DM_SCREEN_LEVEL else ""
    who = messages.WHO_CAN_SEE.get(campaign.dm_screen_visibility, "")
    return "\n".join(
        [
            f"⚙️ **Settings for {discord.utils.escape_markdown(campaign.name)}** "
            "(only you can see this)",
            f"• **{level.capitalize()}**{recommended}: how much DMbot says, in the DM screen "
            f"only: {DM_SCREEN_LEVELS.get(level, level)}. Warnings always show.",
            f"• **Who can see the DM screen:** {who}",
            "• **Saved transcripts:** anyone in the server can read and download them with "
            "`/transcript`.",
            "Tap a button to change a setting. A change works at once, even mid-session.",
        ]
    )


def settings_view(campaign: Campaign) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for level in DM_SCREEN_LEVELS_OFFERED:
        view.add_item(LevelButton(campaign.id, level, current=level == campaign.dm_screen_level))
    for visibility in messages.VISIBILITY_BUTTONS:
        current = visibility == campaign.dm_screen_visibility
        item = VisibilityButton(campaign.id, visibility, current=current)
        item.row = 1
        view.add_item(item)
    return view


async def _allowed(interaction: discord.Interaction, campaign_id: str) -> Campaign | None:
    """The campaign, if this person may change its settings; otherwise None, after
    telling them why. Scoped to the server the button was pressed in."""
    store = getattr(interaction.client, "campaigns", None)
    guild, member = interaction.guild, interaction.user
    campaign = (
        await store.get(guild.id, campaign_id)
        if isinstance(store, CampaignStore) and guild is not None
        else None
    )
    if campaign is None:
        await interaction.response.send_message(messages.CAMPAIGN_GONE, ephemeral=True)
        return None
    manager = isinstance(member, discord.Member) and member.guild_permissions.manage_guild
    if member.id not in campaign.dm_user_ids and not manager:
        await interaction.response.send_message(ONLY_DM, ephemeral=True)
        return None
    return campaign


class SettingsButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:settings:{_ID}",
):
    """⚙️ Settings: opens the campaign's settings privately."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=SETTINGS_LABEL,
                emoji="⚙️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:settings:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> SettingsButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        campaign = await _allowed(interaction, self.campaign_id)
        if campaign is None:
            return
        await interaction.response.send_message(
            settings_text(campaign),
            view=settings_view(campaign),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )


class LevelButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:level:{_ID}:(?P<level>quiet|normal|chatty)",
):
    """How much DMbot says: one tap saves it, and the card shows the new choice."""

    def __init__(self, campaign_id: str, level: str, *, current: bool = False) -> None:
        super().__init__(
            discord.ui.Button(
                label=f"✓ {level.capitalize()}" if current else level.capitalize(),
                style=discord.ButtonStyle.primary if current else discord.ButtonStyle.secondary,
                disabled=current,
                row=0,
                custom_id=f"dmbot:level:{campaign_id}:{level}",
            )
        )
        self.campaign_id = campaign_id
        self.level = level

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> LevelButton:
        return cls(match["campaign"], match["level"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        campaign = await _allowed(interaction, self.campaign_id)
        bot: Any = interaction.client
        if campaign is None:
            return
        try:
            campaign = await bot.set_screen_level(campaign.guild_id, campaign.id, self.level)
        except CampaignError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except Exception:
            log.exception("Couldn't change how much DMbot says")
            await interaction.response.send_message(FAILED, ephemeral=True)
            return
        await interaction.response.edit_message(
            content=settings_text(campaign), view=settings_view(campaign), allowed_mentions=NO_PINGS
        )
