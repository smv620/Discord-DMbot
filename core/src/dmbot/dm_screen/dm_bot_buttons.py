"""The DM Bot channel's panel and what its buttons do (#1666; docs/PLAN.md, "The DM Bot
channel").

One shared message holds two buttons everyone may press: 👤 DMbot Main Menu and ❓ Help. 👤 answers
privately with the buttons that person's role gets (`dm_bot_panel.buttons_for`), re-checked from
the campaign as it is now on every press, and those buttons are the menus DMbot already has
(the 🗂️ Menu, 📜 My character sheet, ⚙️ Settings), each of which checks the person again. Every
answer is private; nothing from the DM screen is ever posted here.

The panel message is kept by DMbot: edited in place when it exists, posted again when it was
deleted, so a deleted panel never leaves the channel empty.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import Any

import discord

from dmbot import consent_dm
from dmbot.campaigns import Campaign
from dmbot.dm_screen import dm_bot_panel as panel
from dmbot.dm_screen.dm_bot_panel import Button, Role, Standing
from dmbot.dm_screen.names_button import NamesButton
from dmbot.dm_screen.settings import SettingsButton
from dmbot.mentions import NO_PINGS

log = logging.getLogger(__name__)

_ID = r"(?P<campaign>[0-9a-f]{32})"
GONE = "DMbot can't find that campaign. Ask your DM."
WENT_WRONG = "Something went wrong. Please press it again."
# The same closing sentence on every card, in the buttons' own words (the 📜 icon is also on
# "My character sheet", so the text names buttons, not icons).
_TAIL = " Press Look up a rule to check a rule, or Transcripts to read what was said."
CARD_TEXT = {
    Role.GAME_MASTER: (
        "You are a DM here. 🗂️ Menu is about you (stop recording, delete your transcripts). "
        "⚙️ Settings is for the whole game." + _TAIL
    ),
    Role.PLAYER: (
        "You play here. Press 🗂️ Menu to stop being recorded or delete your transcripts." + _TAIL
    ),
    Role.MEMBER: ("You agreed to be recorded in this server. Press 🗂️ Menu to stop." + _TAIL),
    Role.GUEST: (
        "DMbot is not recording you. To play, join the voice channel and DMbot will message "
        "you to ask. Help explains DMbot." + _TAIL
    ),
}


async def standing_of(client: Any, campaign: Campaign, user_id: int) -> Standing:
    """What the role depends on, read now (both reads at once, for Discord's 3 seconds). A failed
    read counts as nothing, so a hiccup hides buttons and never adds one. (`recorded` itself
    answers "yes" when unsure, so the 🗂️ Menu, whose Stop button must never be hidden, can show.)"""

    async def tied() -> bool:
        memory = getattr(client, "memory", None)
        if memory is None:
            return False
        try:
            return bool(await memory.is_player_here(campaign.guild_id, campaign.id, user_id))
        except Exception:
            log.warning("Couldn't tell whether someone plays in a campaign", exc_info=True)
            return False

    async def agreed() -> bool:
        try:
            return bool(await client.recorded(campaign.guild_id, user_id))
        except Exception:
            log.warning("Couldn't read someone's consent for the DM Bot panel", exc_info=True)
            return False

    in_campaign, consented = await asyncio.gather(tied(), agreed())
    return Standing(
        is_game_master=user_id in campaign.dm_user_ids,
        in_campaign=in_campaign,
        consented=consented,
    )


def role_view(
    role: Role, campaign: Campaign, *, sheets: bool, site_url: str = ""
) -> discord.ui.View:
    """The buttons that role gets, built from `buttons_for` and nothing else."""
    view = discord.ui.View(timeout=None)
    for button in panel.buttons_for(role):
        if button is Button.HELP:
            view.add_item(HelpButton(campaign.id))
        elif button is Button.RULE:
            view.add_item(RuleButton(campaign.id))
        elif button is Button.TRANSCRIPTS:  # a link: opens the website, nothing to check here
            view.add_item(
                discord.ui.Button(
                    label=panel.LABELS[Button.TRANSCRIPTS],
                    style=discord.ButtonStyle.link,
                    url=panel.transcripts_url(site_url),
                )
            )
        elif button is Button.MY_MENU:
            view.add_item(consent_dm.MenuButton(campaign.guild_id, campaign.id))
        elif button is Button.MY_CHARACTER and sheets:
            view.add_item(consent_dm.sheet_button(campaign.guild_id, campaign.id))
        elif button is Button.SETTINGS:
            view.add_item(SettingsButton(campaign.id))
        elif button is Button.NAMES:
            view.add_item(NamesButton(campaign.id))
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
    try:
        campaign: Campaign | None = await store.get(guild.id, campaign_id)
    except Exception:
        log.exception("Couldn't load a campaign for the DM Bot panel")
        return None
    return campaign


class OpenButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:panel:open:{_ID}",
):
    """👤 DMbot Main Menu: the person's own buttons, privately."""

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
        # Answer first: reading the campaign and the person's standing takes database time, and
        # Discord drops a button that isn't answered in 3 seconds (#1964).
        await interaction.response.defer(ephemeral=True)
        campaign = await _campaign_here(interaction, self.campaign_id)
        if campaign is None:
            await interaction.followup.send(GONE, ephemeral=True)
            return
        who = await standing_of(interaction.client, campaign, interaction.user.id)
        role = panel.role_of(who)
        sheets = getattr(interaction.client, "sheets", None) is not None
        settings = getattr(interaction.client, "settings", None)  # (a client without one: no link)
        site = str(getattr(settings, "site_url", "") or "")
        await interaction.followup.send(
            CARD_TEXT[role],
            view=role_view(role, campaign, sheets=sheets, site_url=site),
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
        from dmbot.ui import logic  # (ui imports the dm_screen package: import it late)

        await interaction.response.defer(ephemeral=True)  # (the campaign read can be slow: #1964)
        campaign = await _campaign_here(interaction, self.campaign_id)
        if campaign is None:
            await interaction.followup.send(GONE, ephemeral=True)
            return
        await interaction.followup.send(logic.HELP_TEXT, ephemeral=True, allowed_mentions=NO_PINGS)


class RuleButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:panel:rule:{_ID}",
):
    """📖 Look up a rule: the same form and private answer as `/dmbot rule`, for anyone in the
    server (#1691). Not `RuleLookupButton`, whose check is the DMs only: the answer itself
    (`rule_lookup.look_up`) decides what a DM and what everyone else sees, and ends a player's
    card with "Your DM decides what applies at your table."."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=panel.LABELS[Button.RULE],
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:panel:rule:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RuleButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.ui.rule_lookup import LookupForm  # (ui imports the dm_screen package)

        campaign = await _campaign_here(interaction, self.campaign_id)
        if campaign is None:
            await interaction.response.send_message(GONE, ephemeral=True)
            return
        await interaction.response.send_modal(LookupForm(campaign))


def panel_view(campaign_id: str) -> discord.ui.View:
    """The shared message's buttons: only what every role may press (`shared_panel`)."""
    view = discord.ui.View(timeout=None)
    for button in panel.shared_panel():
        view.add_item(OpenButton(campaign_id) if button is Button.OPEN else HelpButton(campaign_id))
    return view


_PANEL_LOCKS: dict[str, asyncio.Lock] = {}


async def _pin_panel(message: discord.Message) -> None:
    """Keep the panel at the top of the channel (#1963). It needs Pin Messages, which DMbot asks
    for but can work without (the install check names it), so a refusal or a full pin list is
    only logged; the next start tries again. Already pinned: nothing to do."""
    if getattr(message, "pinned", False) is True:
        return
    try:
        await message.pin(reason="DMbot: the DM Bot channel's buttons")
    except discord.Forbidden:
        log.info("DMbot isn't allowed to pin the DM Bot panel (Pin Messages is off)")
    except discord.HTTPException:
        log.warning("Couldn't pin the DM Bot panel", exc_info=True)


async def ensure_panel(client: Any, campaign: Campaign) -> discord.Message | None:
    """Edit the panel in place, or post it again if it was deleted. Returns the message, or
    None if nothing could be shown (a failure here never stops anything). One at a time for a
    campaign, and the stored message id is read again inside the lock, so two starts never post
    two panels."""
    lock = _PANEL_LOCKS.setdefault(campaign.id, asyncio.Lock())
    async with lock:
        fresh = await client.campaigns.get(campaign.guild_id, campaign.id)
        if fresh is None or fresh.dm_bot_channel_id is None:
            return None
        channel = client.get_channel(fresh.dm_bot_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            log.warning("The DM Bot channel %s isn't cached; no panel yet", fresh.dm_bot_channel_id)
            return None
        view = panel_view(fresh.id)
        if fresh.dm_bot_message_id:
            try:
                message = await channel.fetch_message(fresh.dm_bot_message_id)
                await message.edit(content=panel.PANEL_TEXT, view=view, allowed_mentions=NO_PINGS)
                await _pin_panel(message)  # (a panel posted before pinning, or unpinned by hand)
                return message
            except discord.NotFound:
                pass  # deleted: post a new one below
            except discord.HTTPException:
                # A hiccup or a lost permission: don't pile a second panel beside it.
                log.warning(
                    "Couldn't edit the DM Bot panel (channel %s)", fresh.dm_bot_channel_id,
                    exc_info=True,
                )  # fmt: skip
                return None
        message = await client.post_message(fresh.dm_bot_channel_id, panel.PANEL_TEXT, view)
        if message is None:
            return None
        try:
            await client.campaigns.set_dm_bot_message(fresh.guild_id, fresh.id, message.id)
        except Exception:
            # Without the id the next start would post a second one: take this one down.
            log.exception("Couldn't save the DM Bot panel's message id")
            with contextlib.suppress(discord.HTTPException):
                await message.delete()
            return None
        await _pin_panel(message)
        return message  # type: ignore[no-any-return]  # post_message is typed loosely in tests
