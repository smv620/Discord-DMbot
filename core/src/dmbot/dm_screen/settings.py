"""⚙️ Manage DMbot (was ⚙️ Settings, #515; four-button menu, #2986): what DMbot does in this
Discord server, privately, one tap per change. What the website does not have yet waits on the
same card. The button sits on the DM screen's help card (there between sessions too) and on the
"Listening" message. Only the campaign's DMs, or a server manager, may open it or change anything.

- **How much DMbot says** (Quiet / Normal, #504): saved for the campaign, and a running
  session follows the change from now on (`DMBot.set_screen_level`).
- **Who can see the DM screen:** a submenu (👁️), saved the same way as the help card's buttons
  (`save_visibility`).
- **Campaign management:** a submenu (🗂️) with the choices that are rarely used: hand over,
  pause, how long transcripts are kept, game masters, delete (#2281).

Each tap redraws the card, so it always shows what's saved. A submenu replaces the card in place
and has a ↩️ Back button. The buttons' IDs carry the
campaign, so they work after a restart. Register with
`bot.add_dynamic_items(SettingsButton, LevelButton, SettingsVisibilityButton, VisibilityMenuButton,
ManagementMenuButton, BackToSettingsButton)`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from typing import Any

import discord

from dmbot import features
from dmbot.campaigns import Campaign, CampaignStore
from dmbot.campaigns.invite import parse_invite
from dmbot.campaigns.models import (
    DEFAULT_DM_SCREEN_LEVEL,
    DM_SCREEN_LEVELS,
    DM_SCREEN_LEVELS_OFFERED,
    CampaignError,
    HandoverOffer,
)
from dmbot.campaigns.store import QUIET_STOP_CHOICES
from dmbot.dm_screen import (
    handover,
    messages,
    quiet_stop,
    story_mode,
    table_chat_panel,
)
from dmbot.dm_screen.backstory_button import BackstoryButton
from dmbot.dm_screen.buttons import may_change_screen, save_visibility
from dmbot.dm_screen.clock import ClockButton
from dmbot.dm_screen.delete_campaign import DeleteButton
from dmbot.dm_screen.game_masters import GameMastersButton
from dmbot.dm_screen.pause import pause_buttons, pause_line
from dmbot.dm_screen.recap_style import RecapStyleButton
from dmbot.dm_screen.table_chat_panel import TableChatButton
from dmbot.dm_screen.transcript_keep import TranscriptsButton
from dmbot.logs import set_log_context
from dmbot.mentions import NO_PINGS
from dmbot.transcript.keep import KEEP_LABELS

log = logging.getLogger(__name__)

_ID = r"(?P<campaign>[0-9a-f]{32})"
SETTINGS_LABEL = "Manage DMbot"
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
ONLY_DMS = "Only this campaign's DMs can change this."
RULES_CARDS_ONLY_DMS = ONLY_DMS  # (the old name, kept for its callers)
CAMPAIGN_MANAGEMENT_PATH = "⚙️ Manage DMbot → 🗂️ Campaign management"
ONLY_DM = "Only this campaign's DM (or a server manager) can open or change this."
GONE = messages.DM_CAMPAIGN_GONE
FAILED = "Sorry, that didn't save. Please try again."
LOAD_FAILED = messages.LOAD_FAILED
SAVED = "Saved. Open ⚙️ Manage DMbot again to see it."
INVITE_SAVED = "✅ Saved. Players get a **Join on D&D Beyond** button on new messages from DMbot."
INVITE_REMOVED = "✅ Removed. New messages from DMbot won't have the D&D Beyond button."


def _tick(label: str, current: bool) -> str:
    return f"✓ {label}" if current else label


def _combat_line(campaign: Campaign) -> str:
    """The combat line (#1127, #1769): DMbot follows rounds, never turn order."""
    return "• **Combat:** DMbot counts the rounds, never whose turn it is."


def _transcripts_line(campaign: Campaign) -> str:
    """Who can read the transcripts, and (#1533) how long they are kept."""
    days = campaign.transcript_keep_days
    kept = (
        "kept until the campaign is deleted"
        if days is None
        else f"deleted {KEEP_LABELS[days]} after each session"
    )
    return (
        "• **Saved transcripts:** anyone in the server can read and download them with "
        f"`/transcript`. {kept[:1].upper()}{kept[1:]}. Change it in {CAMPAIGN_MANAGEMENT_PATH} → "
        "🗄️ Transcript retention."
    )


PAUSED_LINE = (
    "• **⏸️ Paused:** DMbot won't start a session for this campaign until you unpause it. The "
    "owner can unpause it under 🗂️ Campaign management."
)


def _rules_cards_line(campaign: Campaign, viewer: int | None) -> str:
    if not campaign.rules_cards:
        return RULES_CARDS_OFF
    who = {"peek": RULES_CARDS_PEEK, "open": RULES_CARDS_OPEN}.get(
        campaign.dm_screen_visibility, ""
    )
    stop = RULES_CARDS_STOP if viewer is not None and viewer in campaign.dm_user_ids else ""
    return RULES_CARDS_ON + who + stop


# What waits here until the website has its page (#2986): each leaves this card, and this list,
# as the website takes it over. Shown as one line under the card's buttons.
NOT_ON_WEBSITE = (
    "🧭 Shared story, 🗂️ Campaign management, 📄 House-rules file, "
    "🔗 D&D Beyond invite, 📚 Shared rules, 📚 Homebrew, "
    "📜 Upload campaign lore, and `/dmbot optionalrules`"
)
MANAGE_INTRO = (
    "What DMbot does in this Discord server. Everything else about your campaign is on the "
    "website: 🌐 Manage campaign."
)


def settings_text(
    campaign: Campaign, offer: HandoverOffer | None = None, viewer: int | None = None
) -> str:
    """The ⚙️ Manage DMbot card (only the person who opened it sees it): only what needs Discord
    to be there (#2986), with the rest listed as not on the website yet. `offer`, `viewer`: kept
    for the card's callers; a waiting hand-over shows where the owner line lives."""
    level = campaign.dm_screen_level
    recommended = " (recommended)" if level == DEFAULT_DM_SCREEN_LEVEL else ""
    what = DM_SCREEN_LEVELS.get(level, level)
    who = messages.WHO_CAN_SEE.get(campaign.dm_screen_visibility, "")
    return "\n".join(
        [
            f"⚙️ **Manage DMbot for {discord.utils.escape_markdown(campaign.name)}**",
            MANAGE_INTRO,
            f"• **How much DMbot says:** {level.capitalize()}{recommended}. "
            f"{what[:1].upper()}{what[1:]}. Warnings always show.",
            f"• **Who can see the DM screen:** {who} Change it with 👁️ Who can see the DM screen.",
            *([PAUSED_LINE] if campaign.paused else []),
            _rules_cards_line(campaign, viewer),
            table_chat_panel.line(campaign),
            *(
                [quiet_stop.line(campaign.quiet_stop_minutes)]
                if viewer in campaign.dm_user_ids
                else []
            ),
            f"**Not on the website yet:** {NOT_ON_WEBSITE}.",
        ]
    )


def visibility_text(campaign: Campaign) -> str:
    """The 👁️ card: who can see the DM screen, with the choice that is saved."""
    who = messages.WHO_CAN_SEE.get(campaign.dm_screen_visibility, "")
    return (
        f"👁️ **Who can see the DM screen for {discord.utils.escape_markdown(campaign.name)}**\n"
        f"{who}\nTap a choice to change it."
    )


def management_text(
    campaign: Campaign, offer: HandoverOffer | None = None, viewer: int | None = None
) -> str:
    """The 🗂️ card: the owner, whether it is paused, and how long transcripts are kept, with the
    buttons for them (hand over, pause, transcript retention, game masters, delete)."""
    return "\n".join(
        [
            f"🗂️ **Campaign management for {discord.utils.escape_markdown(campaign.name)}**",
            handover.owner_line(campaign, offer),
            pause_line(campaign, viewer),
            _transcripts_line(campaign),
            "Tap what you need, or ↩️ Back.",
        ]
    )


def management_items(
    campaign: Campaign, offer: HandoverOffer | None, viewer: int
) -> list[discord.ui.Item[Any]]:
    """The 🗂️ card's buttons for this viewer, each with its own rules as before: hand over and
    pause for the owner (a waiting offer's maker can take it back, the DMs of a campaign with no
    owner can take it on), delete and the rest for the campaign's DMs (delete refuses anyone
    but the plan holder when pressed). Nothing is drawn that the viewer could not use."""
    items: list[discord.ui.Item[Any]] = [
        *handover.owner_buttons(campaign, offer, viewer),
        *pause_buttons(campaign, viewer),
    ]
    if viewer in campaign.dm_user_ids:
        items += [
            TranscriptsButton(campaign.id),  # how long transcripts are kept (#1533)
            GameMastersButton(campaign.id),  # who the DMs are (#1600)
            DeleteButton(campaign.id),  # delete the campaign, plan holder only (#1608)
        ]
    for item in items:
        _unpin(item)
    return items


def _unpin(item: discord.ui.Item[Any]) -> None:
    """Let the card lay the button out: each had a row of its own choosing on the old card."""
    inner = getattr(item, "item", item)  # (a dynamic item wraps the real button)
    inner.row = None
    item.row = None


def _row(item: discord.ui.Item[Any], row: int) -> discord.ui.Item[Any]:
    """Put a button on a row of the card (a dynamic item wraps the real button)."""
    inner = getattr(item, "item", item)
    inner.row = row
    item.row = row
    return item


def settings_view(
    campaign: Campaign, offer: HandoverOffer | None, viewer: int, site_url: str = ""
) -> discord.ui.View:
    """The ⚙️ Manage DMbot card's buttons (#2986). `viewer`: who it's for (it's private).
    Rows 0 and 1 hold only what needs Discord to be there: how much DMbot says, who can see the DM
    screen (the one sub-page), rules cards, the game clock, table chat, stop when quiet and what is
    switched on. From row 2 are the choices that wait here until the website has them; each leaves
    as its page lands. Nothing is drawn that the viewer could not use."""
    view = discord.ui.View(timeout=None)
    for level in DM_SCREEN_LEVELS_OFFERED:
        view.add_item(
            _row(LevelButton(campaign.id, level, current=level == campaign.dm_screen_level), 0)
        )
    view.add_item(_row(VisibilityMenuButton(campaign.id), 0))
    is_dm = viewer in campaign.dm_user_ids
    if is_dm:
        view.add_item(_row(RulesCardsButton(campaign.id, campaign.rules_cards), 0))
        view.add_item(_row(ClockButton(campaign.id, "open"), 1))  # the game clock (#965)
        view.add_item(_row(TableChatButton(campaign.id), 1))  # which typed chat is read (#1118)
        view.add_item(_row(QuietStopButton(campaign.id, campaign.quiet_stop_minutes), 1))
        view.add_item(_row(FeaturesButton(campaign.id), 1))  # what is switched on (#1831)
    # Not on the website yet: each of these moves out as the website's page lands (#2985).
    if management_items(campaign, offer, viewer):
        view.add_item(_row(ManagementMenuButton(campaign.id), 2))
    if is_dm:
        view.add_item(_row(story_mode.StoryButton(campaign.id), 2))  # Shared story (#1692)
        view.add_item(_row(HouseFileButton(campaign.id), 2))  # the house-rules file (#969)
        view.add_item(_row(InviteButton(campaign.id), 2))  # the D&D Beyond invite (#1119)
        view.add_item(_row(RecapStyleButton(campaign.id), 3))  # (the rest of Recaps: website)
        view.add_item(_row(SharedRulesButton(campaign.id), 3))  # shared house rules (#1190)
        view.add_item(_row(HomebrewButton(campaign.id), 3))  # the campaign's own homebrew (#1520)
        view.add_item(_row(BackstoryButton(campaign.id), 3))  # upload campaign lore
    return view


def visibility_view(campaign: Campaign) -> discord.ui.View:
    """The 👁️ card's buttons: the three choices, the saved one ticked, and Back."""
    view = discord.ui.View(timeout=None)
    for visibility in messages.VISIBILITY_BUTTONS:
        current = visibility == campaign.dm_screen_visibility
        view.add_item(SettingsVisibilityButton(campaign.id, visibility, current=current))
    view.add_item(BackToSettingsButton(campaign.id))
    return view


def management_view(
    campaign: Campaign, offer: HandoverOffer | None, viewer: int
) -> discord.ui.View:
    """The 🗂️ card's buttons for this viewer, and Back."""
    view = discord.ui.View(timeout=None)
    for item in management_items(campaign, offer, viewer):
        view.add_item(item)
    view.add_item(BackToSettingsButton(campaign.id))
    return view


def _card(campaign: Campaign, viewer: int) -> tuple[str, discord.ui.View]:
    """The card the hand-over and pause buttons live on, drawn again after an offer is taken
    back or a campaign is paused: 🗂️ Campaign management."""
    return management_text(campaign, None, viewer), management_view(campaign, None, viewer)


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


def _site(interaction: discord.Interaction) -> str:
    """The website address the bot was set up with (empty: none)."""
    return str(getattr(getattr(interaction.client, "settings", None), "site_url", "") or "")


def _drawn(
    campaign: Campaign, offer: HandoverOffer | None, viewer: int, card: str, site_url: str = ""
) -> tuple[str, discord.ui.View]:
    """A card as the campaign is now: "settings", "visibility" or "management"."""
    if card == "visibility":
        return visibility_text(campaign), visibility_view(campaign)
    if card == "management":
        return management_text(campaign, offer, viewer), management_view(campaign, offer, viewer)
    return settings_text(campaign, offer, viewer), settings_view(campaign, offer, viewer, site_url)


async def _redraw(
    interaction: discord.Interaction, campaign: Campaign, card: str = "settings"
) -> None:
    """Show the card as saved now (the press was deferred as an update). If the card
    can't be redrawn (it's too old), the change still is saved: say so."""
    offer = await _open_offer(interaction, campaign.id)
    text, view = _drawn(campaign, offer, interaction.user.id, card, _site(interaction))
    try:
        await interaction.edit_original_response(content=text, view=view, allowed_mentions=NO_PINGS)
    except discord.HTTPException:
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(SAVED, ephemeral=True)


_OWNER_CHECKS: set[asyncio.Task[Any]] = set()


class SettingsButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:settings:{_ID}",
):
    """⚙️ Manage DMbot: opens the campaign's settings privately."""

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
            view=settings_view(campaign, offer, interaction.user.id, _site(interaction)),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )
        # A campaign made before the DM Bot channel (#1666) gets one now, and is told where.
        upgrade = getattr(interaction.client, "upgrade_dm_bot_channel", None)
        if callable(upgrade):
            try:
                made = await upgrade(campaign)
            except Exception:
                log.exception("Couldn't make the DM Bot channel on a Settings press")
                made = None
            if made is not None:
                with contextlib.suppress(discord.HTTPException):  # (the owner check follows)
                    await interaction.followup.send(
                        f"The DMbot menu now also lives in <#{made}>.",
                        ephemeral=True,
                        allowed_mentions=NO_PINGS,
                    )
        # Then, without making the card wait, see whether the owner is still in the server
        # (#1214); if not, the DMs are told and the card's Take it on appears next time.
        check = getattr(interaction.client, "check_owner_present", None)
        looking = check(campaign) if callable(check) else None
        if asyncio.iscoroutine(looking):
            task = asyncio.create_task(looking, name="owner-check")
            _OWNER_CHECKS.add(task)  # a task nobody holds can be collected before it ends
            task.add_done_callback(_OWNER_CHECKS.discard)


class FeaturesButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:features:{_ID}",
):
    """What's switched on, on the Settings card: a private list of the features the person who runs
    this DMbot has turned on (#1831). Names and on or off only, no values. Campaign DMs only."""

    def __init__(self, campaign_id: str, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=features.BUTTON_LABEL,
                emoji="ℹ️",
                style=discord.ButtonStyle.secondary,
                row=row,
                custom_id=f"dmbot:features:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> FeaturesButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        store = getattr(interaction.client, "campaigns", None)
        guild = interaction.guild
        campaign: Campaign | None = None
        if store is not None and guild is not None:
            try:
                campaign = await store.get(guild.id, self.campaign_id)
            except Exception:
                log.exception("Couldn't load a campaign for the features list")
                await interaction.response.send_message(LOAD_FAILED, ephemeral=True)
                return
        if campaign is None:
            await interaction.response.send_message(GONE, ephemeral=True)
            return
        if interaction.user.id not in campaign.dm_user_ids:  # on every press
            await interaction.response.send_message(ONLY_DM, ephemeral=True)
            return
        settings = getattr(interaction.client, "settings", None)
        if settings is None:  # (a client without one: nothing to list)
            await interaction.response.send_message(LOAD_FAILED, ephemeral=True)
            return
        await interaction.response.send_message(
            features.whats_on_text(settings), ephemeral=True, allowed_mentions=NO_PINGS
        )


class RuleLookupButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:rulelookup:{_ID}",
):
    """📖 Look up a rule, on ⚙️ Manage DMbot: a form with one box; the answer is private. Only
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
    """📄 House-rules file, on ⚙️ Manage DMbot (#969): link a file, check it, or upload one. Only
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


class SharedRulesButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:sharedrules:{_ID}",
):
    """📚 Shared rules, on ⚙️ Manage DMbot (#1190): share house rules, or use another table's
    set. Only the campaign's DMs."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Shared rules",
                emoji="📚",
                style=discord.ButtonStyle.secondary,
                row=4,
                custom_id=f"dmbot:sharedrules:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> SharedRulesButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.ui.shared_rules import GONE, ONLY_DMS, open_menu

        set_log_context(guild_id=interaction.guild_id)
        store = getattr(interaction.client, "campaigns", None)
        guild = interaction.guild
        campaign: Campaign | None = None
        if store is not None and guild is not None:
            try:
                campaign = await store.get(guild.id, self.campaign_id)
            except Exception:
                log.exception("Couldn't load a campaign for the shared rules")
                await interaction.response.send_message(LOAD_FAILED, ephemeral=True)
                return
        if campaign is None:
            await interaction.response.send_message(GONE, ephemeral=True)
            return
        if interaction.user.id not in campaign.dm_user_ids:
            await interaction.response.send_message(ONLY_DMS, ephemeral=True)
            return
        await open_menu(interaction, campaign)


class HomebrewButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:homebrew:{_ID}",
):
    """📚 Homebrew, on ⚙️ Manage DMbot (#1520): the campaign's own spells, monsters, items and other
    game content. Only the campaign's DMs."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Homebrew",
                emoji="📚",
                style=discord.ButtonStyle.secondary,
                row=4,
                custom_id=f"dmbot:homebrew:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> HomebrewButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.ui.homebrew import CAMPAIGN_GONE, ONLY_DMS, open_menu

        set_log_context(guild_id=interaction.guild_id)
        store = getattr(interaction.client, "campaigns", None)
        guild = interaction.guild
        campaign: Campaign | None = None
        if store is not None and guild is not None:
            try:
                campaign = await store.get(guild.id, self.campaign_id)
            except Exception:
                log.exception("Couldn't load a campaign for the homebrew")
                await interaction.response.send_message(LOAD_FAILED, ephemeral=True)
                return
        if campaign is None:
            await interaction.response.send_message(CAMPAIGN_GONE, ephemeral=True)
            return
        if interaction.user.id not in campaign.dm_user_ids:
            await interaction.response.send_message(ONLY_DMS, ephemeral=True)
            return
        await open_menu(interaction, campaign)


class InviteButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:invite:{_ID}",
):
    """🔗 D&D Beyond invite, on ⚙️ Manage DMbot (#1119): set, change or remove the campaign's
    invite link. Only the campaign's DMs."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="D&D Beyond invite",
                emoji="🔗",
                style=discord.ButtonStyle.secondary,
                row=3,
                custom_id=f"dmbot:invite:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> InviteButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        store = getattr(interaction.client, "campaigns", None)
        guild = interaction.guild
        campaign: Campaign | None = None
        if store is not None and guild is not None:
            try:
                campaign = await store.get(guild.id, self.campaign_id)
            except Exception:
                log.exception("Couldn't load a campaign for the D&D Beyond invite")
                await interaction.response.send_message(LOAD_FAILED, ephemeral=True)
                return
        if campaign is None:
            await interaction.response.send_message(GONE, ephemeral=True)
            return
        if interaction.user.id not in campaign.dm_user_ids:
            await interaction.response.send_message(ONLY_DM, ephemeral=True)
            return
        await interaction.response.send_modal(InviteForm(campaign))


class InviteForm(discord.ui.Modal, title="D&D Beyond invite"):
    link: discord.ui.TextInput[InviteForm] = discord.ui.TextInput(
        label="Campaign invite link",
        placeholder="Paste your D&D Beyond invite link. Blank removes it.",
        required=False,
        max_length=200,
    )

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(timeout=600)
        self.campaign_id = campaign.id
        if campaign.dndbeyond_invite:
            self.link.default = campaign.dndbeyond_invite

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        log.exception("The D&D Beyond invite form failed", exc_info=error)
        with contextlib.suppress(discord.HTTPException):
            await interaction.response.send_message(FAILED, ephemeral=True)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        campaign = await _allowed(interaction, self.campaign_id)
        if campaign is None:
            return
        try:
            invite = parse_invite(self.link.value)
        except CampaignError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        try:
            saved = await interaction.client.campaigns.set_dndbeyond_invite(  # type: ignore[attr-defined]
                campaign.guild_id, campaign.id, invite
            )
        except CampaignError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except Exception:
            log.exception("Couldn't save the D&D Beyond invite")
            await interaction.response.send_message(FAILED, ephemeral=True)
            return
        follow = getattr(interaction.client, "after_invite_change", None)
        if follow is not None:
            # A running session and the transcript channel's pinned card follow at once.
            await follow(saved)
        await interaction.response.send_message(
            INVITE_SAVED if invite else INVITE_REMOVED, ephemeral=True
        )


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


class QuietStopButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:quietstop:{_ID}:(?P<to>[0-9]{{1,2}})",
):
    """Stop when quiet (#2034, #2288): one tap goes to the next choice (5, 10, 15, 30 minutes,
    then 5 again; there is no Off) and the card shows it. The button's ID carries the choice a
    press saves, so it works after a restart. A button on an old card (60, 90 or 0) is read as
    30 and saves the choice after it."""

    def __init__(self, campaign_id: str, minutes: int) -> None:
        self.campaign_id = campaign_id
        shown = minutes if minutes in QUIET_STOP_CHOICES else 30
        self.next = QUIET_STOP_CHOICES[
            (QUIET_STOP_CHOICES.index(shown) + 1) % len(QUIET_STOP_CHOICES)
        ]
        super().__init__(
            discord.ui.Button(
                label=f"Stop when quiet: {quiet_stop.quiet_label(shown)}",
                emoji="🔇",
                style=discord.ButtonStyle.secondary,
                row=0,
                custom_id=f"dmbot:quietstop:{campaign_id}:{self.next}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> QuietStopButton:
        # What is saved is the choice in the ID; the label is redrawn from the saved value.
        wanted = int(match["to"])
        button = cls(match["campaign"], wanted)
        # A button from before #2288 may carry 0, 60 or 90, which are no longer choices: save 30.
        button.next = wanted if wanted in QUIET_STOP_CHOICES else 30
        return button

    async def callback(self, interaction: discord.Interaction) -> Any:
        campaign = await _allowed(interaction, self.campaign_id)
        if campaign is None:
            return
        store = getattr(interaction.client, "campaigns", None)
        await interaction.response.defer()
        try:
            if store is None:
                raise RuntimeError("no running DMbot to save it")
            saved: Campaign = await store.set_quiet_stop(campaign.guild_id, campaign.id, self.next)
        except CampaignError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except Exception:
            log.exception("Couldn't change the quiet-stop setting")
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
                row=0,
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
            await _redraw(interaction, saved, "visibility")


async def _open_card(interaction: discord.Interaction, campaign_id: str, card: str) -> None:
    """Replace the private card in place with another, drawn from the campaign as it is now.
    The same check as every settings button, on every press."""
    allowed = await _may(interaction, campaign_id)
    if allowed is None:
        return
    campaign = allowed[0]
    offer = await _open_offer(interaction, campaign_id) if card != "visibility" else None
    text, view = _drawn(campaign, offer, interaction.user.id, card, _site(interaction))
    try:
        await interaction.response.edit_message(content=text, view=view, allowed_mentions=NO_PINGS)
    except discord.HTTPException:  # the card is too old to edit: say so, don't leave "failed"
        # The press is not answered yet when the edit itself fails, so a follow-up alone would
        # fail too and Discord would show "This interaction failed" (#2292).
        with contextlib.suppress(discord.HTTPException):
            if not interaction.response.is_done():
                await interaction.response.send_message(FAILED, ephemeral=True)
            else:
                await interaction.followup.send(FAILED, ephemeral=True)


class VisibilityMenuButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:setvismenu:{_ID}",
):
    """👁️ Who can see the DM screen: opens the three choices (#2281)."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Who can see the DM screen",
                emoji="👁️",
                style=discord.ButtonStyle.secondary,
                row=1,
                custom_id=f"dmbot:setvismenu:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> VisibilityMenuButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _open_card(interaction, self.campaign_id, "visibility")


class ManagementMenuButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:setmgmt:{_ID}",
):
    """🗂️ Campaign management: hand over, pause, transcript retention, game masters, delete
    (#2281). Drawn only for someone who has at least one of them."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Campaign management",
                emoji="🗂️",
                style=discord.ButtonStyle.secondary,
                row=1,
                custom_id=f"dmbot:setmgmt:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ManagementMenuButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _open_card(interaction, self.campaign_id, "management")


class BackToSettingsButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:setback:{_ID}",
):
    """↩️ Back to Settings: on each submenu card, draws the Settings card again in place."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Back",
                emoji="↩️",
                style=discord.ButtonStyle.secondary,
                row=4,
                custom_id=f"dmbot:setback:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> BackToSettingsButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _open_card(interaction, self.campaign_id, "settings")
