"""📥 Import sessions on ⚙️ Settings and `/dmbot import` (#2154): the way to the website's import
page, and nothing more.

Importing older sessions happens only on the website (owner decision, 2026-10-20); Discord takes no
file and no link. Both ways give the same short private reply with one link button. Only the
campaign's DMs get it; the button is on the card, and the command answers, only where importing is
switched on for the server (`DMBOT_IMPORTS` or `DMBOT_IMPORTS_SERVERS`, #2259); and with no
website address set there is no link to give, so it says so rather than send a broken one.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlsplit

import discord

from dmbot.campaigns import Campaign
from dmbot.logs import set_log_context
from dmbot.mentions import NO_PINGS

log = logging.getLogger(__name__)

_ID = r"(?P<campaign>[0-9a-f]{32})"
LABEL = "Import sessions"
LINE = "Bring in sessions DMbot didn't record, on the website."
LINK_LABEL = "Open Import sessions"
ONLY_DM = "That's for the DM."
NOT_ON = "Importing isn't switched on yet."
NO_SITE = "The website isn't set up on this server."
NOT_IN_SERVER = "Use this in a server where DMbot is."
GONE = "That campaign isn't here any more."
FAILED = "Couldn't load the campaign. Try again in a minute."
PAGE = "/account/import"
MANY = LINE + " Pick the campaign:"
LABEL_MAX = 60  # a button label holds 80 characters; the 📥 and a margin


# Whether importing is on for a server; the bot sets it at start (`DMBot.__init__`) so the card
# can hide the button without every caller passing the settings in.
def _off(guild_id: int) -> bool:
    return False


_enabled_for: Callable[[int], bool] = _off


def use_switch(enabled_for: Callable[[int], bool]) -> None:
    global _enabled_for
    _enabled_for = enabled_for


def enabled(guild_id: int) -> bool:
    return _enabled_for(guild_id)


def page_url(site_url: str, guild_id: int, campaign_id: str) -> str:
    """This campaign's import page on the configured website, or "" when there is none that
    Discord would take (a link button needs an https address). The page reads the campaign from
    its address, so a bare page would open blank."""
    parts = urlsplit(site_url.strip())
    if parts.scheme.lower() != "https" or not parts.netloc:
        return ""
    return f"https://{parts.netloc}{PAGE}/{guild_id}.{campaign_id}"


MAX_LINKS = 5  # a message holds five buttons in a row


async def reply(interaction: discord.Interaction, mine: Sequence[Campaign]) -> None:
    """The one private answer of the button and the command, in the order that tells a player
    nothing: not a DM (`mine` empty), then not switched on, then no website. One link button for
    each campaign the person is a DM of (the command may find several)."""
    if not mine:
        await interaction.response.send_message(ONLY_DM, ephemeral=True)
        return
    guild = interaction.guild
    if guild is None or not enabled(guild.id):
        await interaction.response.send_message(NOT_ON, ephemeral=True)
        return
    settings = getattr(interaction.client, "settings", None)
    site = str(getattr(settings, "site_url", "") or "")
    view = discord.ui.View(timeout=None)
    for campaign in mine[:MAX_LINKS]:
        url = page_url(site, guild.id, campaign.id)
        if not url:
            await interaction.response.send_message(NO_SITE, ephemeral=True)
            return
        label = LINK_LABEL if len(mine) == 1 else campaign.name[:LABEL_MAX]
        view.add_item(discord.ui.Button(label=label, emoji="📥", url=url))
    text = LINE if len(mine) == 1 else MANY
    await interaction.response.send_message(
        text, view=view, ephemeral=True, allowed_mentions=NO_PINGS
    )


class ImportButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:import:{_ID}",
):
    """📥 Import sessions, on ⚙️ Settings. DMs only (the card is theirs); the DM check is made
    again on every press."""

    def __init__(self, campaign_id: str, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=LABEL,
                emoji="📥",
                style=discord.ButtonStyle.secondary,
                row=row,
                custom_id=f"dmbot:import:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ImportButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        store = getattr(interaction.client, "campaigns", None)
        campaign: Campaign | None = None
        if interaction.guild is None:
            await interaction.response.send_message(NOT_IN_SERVER, ephemeral=True)
            return
        if store is not None:
            try:
                campaign = await store.get(interaction.guild.id, self.campaign_id)
            except Exception:
                log.exception("Couldn't load a campaign for the import button")
                await interaction.response.send_message(FAILED, ephemeral=True)
                return
        if campaign is None:
            await interaction.response.send_message(GONE, ephemeral=True)
            return
        await reply(interaction, [campaign] if interaction.user.id in campaign.dm_user_ids else [])
