"""⚙️ Settings on the DM screen (#515): the campaign's settings, privately, one tap per
change. The button sits on the DM screen's help card (there between sessions too) and on
the "Listening" message. Only the campaign's DMs, or a server manager, may open it or
change anything.

- **How much DMbot says** (Quiet / Normal, #504): saved for the campaign, and a running
  session follows the change from now on (`DMBot.set_screen_level`).
- **Who can see the DM screen:** saved the same way as the help card's buttons
  (`save_visibility`).
- **Saved transcripts:** where to find them (there's nothing to change).

Each tap redraws the card, so it always shows what's saved. The buttons' IDs carry the
campaign, so they work after a restart. Register with
`bot.add_dynamic_items(SettingsButton, LevelButton, SettingsVisibilityButton)`.
"""

from __future__ import annotations

import contextlib
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
from dmbot.dm_screen.buttons import may_change_screen, save_visibility

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
_ID = r"(?P<campaign>[0-9a-f]{32})"
SETTINGS_LABEL = "Settings"
ONLY_DM = "Only this campaign's DM (or a server manager) can open or change its settings."
GONE = "I can't find this campaign anymore. It may have been deleted. To start one: `/dmbot start`."
FAILED = "Sorry, that didn't save. Please try again."
LOAD_FAILED = "Sorry, something went wrong. Please try again."
SAVED = "Saved. Press ⚙️ Settings again to see your settings."


def _tick(label: str, current: bool) -> str:
    return f"✓ {label}" if current else label


def settings_text(campaign: Campaign) -> str:
    """The settings card (only the person who opened it sees it)."""
    level = campaign.dm_screen_level
    recommended = " (recommended)" if level == DEFAULT_DM_SCREEN_LEVEL else ""
    what = DM_SCREEN_LEVELS.get(level, level)
    who = messages.WHO_CAN_SEE.get(campaign.dm_screen_visibility, "")
    return "\n".join(
        [
            f"⚙️ **Settings for {discord.utils.escape_markdown(campaign.name)}**",
            f"• **How much DMbot says:** {level.capitalize()}{recommended}. "
            f"{what[:1].upper()}{what[1:]}. Warnings always show.",
            f"• **Who can see the DM screen:** {who}",
            "• **Saved transcripts:** anyone in the server can read and download them with "
            "`/transcript`. (This can't be changed.)",
            "Tap a button to change it. If DMbot is listening now, it follows the change from "
            "now on.",
        ]
    )


def settings_view(campaign: Campaign) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for level in DM_SCREEN_LEVELS_OFFERED:
        view.add_item(LevelButton(campaign.id, level, current=level == campaign.dm_screen_level))
    for visibility in messages.VISIBILITY_BUTTONS:
        current = visibility == campaign.dm_screen_visibility
        view.add_item(SettingsVisibilityButton(campaign.id, visibility, current=current))
    return view


async def _allowed(interaction: discord.Interaction, campaign_id: str) -> Campaign | None:
    """The campaign, if this person may change its settings; otherwise None, after
    telling them why. The same check as the help card's buttons."""
    allowed = await _may(interaction, campaign_id)
    return allowed[0] if allowed is not None else None


async def _may(
    interaction: discord.Interaction, campaign_id: str
) -> tuple[Campaign, discord.Guild, CampaignStore] | None:
    """The one check for every settings button, with the card's own words."""
    return await may_change_screen(
        interaction, campaign_id, not_dm=ONLY_DM, gone=GONE, failed=LOAD_FAILED
    )


async def _redraw(interaction: discord.Interaction, campaign: Campaign) -> None:
    """Show the card as saved now (the press was deferred as an update). If the card
    can't be redrawn (it's too old), the change still is saved: say so."""
    try:
        await interaction.edit_original_response(
            content=settings_text(campaign),
            view=settings_view(campaign),
            allowed_mentions=NO_PINGS,
        )
    except discord.HTTPException:
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(SAVED, ephemeral=True)


class SettingsButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:settings:{_ID}",
):
    """⚙️ Settings: opens the campaign's settings privately."""

    def __init__(self, campaign_id: str, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=SETTINGS_LABEL,
                emoji="⚙️",
                style=discord.ButtonStyle.secondary,
                row=row,
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
    template=rf"dmbot:level:{_ID}:(?P<level>{'|'.join(DM_SCREEN_LEVELS_OFFERED)})",
):
    """How much DMbot says: one tap saves it, and the card shows the new choice."""

    def __init__(self, campaign_id: str, level: str, *, current: bool = False) -> None:
        super().__init__(
            discord.ui.Button(
                label=_tick(level.capitalize(), current),
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
        if campaign is None:
            return
        save = getattr(interaction.client, "set_screen_level", None)
        await interaction.response.defer()  # saving may take a moment
        try:
            if save is None:
                raise RuntimeError("no running DMbot to save it")
            saved: Campaign = await save(
                campaign.guild_id, campaign.id, self.level, was=campaign.dm_screen_level
            )
        except CampaignError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except Exception:
            log.exception("Couldn't change how much DMbot says")
            await interaction.followup.send(FAILED, ephemeral=True)
            return
        await _redraw(interaction, saved)


class SettingsVisibilityButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:setvis:{_ID}:(?P<visibility>private|peek|open)",
):
    """Who can see the DM screen, on the settings card: saved like the help card's
    buttons, then the card is redrawn."""

    def __init__(self, campaign_id: str, visibility: str, *, current: bool = False) -> None:
        emoji, label = messages.VISIBILITY_BUTTONS[visibility]
        super().__init__(
            discord.ui.Button(
                label=_tick(label, current),
                emoji=emoji,
                style=discord.ButtonStyle.primary if current else discord.ButtonStyle.secondary,
                disabled=current,
                row=1,
                custom_id=f"dmbot:setvis:{campaign_id}:{visibility}",
            )
        )
        self.campaign_id = campaign_id
        self.visibility = visibility

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> SettingsVisibilityButton:
        return cls(match["campaign"], match["visibility"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        allowed = await _may(interaction, self.campaign_id)
        if allowed is None:
            return
        await interaction.response.defer()  # the card is redrawn after saving
        saved = await save_visibility(interaction, *allowed, self.visibility, quiet=True)
        if saved is not None:
            await _redraw(interaction, saved)
