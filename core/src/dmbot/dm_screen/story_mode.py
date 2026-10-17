"""🧭 Shared story on ⚙️ Settings (#1692; docs/STORY_MEMORY.md, section 6): the DM picks Off,
Watch the story or Guide me.

This is only the switch and the DM's confirmation. Nothing reads the mode yet: reading the
adventure, telling the DM when the table and the book no longer fit ("Watch the story") and
saying what the book has next ("Guide me") are the next slices of Phase 5d, and the card says
so, so the DM isn't misled.

Turning it on the first time shows the warning from CLAUDE.md's IP rule ("Only give DMbot
material you have the right to use. DMbot doesn't check this.") and saves only on its
**I have the right** button, which records who confirmed and when. Without that press the mode
stays Off. Off keeps the record, so turning it on again doesn't ask again.

Only the campaign's DMs, checked again on every press. The buttons' IDs carry the campaign and
the mode, so they survive a restart (`bot.add_dynamic_items` names the four classes). Offered on
every plan: docs/PLAN.md names no plan rule for the switch.
"""

from __future__ import annotations

import logging
import re
from typing import Any, cast

import discord

from dmbot.campaigns import Campaign, CampaignError
from dmbot.campaigns.models import (
    STORY_GUIDE,
    STORY_MODES,
    STORY_OFF,
    STORY_RIGHT_WARNING,
    STORY_WATCH,
)
from dmbot.logs import set_log_context

log = logging.getLogger(__name__)

_ID = r"(?P<campaign>[0-9a-f]{32})"
_MODE = r"(?P<mode>off|watch|guide)"
LABEL_OF = {STORY_OFF: "Off", STORY_WATCH: "Watch the story", STORY_GUIDE: "Guide me"}
MEANING = {
    STORY_OFF: "DMbot remembers your table's story only.",
    STORY_WATCH: "DMbot also tells you when your table and the book no longer fit.",
    STORY_GUIDE: (
        "Everything in Watch the story, plus: when you ask, DMbot says what the book has next, "
        "with the page."
    ),
}
ONLY_DMS = "Only this campaign's DMs can change that. Ask one of them."
GONE = "That campaign isn't here any more. Open ⚙️ Settings again."
COULDNT = "That didn't work, and nothing changed. Please try again in a moment."
INTRO = "🧭 **Shared story**"
COMING = "Not working yet. Soon you will be able to give DMbot your adventure."
RIGHT_LABEL = "I have the right"
NOT_NOW_LABEL = "Not now"


def line(campaign: Campaign) -> str:
    """The Settings card's one line about it."""
    mode = campaign.story_mode
    label = LABEL_OF.get(mode, "Off")
    # Nothing reads the mode yet, and this is the line DMs look at: say so, so no one waits for
    # alerts that cannot come. Remove with the caveat when the reader ships.
    pending = "" if mode == STORY_OFF else " (not working yet)"
    return f"• **Shared story:** {label}{pending}. Coming soon: DMbot will use your adventure."


def panel_text(campaign: Campaign) -> str:
    current = LABEL_OF.get(campaign.story_mode, "Off")
    return (
        f"{INTRO}: **{current}**\n{MEANING.get(campaign.story_mode, MEANING[STORY_OFF])}\n{COMING}"
    )


def warning_text(mode: str) -> str:
    """Shown once, the first time it is turned on. It says what pressing does."""
    return (
        f"{STORY_RIGHT_WARNING}\n"
        f"Press **{RIGHT_LABEL}** to turn on **{LABEL_OF[mode]}**. That is your word that you may "
        "use your adventure. DMbot keeps a note of who said so, and when. Nothing is read yet."
    )


def panel_view(campaign: Campaign) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for mode in STORY_MODES:
        view.add_item(StoryChoiceButton(campaign.id, mode, current=mode == campaign.story_mode))
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
        log.exception("Couldn't load a campaign for the Shared story setting")
        await interaction.response.send_message(COULDNT, ephemeral=True)
        return None
    if campaign is None:
        await interaction.response.send_message(GONE, ephemeral=True)
        return None
    if interaction.user.id not in campaign.dm_user_ids:
        await interaction.response.send_message(ONLY_DMS, ephemeral=True)
        return None
    return cast(Campaign, campaign)


async def _save(
    interaction: discord.Interaction, campaign: Campaign, mode: str, *, who: int | None
) -> None:
    store = interaction.client.campaigns  # type: ignore[attr-defined]
    try:
        saved = await store.set_story_mode(campaign.guild_id, campaign.id, mode, confirmed_by=who)
    except CampaignError as exc:
        await interaction.response.edit_message(content=str(exc), view=panel_view(campaign))
        return
    except Exception:
        log.exception("Couldn't save the Shared story setting")
        await interaction.response.edit_message(content=COULDNT, view=panel_view(campaign))
        return
    await interaction.response.edit_message(
        content=f"Saved.\n\n{panel_text(saved)}", view=panel_view(saved)
    )


class StoryButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:story:{_ID}",
):
    """🧭 Shared story, on ⚙️ Settings: opens the private panel above. DMs only."""

    def __init__(self, campaign_id: str, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label="Shared story",
                emoji="🧭",
                style=discord.ButtonStyle.secondary,
                row=row,
                custom_id=f"dmbot:story:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> StoryButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None:
            return
        await interaction.response.send_message(
            panel_text(campaign), view=panel_view(campaign), ephemeral=True
        )


class StoryChoiceButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:storyset:{_ID}:{_MODE}",
):
    """One of Off / Watch the story / Guide me. Off, the one already chosen and a mode the
    campaign has confirmed save at once; the first time it is turned on, the warning comes
    first (`warning_text`)."""

    def __init__(self, campaign_id: str, mode: str, *, current: bool = False) -> None:
        super().__init__(
            discord.ui.Button(
                label=LABEL_OF[mode],
                style=discord.ButtonStyle.primary if current else discord.ButtonStyle.secondary,
                custom_id=f"dmbot:storyset:{campaign_id}:{mode}",
            )
        )
        self.campaign_id, self.mode = campaign_id, mode

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> StoryChoiceButton:
        return cls(match["campaign"], match["mode"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None:
            return
        if self.mode == campaign.story_mode:
            await interaction.response.edit_message(
                content=panel_text(campaign), view=panel_view(campaign)
            )
            return
        if self.mode != STORY_OFF:
            store = interaction.client.campaigns  # type: ignore[attr-defined]
            try:
                known = await store.story_confirmed(campaign.guild_id, campaign.id)
            except Exception:
                log.exception("Couldn't read the Shared story confirmation")
                await interaction.response.send_message(COULDNT, ephemeral=True)
                return
            if not known:
                view = discord.ui.View(timeout=None)
                view.add_item(StoryYesButton(campaign.id, self.mode))
                view.add_item(StoryNoButton(campaign.id))
                await interaction.response.edit_message(content=warning_text(self.mode), view=view)
                return
        await _save(interaction, campaign, self.mode, who=None)


class StoryYesButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:storyyes:{_ID}:{_MODE}",
):
    """I have the right: records who and when, and turns the mode on."""

    def __init__(self, campaign_id: str, mode: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=RIGHT_LABEL,
                style=discord.ButtonStyle.success,
                custom_id=f"dmbot:storyyes:{campaign_id}:{mode}",
            )
        )
        self.campaign_id, self.mode = campaign_id, mode

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> StoryYesButton:
        return cls(match["campaign"], match["mode"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        campaign = await _dm_campaign(interaction, self.campaign_id)  # checked again
        if campaign is None:
            return
        if self.mode == STORY_OFF:  # (off never asks: nothing to confirm)
            await _save(interaction, campaign, STORY_OFF, who=None)
            return
        await _save(interaction, campaign, self.mode, who=interaction.user.id)


class StoryNoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:storyno:{_ID}",
):
    """Not now: nothing changes, and the panel comes back."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=NOT_NOW_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:storyno:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> StoryNoButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None:
            return
        await interaction.response.edit_message(
            content=panel_text(campaign), view=panel_view(campaign)
        )


STORY_BUTTONS = (StoryButton, StoryChoiceButton, StoryYesButton, StoryNoButton)
