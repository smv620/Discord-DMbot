"""🗄️ Transcript retention, in ⚙️ Settings → 🗂️ Campaign management (#1533): how long the
campaign keeps its transcripts.

The DM picks Forever (the default), 1 year, 90 days or 30 days. The daily retention job
(`transcript.keep`) deletes the transcript of every session that ended longer ago than that. A
longer choice just saves. A **shorter** one first shows one plain warning with what goes and what
stays, and saves only on its one button, because deleted transcripts can't come back.

Only the campaign's DMs. Every press checks that again, so an old menu can't be used by someone
who is no longer one. The buttons' IDs carry the campaign and the choice, so they survive a
restart (`bot.add_dynamic_items(...)` with `KEEP_BUTTONS`).
"""

from __future__ import annotations

import logging
import re
from typing import Any, cast

import discord

from dmbot.campaigns import Campaign, CampaignError
from dmbot.logs import set_log_context
from dmbot.transcript.keep import KEEP_LABELS

log = logging.getLogger(__name__)

_ID = r"(?P<campaign>[0-9a-f]{32})"
_CHOICE = r"(?P<choice>forever|365|90|30)"
CHOICES: tuple[tuple[str, int | None], ...] = (
    ("forever", None),
    ("365", 365),
    ("90", 90),
    ("30", 30),
)
LABEL_OF: dict[str, str] = {"forever": "Forever", **{str(d): w for d, w in KEEP_LABELS.items()}}
ONLY_DMS = "Only this campaign's DMs can change that."
GONE = "That campaign isn't here any more. Open ⚙️ Settings again."
COULDNT = "That didn't work, and nothing changed. Please try again in a moment."
INTRO = "📜 **Keep transcripts for**"


def days_of(choice: str) -> int | None:
    return None if choice == "forever" else int(choice)


def _shorter(new: int | None, old: int | None) -> bool:
    """Does the new choice keep transcripts for less time than the old one?"""
    if new is None:
        return False
    return old is None or new < old


def choice_of(campaign: Campaign) -> str:
    days = campaign.transcript_keep_days
    return "forever" if days is None else str(days)


def panel_text(campaign: Campaign) -> str:
    current = LABEL_OF[choice_of(campaign)]
    days = campaign.transcript_keep_days
    after = (
        "DMbot keeps them until the campaign is deleted."
        if days is None
        else f"Each night, sessions older than {KEEP_LABELS[days]} are deleted, with their "
        "downloads on the website. Recaps and the facts you confirmed stay."
    )
    return f"{INTRO}: **{current}**\nAnyone in the server can read them. {after}"


def warning_text(days: int) -> str:
    """The one warning before a shorter limit is saved. It says when the first clean-up is, since
    a DM's first worry is whether old sessions go at once."""
    return (
        f"Sessions older than {KEEP_LABELS[days]} will be deleted every night, with their "
        "downloads on the website. The first clean-up is tonight. Recaps and the facts you "
        "confirmed stay. Nobody can get the deleted transcripts back."
    )


def saved_text(days: int | None) -> str:
    if days is None:
        return "Saved. DMbot keeps transcripts until the campaign is deleted."
    return f"Saved. Sessions older than {KEEP_LABELS[days]} are deleted every night."


def panel_view(campaign: Campaign) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for choice, days in CHOICES:
        view.add_item(
            KeepChoiceButton(campaign.id, choice, current=days == campaign.transcript_keep_days)
        )
    return view


async def _dm_campaign(interaction: discord.Interaction, campaign_id: str) -> Campaign | None:
    """The campaign as it is now, if this person is one of its DMs; else they are told."""
    store = getattr(interaction.client, "campaigns", None)
    guild = interaction.guild
    if store is None or guild is None:
        await interaction.response.send_message(COULDNT, ephemeral=True)
        return None
    try:
        campaign = await store.get(guild.id, campaign_id)
    except Exception:
        log.exception("Couldn't load a campaign for the transcript setting")
        await interaction.response.send_message(COULDNT, ephemeral=True)
        return None
    if campaign is None:
        await interaction.response.send_message(GONE, ephemeral=True)
        return None
    if interaction.user.id not in campaign.dm_user_ids:
        await interaction.response.send_message(ONLY_DMS, ephemeral=True)
        return None
    return cast(Campaign, campaign)


class TranscriptsButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:keep:{_ID}",
):
    """🗄️ Transcript retention, in ⚙️ Settings → 🗂️ Campaign management: opens the private panel
    above. DMs only."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Transcript retention",
                emoji="🗄️",
                style=discord.ButtonStyle.secondary,
                row=2,  # (the rows below it are full; this one holds the owner's buttons)
                custom_id=f"dmbot:keep:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> TranscriptsButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None:
            return
        await interaction.response.send_message(
            panel_text(campaign), view=panel_view(campaign), ephemeral=True
        )


class KeepChoiceButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:keepset:{_ID}:{_CHOICE}",
):
    """One of Forever / 1 year / 90 days / 30 days. A longer choice saves at once; a shorter
    one asks first (`warning_text`)."""

    def __init__(self, campaign_id: str, choice: str, *, current: bool = False) -> None:
        super().__init__(
            discord.ui.Button(
                label=LABEL_OF[choice],
                style=discord.ButtonStyle.primary if current else discord.ButtonStyle.secondary,
                custom_id=f"dmbot:keepset:{campaign_id}:{choice}",
            )
        )
        self.campaign_id, self.choice = campaign_id, choice

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> KeepChoiceButton:
        return cls(match["campaign"], match["choice"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None:
            return
        days = days_of(self.choice)
        if days == campaign.transcript_keep_days:
            await interaction.response.edit_message(
                content=panel_text(campaign), view=panel_view(campaign)
            )
            return
        if _shorter(days, campaign.transcript_keep_days):
            assert days is not None  # (shorter than the old one means a limit)
            view = discord.ui.View(timeout=None)
            view.add_item(KeepYesButton(campaign.id, self.choice))
            view.add_item(KeepNoButton(campaign.id))
            await interaction.response.edit_message(content=warning_text(days), view=view)
            return
        await _save(interaction, campaign, days)


async def _save(interaction: discord.Interaction, campaign: Campaign, days: int | None) -> None:
    # Answer Discord first (#1798): saving is a database write and the card is read back after.
    await interaction.response.defer()
    store = interaction.client.campaigns  # type: ignore[attr-defined]
    try:
        saved = await store.set_transcript_keep(campaign.guild_id, campaign.id, days)
    except CampaignError as exc:
        await interaction.edit_original_response(content=str(exc), view=None)
        return
    except Exception:
        log.exception("Couldn't save the transcript setting")
        await interaction.edit_original_response(content=COULDNT, view=None)
        return
    await interaction.edit_original_response(
        content=f"{saved_text(days)}\n\n{panel_text(saved)}",
        view=panel_view(saved),
    )


class KeepYesButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:keepyes:{_ID}:{_CHOICE}",
):
    """Delete after N days: saves the shorter limit the warning was about. Named for what it
    does, since it sits in red beside Cancel and a "Keep" label would read as the safe one."""

    def __init__(self, campaign_id: str, choice: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=f"Delete after {LABEL_OF[choice]}",
                style=discord.ButtonStyle.danger,
                custom_id=f"dmbot:keepyes:{campaign_id}:{choice}",
            )
        )
        self.campaign_id, self.choice = campaign_id, choice

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> KeepYesButton:
        return cls(match["campaign"], match["choice"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        campaign = await _dm_campaign(interaction, self.campaign_id)  # checked again
        if campaign is None:
            return
        await _save(interaction, campaign, days_of(self.choice))


class KeepNoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:keepno:{_ID}",
):
    """Keep as it is: nothing changes, and the panel comes back."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Keep as it is",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:keepno:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> KeepNoButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None:
            return
        await interaction.response.edit_message(
            content=panel_text(campaign), view=panel_view(campaign)
        )


KEEP_BUTTONS = (TranscriptsButton, KeepChoiceButton, KeepYesButton, KeepNoButton)
