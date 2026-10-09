"""⏸️ Pause this campaign / ▶️ Unpause, on ⚙️ Settings (#957; docs/PLAN.md, "Plans and
pricing"). A paused campaign keeps everything, can't start and doesn't count toward its
owner's plan. Only the owner has the button: it is their plan's room that changes. The
other DMs see the state, and are told to ask the owner.

The buttons' IDs carry the campaign, so they work after a restart. Register with
`bot.add_dynamic_items(PauseButton)`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.campaigns.models import CampaignError
from dmbot.dm_screen import handover, messages
from dmbot.logs import set_log_context

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
_ID = r"(?P<campaign>[0-9a-f]{32})"
PAUSE_LABEL = "Pause this campaign"
UNPAUSE_LABEL = "Unpause"
ONLY_OWNER = "Only the campaign's owner can pause or unpause it. Ask them to."
FAILED = "Sorry, that didn't save. Please try again."
# What each person reads about the plan differs: only the owner is told how pausing
# touches their plan (CLAUDE.md: nobody learns another person's plan).
LINE_RUNNING_OWNER = (
    "• **Paused:** no. Pausing keeps everything but stops it from starting, and frees a "
    "place on your plan."
)
LINE_PAUSED_OWNER = (
    "• **Paused:** yes. It can't start. Everything is kept. Press ▶️ **Unpause** to use it again."
)
LINE_RUNNING_OTHER = "• **Paused:** no."
LINE_PAUSED_OTHER = "• **Paused:** yes. It can't start. Ask the campaign's owner to unpause it."


def pause_line(campaign: Campaign, viewer: int | None) -> str:
    owner = viewer is not None and viewer == campaign.owner_user_id
    if campaign.paused:
        return LINE_PAUSED_OWNER if owner else LINE_PAUSED_OTHER
    return LINE_RUNNING_OWNER if owner else LINE_RUNNING_OTHER


def pause_buttons(campaign: Campaign, viewer: int) -> list[discord.ui.Item[Any]]:
    """The owner's button for the card (it is private, so `viewer` is who is looking)."""
    if campaign.owner_user_id is None or viewer != campaign.owner_user_id:
        return []
    return [PauseButton(campaign.id, paused=campaign.paused)]


class PauseButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:pause:{_ID}:(?P<to>pause|unpause)",
):
    """⏸️ Pause this campaign, or ▶️ Unpause when it is paused. The owner only."""

    def __init__(self, campaign_id: str, *, paused: bool) -> None:
        to = "unpause" if paused else "pause"
        super().__init__(
            discord.ui.Button(
                label=UNPAUSE_LABEL if paused else PAUSE_LABEL,
                emoji="▶️" if paused else "⏸️",
                style=discord.ButtonStyle.primary if paused else discord.ButtonStyle.secondary,
                row=2,
                custom_id=f"dmbot:pause:{campaign_id}:{to}",
            )
        )
        self.campaign_id = campaign_id
        self.pausing = not paused  # what a press does

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> PauseButton:
        return cls(match["campaign"], paused=match["to"] == "unpause")

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        guild = interaction.guild
        store = getattr(interaction.client, "campaigns", None)
        change = getattr(interaction.client, "set_paused", None)
        if guild is None or store is None or change is None:
            await interaction.response.send_message(FAILED, ephemeral=True)
            return
        await interaction.response.defer()  # saving may take a moment
        try:
            campaign: Campaign | None = await store.get(guild.id, self.campaign_id)
            if campaign is None:
                await interaction.followup.send(messages.DM_CAMPAIGN_GONE, ephemeral=True)
                return
            # Checked here as well as in the store, which is the last word: the owner's
            # card is private, but a button can outlive a hand-over.
            if campaign.owner_user_id != interaction.user.id:
                await interaction.followup.send(ONLY_OWNER, ephemeral=True)
                return
            saved: Campaign = await change(
                guild.id, self.campaign_id, interaction.user.id, self.pausing
            )
        except CampaignError as exc:
            await interaction.followup.send(str(exc), ephemeral=True, allowed_mentions=NO_PINGS)
            return
        except Exception:
            log.exception("Couldn't pause or unpause a campaign")
            await interaction.followup.send(FAILED, ephemeral=True)
            return
        await handover.redraw_card(interaction, saved)
