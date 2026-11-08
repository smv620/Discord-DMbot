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
        "campaigns",  # the campaign's home: `/account/campaigns/<ref>`
        "restore",  # Load a backup: one page for every campaign, so no ref (NO_REF)
    }
)


def _host(site_url: str) -> str:
    """The site's host (and port) from its https address, or "" for anything else. Built from the
    parsed host, never the raw text, so a user-info part (`good.com@evil.com`), a space or a
    backslash can't ride along."""
    try:
        parts = urlsplit(site_url.strip())
        host, port = parts.hostname, parts.port
    except ValueError:
        return ""
    if parts.scheme.lower() != "https" or not host or parts.username or parts.password:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host):
        return ""
    return f"{host}:{port}" if port else host


# Pages that are one page for every campaign: they have no `<ref>` in their address, because the
# site serves them only at `/account/<page>` (web/public/_redirects has a rewrite for the others).
NO_REF = frozenset({"questions", "restore"})


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
    host = _host(site_url)
    if not host or page not in PAGES or not _TAIL.fullmatch(campaign_id):
        return ""
    if tail and not _TAIL.fullmatch(tail):
        return ""
    if item and not _ITEM.fullmatch(item):
        return ""
    ref = f"{guild_id}.{campaign_id}" + (f".{tail}" if tail else "")
    query = f"?{ITEM_PARAM}={quote(item, safe='')}" if item else ""
    if page in NO_REF:
        return f"https://{host}/account/{page}{query}"
    return f"https://{host}/account/{page}/{ref}{query}"


def handover_link(site_url: str) -> str:
    """The hand-over page (#437): one page for a person offered a campaign, at `/handover`, outside
    the account area. No ids: the offer's token is in the address's hash only when the person
    follows an email. "" without an https site."""
    host = _host(site_url)
    return f"https://{host}/handover" if host else ""


def restore_link(site_url: str) -> str:
    """The Load a backup page (one page for every campaign). "" without an https site."""
    return site_link(site_url, "restore", 0, "x")


def account_link(site_url: str) -> str:
    """The account page, where a campaign's page falls back to when it has no page of its own
    (a page behind a switch that is off, a restored campaign whose id is not known yet)."""
    host = _host(site_url)
    return f"https://{host}/account" if host else ""


# The website's address, set once at start (the bot's `settings.site_url`). Senders that are far
# from the bot object (a module-level `tell_new`, an offers loop) read it here rather than carry
# it through every call; unset (the default, and in tests) the website button is simply left out.
_site = ""


def configure_site(site_url: str) -> None:
    global _site
    _site = site_url


def view_for(
    view: discord.ui.View | None, guild_id: int, campaign_id: str, links: DMLinks
) -> discord.ui.View | None:
    """`for_campaign` with the configured website address."""
    return for_campaign(view, _site, guild_id, campaign_id, links)


def view_with_site(view: discord.ui.View | None, site_url_for_page: str) -> discord.ui.View | None:
    """`view` with one "Open on the website" button for an address built by `handover_link` or
    `account_link` with the configured site, or the view unchanged when it is empty."""
    return add_links(view, site_url=site_url_for_page)


def configured_site() -> str:
    return _site


def questions_link(site_url: str) -> str:
    """The Questions page: one page for every campaign, so no ids. "" without an https site."""
    host = _host(site_url)
    return f"https://{host}/account/questions" if host else ""


def clarify_link(site_url: str, guild_id: int, campaign_id: str, name_id: str) -> str:
    """The clarify page for one name (#3604). It has its own address shape, not `?item=`; "" when
    there is no https address or an id is not a plain id."""
    host = _host(site_url)
    if not host or not _TAIL.fullmatch(campaign_id) or not _ITEM.fullmatch(name_id):
        return ""
    ref = quote(f"{guild_id}.{campaign_id}", safe=".")
    return f"https://{host}/account/clarify?campaign={ref}&name={quote(name_id, safe='')}"


def jump_link(message: discord.Message | discord.PartialMessage | None) -> str:
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
    have = {getattr(c, "url", None) for c in shown.children}
    links = [b for b in links if b.url not in have]  # (adding twice must not double them)
    if not links:
        return shown
    # Room first, so it is both links or neither. Buttons laid out by discord.py have no row of
    # their own: the links then follow them in order. Otherwise they take the row after the last.
    explicit = [c.row for c in shown.children if c.row is not None]
    placed = len(explicit) == len(shown.children)
    row = min(max(explicit, default=-1) + 1, 4)
    on_row = sum(1 for c in shown.children if c.row == row)
    if len(shown.children) + len(links) > 25 or (placed and on_row + len(links) > 5):
        log.info("A private message had no room for its link buttons")
        return shown
    try:
        for link in links:
            if placed:
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


async def point_at_itself(message: discord.Message | None, view: discord.ui.View | None) -> None:
    """After a private question is sent, edit it once so "Open in Discord" opens that very
    message (#3663): its address is only known after it is sent. The view may be shared by several
    DMs' copies, so the button is taken off again, ready for the next copy. Never fatal: the
    question is already there without it."""
    url = jump_link(message)
    if message is None or view is None or not url:
        return
    own = buttons(discord_url=url)
    if not own:
        return
    link = own[0]
    try:
        # Keep the order the same on every message: Discord first, then the website.
        kept = [c for c in view.children if getattr(c, "url", None)]
        for c in kept:
            view.remove_item(c)
        row = next((c.row for c in kept if c.row is not None), None)
        link.row = row
        view.add_item(link)
        for c in kept:
            view.add_item(c)
        await message.edit(view=view)
    except Exception as exc:  # (a closed message, a full view, a rate limit: all fine to skip)
        log.info("Couldn't add Open in Discord to a private message (%s)", type(exc).__name__)
    finally:
        try:
            view.remove_item(link)
        except Exception:
            log.debug("The own-message link was already gone")
