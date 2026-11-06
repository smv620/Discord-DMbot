"""The two link buttons every private message to a DM ends with (#3663): "Open in Discord" and
"Open on the website". One helper builds them for every sender, so the words, the icons and the
address shape are the same everywhere.

The website address is private and the same for anyone who holds it: it names the campaign by its
ids and, at most, one item by its id (`?item=<id>`), never a name or any words. The page opens only
after the DM signs in and every door behind it is DM-only, so a player who is sent the link sees
nothing of the DM's. Link-style buttons carry a URL and no custom id, so they need no registration
and survive a restart."""

from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

import discord

OPEN_IN_DISCORD = "Open in Discord"
OPEN_ON_SITE = "Open on the website"
DISCORD_ICON = "💬"
SITE_ICON = "🌐"

ITEM_PARAM = "item"  # the one query parameter every page reads (WebDev, #3663)
_ITEM = re.compile(r"^[A-Za-z0-9._-]{1,64}$")  # an id, never a word a person wrote
_PAGE = re.compile(r"^[a-z][a-z-]{0,30}$")
_TAIL = re.compile(r"^[A-Za-z0-9._-]{1,64}$")  # e.g. a recap's session number

# The pages a message may point at; a new one is added here so the test below lists it.
PAGES = frozenset(
    {"questions", "names", "recaps", "house-rules", "lore", "import", "timeline", "transcripts"}
)


def site_link(
    site_url: str,
    page: str,
    guild_id: int,
    campaign_id: str,
    *,
    tail: str = "",
    item: str = "",
) -> str:
    """The campaign's website page, opened on one item, or "" when there is no https address
    Discord would take, the page is unknown, or an id is not a plain id."""
    parts = urlsplit(site_url.strip())
    if parts.scheme.lower() != "https" or not parts.netloc or page not in PAGES:
        return ""
    if not _PAGE.match(page) or not _TAIL.match(campaign_id):
        return ""
    if tail and not _TAIL.match(tail):
        return ""
    if item and not _ITEM.match(item):
        return ""
    ref = f"{guild_id}.{campaign_id}" + (f".{tail}" if tail else "")
    query = f"?{ITEM_PARAM}={quote(item, safe='')}" if item else ""
    return f"https://{parts.netloc}/account/{page}/{ref}{query}"


def jump_link(message: discord.Message | None) -> str:
    """The message's own address in Discord, or "" when there is no message to point at."""
    url = getattr(message, "jump_url", "") if message is not None else ""
    return url if isinstance(url, str) and url.startswith("https://discord.com/") else ""


def buttons(
    *, discord_url: str = "", site_url: str = ""
) -> list[discord.ui.Button[discord.ui.View]]:
    """The link buttons for the addresses given: both, either one alone, or none."""
    out: list[discord.ui.Button[discord.ui.View]] = []
    if discord_url:
        out.append(discord.ui.Button(label=OPEN_IN_DISCORD, emoji=DISCORD_ICON, url=discord_url))
    if site_url:
        out.append(discord.ui.Button(label=OPEN_ON_SITE, emoji=SITE_ICON, url=site_url))
    return out


def add_links(
    view: discord.ui.View | None, *, discord_url: str = "", site_url: str = ""
) -> discord.ui.View | None:
    """`view` with the link buttons added on the row after its own (a view with none gets a new
    one). Nothing to add gives the view back unchanged, so a message never gains an empty row."""
    links = buttons(discord_url=discord_url, site_url=site_url)
    if not links:
        return view
    shown = view if view is not None else discord.ui.View(timeout=None)
    row = max((item.row for item in shown.children if item.row is not None), default=-1) + 1
    row = min(row, 4)
    for link in links:
        link.row = row
        shown.add_item(link)
    return shown
