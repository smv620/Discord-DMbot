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

import asyncio
import contextlib
import logging
import re
import time
from typing import Any

import discord

from dmbot.campaigns import Campaign, CampaignStore
from dmbot.campaigns.models import (
    DEFAULT_DM_SCREEN_LEVEL,
    DM_SCREEN_LEVELS,
    DM_SCREEN_LEVELS_OFFERED,
    CampaignError,
    HandoverOffer,
)
from dmbot.dm_screen import handover, messages
from dmbot.dm_screen.buttons import may_change_screen, save_visibility
from dmbot.dm_screen.clock import ClockButton
from dmbot.dm_screen.pause import pause_buttons, pause_line
from dmbot.logs import set_log_context

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
_ID = r"(?P<campaign>[0-9a-f]{32})"
SETTINGS_LABEL = "Settings"
RULE_LOOKUP_LABEL = "Look up a rule"
RULES_CARDS_OFF = (
    "• **Rules cards: Off.** When on, DMbot shows a short card on the DM screen when someone "
    "at the table names a spell, condition or creature. It only shows the free rules (SRD). "
    "You decide what applies."
)
RULES_CARDS_ON = (
    "• **Rules cards: On.** When someone who agreed to be recorded names a spell, condition "
    "or creature, DMbot shows a short card on the DM screen (one a minute at most). It only "
    "shows the free rules (SRD); you decide."
)
RULES_CARDS_STOP = " Tap 🃏 Turn rules cards off to stop them."
RULES_CARDS_PEEK = " Players who peek can see them too."
RULES_CARDS_OPEN = " Players can see them too, because your DM screen is open."
RULES_CARDS_ONLY_DMS = "Only this campaign's DMs can change this."
RULES_LINE = (
    "• **Rules:** press 📖 Look up a rule to read a spell, condition or creature from the "
    "free rules (SRD). Only you see it."
)
ONLY_DM = "Only this campaign's DM (or a server manager) can open or change its settings."
GONE = messages.DM_CAMPAIGN_GONE
FAILED = "Sorry, that didn't save. Please try again."
LOAD_FAILED = messages.LOAD_FAILED
SAVED = "Saved. Press ⚙️ Settings again to see your settings."


def _tick(label: str, current: bool) -> str:
    return f"✓ {label}" if current else label


def _rules_cards_line(campaign: Campaign, viewer: int | None) -> str:
    if not campaign.rules_cards:
        return RULES_CARDS_OFF
    who = {"peek": RULES_CARDS_PEEK, "open": RULES_CARDS_OPEN}.get(
        campaign.dm_screen_visibility, ""
    )
    stop = RULES_CARDS_STOP if viewer is not None and viewer in campaign.dm_user_ids else ""
    return RULES_CARDS_ON + who + stop


def settings_text(
    campaign: Campaign, offer: HandoverOffer | None = None, viewer: int | None = None
) -> str:
    """The settings card (only the person who opened it sees it). `viewer`: who it's for;
    the line about looking up rules is only for the campaign's DMs, who have its button."""
    level = campaign.dm_screen_level
    recommended = " (recommended)" if level == DEFAULT_DM_SCREEN_LEVEL else ""
    what = DM_SCREEN_LEVELS.get(level, level)
    who = messages.WHO_CAN_SEE.get(campaign.dm_screen_visibility, "")
    rules = [RULES_LINE] if viewer is not None and viewer in campaign.dm_user_ids else []
    return "\n".join(
        [
            f"⚙️ **Settings for {discord.utils.escape_markdown(campaign.name)}**",
            f"• **How much DMbot says:** {level.capitalize()}{recommended}. "
            f"{what[:1].upper()}{what[1:]}. Warnings always show.",
            f"• **Who can see the DM screen:** {who}",
            "• **Saved transcripts:** anyone in the server can read and download them with "
            "`/transcript`. (This can't be changed.)",
            *rules,
            _rules_cards_line(campaign, viewer),
            handover.owner_line(campaign, offer),
            pause_line(campaign, viewer),
            "Tap a button to change it. If DMbot is listening now, it follows the change from "
            "now on.",
        ]
    )


def settings_view(campaign: Campaign, offer: HandoverOffer | None, viewer: int) -> discord.ui.View:
    """The card's buttons. `viewer`: who it's for (it's private), so only the owner gets
    the hand-over button."""
    view = discord.ui.View(timeout=None)
    for level in DM_SCREEN_LEVELS_OFFERED:
        view.add_item(LevelButton(campaign.id, level, current=level == campaign.dm_screen_level))
    for visibility in messages.VISIBILITY_BUTTONS:
        current = visibility == campaign.dm_screen_visibility
        view.add_item(SettingsVisibilityButton(campaign.id, visibility, current=current))
    for item in handover.owner_buttons(campaign, offer, viewer):
        view.add_item(item)
    for item in pause_buttons(campaign, viewer):
        view.add_item(item)
    if viewer in campaign.dm_user_ids:  # rules lookup is for the campaign's DMs (#908)
        view.add_item(RuleLookupButton(campaign.id))
        view.add_item(RulesCardsButton(campaign.id, campaign.rules_cards))
        view.add_item(ClockButton(campaign.id, "open"))  # the game clock (#965)
        view.add_item(HouseFileButton(campaign.id))  # the house-rules file (#969)
    return view


def _card(campaign: Campaign, viewer: int) -> tuple[str, discord.ui.View]:
    return settings_text(campaign, None, viewer), settings_view(campaign, None, viewer)


handover.use_settings_card(_card)  # it redraws this card after an offer is taken back


async def _open_offer(interaction: discord.Interaction, campaign_id: str) -> HandoverOffer | None:
    """A hand-over offer waiting for an answer, for the card (none if it can't be read)."""
    store = getattr(interaction.client, "campaigns", None)
    if store is None or interaction.guild is None:
        return None
    try:
        offer: HandoverOffer | None = await store.open_offer(
            interaction.guild.id, campaign_id, int(time.time())
        )
    except Exception:
        log.exception("Couldn't read a hand-over offer for the settings card")
        return None
    return offer


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
    offer = await _open_offer(interaction, campaign.id)
    try:
        await interaction.edit_original_response(
            content=settings_text(campaign, offer, interaction.user.id),
            view=settings_view(campaign, offer, interaction.user.id),
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
        # Both at once: Discord waits 3 seconds for the card (#437 review).
        campaign, offer = await asyncio.gather(
            _allowed(interaction, self.campaign_id), _open_offer(interaction, self.campaign_id)
        )
        if campaign is None:
            return
        await interaction.response.send_message(
            settings_text(campaign, offer, interaction.user.id),
            view=settings_view(campaign, offer, interaction.user.id),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )


class RuleLookupButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:rulelookup:{_ID}",
):
    """📖 Look up a rule, on ⚙️ Settings: a form with one box; the answer is private. Only
    the campaign's DMs (a server manager who isn't one doesn't get it)."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=RULE_LOOKUP_LABEL,
                emoji="📖",
                style=discord.ButtonStyle.secondary,
                row=3,
                custom_id=f"dmbot:rulelookup:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RuleLookupButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.ui.rule_lookup import ONLY_DMS, LookupForm, may_look_up

        set_log_context(guild_id=interaction.guild_id)
        store = getattr(interaction.client, "campaigns", None)
        guild = interaction.guild
        campaign: Campaign | None = None
        if store is not None and guild is not None:
            try:
                campaign = await store.get(guild.id, self.campaign_id)
            except Exception:
                log.exception("Couldn't load a campaign for a rules lookup")
                await interaction.response.send_message(LOAD_FAILED, ephemeral=True)
                return
        if campaign is None:
            await interaction.response.send_message(GONE, ephemeral=True)
            return
        if not may_look_up(campaign, interaction.user.id):
            await interaction.response.send_message(ONLY_DMS, ephemeral=True)
            return
        await interaction.response.send_modal(LookupForm(campaign))


class HouseFileButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:housefile:{_ID}",
):
    """📄 House-rules file, on ⚙️ Settings (#969): link a file, check it, or upload one. Only
    the campaign's DMs."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="House-rules file",
                emoji="📄",
                style=discord.ButtonStyle.secondary,
                row=3,
                custom_id=f"dmbot:housefile:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> HouseFileButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.ui.house_file import GONE, ONLY_DMS, open_menu

        set_log_context(guild_id=interaction.guild_id)
        store = getattr(interaction.client, "campaigns", None)
        guild = interaction.guild
        campaign: Campaign | None = None
        if store is not None and guild is not None:
            try:
                campaign = await store.get(guild.id, self.campaign_id)
            except Exception:
                log.exception("Couldn't load a campaign for the house-rules file")
                await interaction.response.send_message(LOAD_FAILED, ephemeral=True)
                return
        if campaign is None:
            await interaction.response.send_message(GONE, ephemeral=True)
            return
        if interaction.user.id not in campaign.dm_user_ids:
            await interaction.response.send_message(ONLY_DMS, ephemeral=True)
            return
        await open_menu(interaction, campaign)


class RulesCardsButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:rulescards:{_ID}:(?P<to>on|off)",
):
    """📖 Rules cards: On or Off, one tap changes it (#931). Only the campaign's DMs have it;
    a running session follows the change from now on."""

    def __init__(self, campaign_id: str, on: bool) -> None:
        super().__init__(
            discord.ui.Button(
                label=f"Turn rules cards {'off' if on else 'on'}",
                emoji="🃏",
                style=discord.ButtonStyle.primary if on else discord.ButtonStyle.secondary,
                row=3,
                custom_id=f"dmbot:rulescards:{campaign_id}:{'off' if on else 'on'}",
            )
        )
        self.campaign_id = campaign_id
        self.turn_on = not on  # what a press does

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RulesCardsButton:
        return cls(match["campaign"], on=match["to"] == "off")  # "to off" means it is on now

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.ui.rule_lookup import may_look_up

        set_log_context(guild_id=interaction.guild_id)
        store = getattr(interaction.client, "campaigns", None)
        guild = interaction.guild
        campaign: Campaign | None = None
        if store is not None and guild is not None:
            try:
                campaign = await store.get(guild.id, self.campaign_id)
            except Exception:
                log.exception("Couldn't load a campaign for rules cards")
                await interaction.response.send_message(LOAD_FAILED, ephemeral=True)
                return
        if campaign is None:
            await interaction.response.send_message(GONE, ephemeral=True)
            return
        if not may_look_up(campaign, interaction.user.id):  # the campaign's DMs, as for lookups
            await interaction.response.send_message(RULES_CARDS_ONLY_DMS, ephemeral=True)
            return
        save = getattr(interaction.client, "set_rules_cards", None)
        await interaction.response.defer()  # saving may take a moment
        try:
            if save is None:
                raise RuntimeError("no running DMbot to save it")
            saved: Campaign = await save(campaign.guild_id, campaign.id, self.turn_on)
        except Exception:
            log.exception("Couldn't change rules cards")
            await interaction.followup.send(FAILED, ephemeral=True)
            return
        await _redraw(interaction, saved)


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
