"""The last choice of a name menu that was cut short: "🌐 All names on the website…" (#2294).

Discord lets a menu hold 25 choices and a panel only so many names, so a big campaign's names
never all fit. The last choice sends the DM to the website's Names page for the whole list. Discord
can't open a link from a menu, so choosing it answers privately with one link button. The choice
takes one slot, so a menu that carries it lists at most 24 names, and it is added only when names
were left out. The page checks who is asking; this module only builds the address.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import discord

from dmbot.mentions import NO_PINGS
from dmbot.ui import logic

VALUE = "dmbot:all-names"  # no name's id looks like this, so a menu can tell the choice apart
LABEL = "🌐 All names on the website…"
BUTTON = "Open all names"
LINE = (
    "Every name is on the website, with search and filters. Tap the button to open it. "
    "You may need to sign in."
)
NO_SITE = "DMbot has no website link set up yet. Ask whoever runs DMbot to add it."
PAGE = "/account/names"
NAMES_MAX = logic.SELECT_OPTIONS_MAX - 1


def page_url(site_url: str, guild_id: int, campaign_id: str) -> str:
    """This campaign's Names page, or "" when there is no https address Discord would take."""
    parts = urlsplit(site_url.strip())
    if parts.scheme.lower() != "https" or not parts.netloc:
        return ""
    return f"https://{parts.netloc}{PAGE}/{guild_id}.{campaign_id}"


def choice() -> discord.SelectOption:
    return discord.SelectOption(label=LABEL, value=VALUE)


def with_choice(
    options: list[discord.SelectOption], *, left_out: bool
) -> list[discord.SelectOption]:
    """The menu's names, then the choice when names were left out. With the choice there is room
    for 24 names; without it, 25."""
    if not left_out and len(options) <= logic.SELECT_OPTIONS_MAX:
        return options
    return [*options[:NAMES_MAX], choice()]


def chosen(values: list[str]) -> bool:
    return bool(values) and values[0] == VALUE


async def reply(interaction: discord.Interaction, campaign_id: str) -> None:
    """The private answer to the choice: one link button, or a plain note when the bot has no
    website address."""
    settings = getattr(interaction.client, "settings", None)
    site = str(getattr(settings, "site_url", "") or "")
    url = page_url(site, interaction.guild_id or 0, campaign_id) if interaction.guild_id else ""
    if not url:
        await _say(interaction, NO_SITE)
        return
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label=BUTTON, emoji="🔗", url=url))
    await _say(interaction, LINE, view)


async def _say(
    interaction: discord.Interaction, text: str, view: discord.ui.View | None = None
) -> None:
    """Private, and without changing the menu the DM pressed it in."""
    shown = view if view is not None else discord.utils.MISSING
    if interaction.response.is_done():
        await interaction.followup.send(text, view=shown, ephemeral=True, allowed_mentions=NO_PINGS)
    else:
        await interaction.response.send_message(
            text, view=shown, ephemeral=True, allowed_mentions=NO_PINGS
        )
