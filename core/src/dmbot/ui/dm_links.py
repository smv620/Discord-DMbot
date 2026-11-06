"""The two link buttons every private message to a DM ends with (#3663): "Open in Discord" and
"Open on the website". One helper builds them for every sender, so the words, the icons and the
address shape are the same everywhere.

The website address is private and the same for anyone who holds it: it names the campaign by its
ids and, at most, one item by its id (`?item=<id>`), never a name or any words. The page opens only
after the DM signs in and every door behind it is DM-only, so a player who is sent the link sees
nothing of the DM's. Link-style buttons carry a URL and no custom id, so they need no registration
and survive a restart."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import discord

log = logging.getLogger(__name__)

OPEN_IN_DISCORD = "Open in Discord"
OPEN_ON_SITE = "Open on the website"
DISCORD_ICON = "💬"
SITE_ICON = "🌐"

ITEM_PARAM = "item"  # the one query parameter every page reads (WebDev, #3663)
_ITEM = re.compile(
    r"^[A-Za-z0-9._:-]{1,80}$"
)  # an id, never a word a person wrote (WebDev's contract)
_PAGE = re.compile(r"^[a-z][a-z-]{0,30}$")
_TAIL = re.compile(r"^[A-Za-z0-9._-]{1,64}$")  # e.g. a recap's session number

# The pages a message may point at; a new one is added here so the test below lists it.
PAGES = frozenset(
    {
        "questions",
        "names",
        "recaps",
        "web",  # the Story Graph: `?item=<claim or question id>` or `<idA>.<idB>` for one line
        "house-rules",
        "lore",
        "import",
        "timeline",
        "transcripts",
    }
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


def clarify_link(site_url: str, guild_id: int, campaign_id: str, name_id: str) -> str:
    """The clarify page for one name (#3604). It has its own address shape, not `?item=`; "" when
    there is no https address or an id is not a plain id."""
    parts = urlsplit(site_url.strip())
    if parts.scheme.lower() != "https" or not parts.netloc:
        return ""
    if not _TAIL.match(campaign_id) or not _ITEM.match(name_id):
        return ""
    ref = quote(f"{guild_id}.{campaign_id}", safe=".")
    return f"https://{parts.netloc}/account/clarify?campaign={ref}&name={quote(name_id, safe='')}"


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
    try:
        for link in links:
            link.row = row
            shown.add_item(link)
    except ValueError:
        # A full view (25 items, or the row is full): the message goes without the links rather
        # than not at all. Counts only.
        log.info("A private message had no room for its link buttons")
    return shown


@dataclass(frozen=True)
class DMLinks:
    """What a private message to the DM is about: the website page that answers it (the Questions
    page unless the sender knows better), the item on it, and the Discord message that holds the
    question when that is not the private message itself (the DM-screen post)."""

    page: str = "questions"
    item: str = ""
    tail: str = ""
    discord_url: str = ""
    site_url_override: str = ""  # a page with its own address shape (the clarify page)


def for_campaign(
    view: discord.ui.View | None, site_url: str, guild_id: int, campaign_id: str, links: DMLinks
) -> discord.ui.View | None:
    """`view` with the two link buttons for this campaign (the website one only when the site has
    an https address)."""
    site = links.site_url_override or site_link(
        site_url, links.page, guild_id, campaign_id, tail=links.tail, item=links.item
    )
    return add_links(view, discord_url=links.discord_url, site_url=site)
