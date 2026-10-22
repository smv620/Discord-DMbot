"""The NPC list's link to each NPC's page on the website, and what the page is made from (#2265;
docs/PLAN.md, "Every NPC opens its own page").

Each message of the NPC list is a link button, the name itself ("👤 Mira · NPC"), to
`/account/npc/<server>.<campaign>/<entry>` on the configured website. It is a plain link button:
the bot handles no click, nothing breaks after a restart, and the message text is unchanged. With
no usable website address there is no button.

The page shows what the list shows the players and nothing more. The same pass that keeps the list
(`npcs_channel.sync_campaign`) writes the entries it just listed, with the same function and rules
(`RecallService.roster`), to `npc_roster`; the website has no right on the memory tables, so that
copy is what its door reads. It also keeps each person's private notes tidy: a note's NPC name is
refreshed, a note whose NPC is no longer listed is marked (30 days, then the daily sweep deletes
it), and one whose NPC is listed again is unmarked. A merge moves notes in `MemoryStore.merge`.
Notes are never read, logged or sent anywhere here: only counted.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import discord

from dmbot.db import Database
from dmbot.memory.recall import RosterEntry

log = logging.getLogger(__name__)

PERSON_MARK, GROUP_MARK = "👤", "👥"  # as the list has always marked them
PAGE = "/account/npc"
ORPHAN_DAYS = 30  # a note on a name no longer listed is kept this long


def page_url(site_url: str, guild_id: int, campaign_id: str, entry_id: str) -> str:
    """The NPC's page on the configured website, or "" when there is none that Discord would take
    (a link button needs an https address). The entry's reference is in the address, not in the
    message text."""
    parts = urlsplit(site_url.strip())
    if parts.scheme.lower() != "https" or not parts.netloc:
        return ""
    return f"https://{parts.netloc}{PAGE}/{guild_id}.{campaign_id}/{entry_id}"


LABEL_MAX = 80  # Discord's limit for a button's label
CUT = "…"


def button_label(name: str, kind: str) -> str:
    """ "<name> · <kind>" for the button, at most `LABEL_MAX` letters, cut at a word with "…"
    when the name is long. A button's label is plain text: the name is shown as it is spelt
    (markdown characters and all), not escaped."""
    tail = f" · {kind}"
    label = f"{name}{tail}"
    if len(label) <= LABEL_MAX:
        return label
    room = LABEL_MAX - len(tail) - len(CUT)
    if room < 8:  # a kind this long leaves no room for a name: cut the whole label
        return label[: LABEL_MAX - len(CUT)].rstrip() + CUT
    cut = name[:room]
    if len(name) > room and " " in cut:
        cut = cut[: cut.rindex(" ")]  # at a word, not in the middle of one
    return f"{cut.rstrip()}{CUT}{tail}"


@dataclass(frozen=True, slots=True)
class Look:
    """What a list message shows, to tell whether it needs changing. `layout`: "v2" (the name is
    the button; `text` is the other lines) or "text" (the name is in the text; `link` is the old
    📖 Open button's address, if it has one)."""

    layout: str
    text: str = ""
    label: str = ""
    link: str = ""


def wanted_look(entry: RosterEntry, other_lines: str, url: str, full_text: str) -> Look:
    """How an entry's message should look: the name as a link button (with the other lines beside
    it when there are any) when there is a page to open, else today's text."""
    if not url:
        return Look("text", full_text)
    return Look("v2", other_lines, button_label(entry.name, entry.kind), url)


def layout_for(entry: RosterEntry, other_lines: str, url: str) -> discord.ui.LayoutView:
    """The message for an entry with a page: a link button whose label is the name, in a Section
    beside the other lines, or alone in a row when there are none. The icon is the button's
    emoji. Components v2: the message has no `content`."""
    button: discord.ui.Button[Any] = discord.ui.Button(
        label=button_label(entry.name, entry.kind),
        emoji=GROUP_MARK if entry.is_group else PERSON_MARK,
        url=url,
    )
    view = discord.ui.LayoutView(timeout=None)
    if other_lines:
        view.add_item(discord.ui.Section(other_lines, accessory=button))
    else:
        view.add_item(discord.ui.ActionRow(button))
    return view


def _walk(components: Sequence[Any]) -> tuple[list[str], str, str]:
    """(text pieces, button label, button address) from a message's components, whatever their
    shape (a Section's text and accessory, a row's buttons)."""
    texts: list[str] = []
    label = url = ""
    for part in components:
        content = getattr(part, "content", None)
        if isinstance(content, str):
            texts.append(content)
        for sub in getattr(part, "children", None) or []:
            more, l2, u2 = _walk([sub])
            texts += more
            label, url = label or l2, url or u2
        accessory = getattr(part, "accessory", None)
        if accessory is not None:
            _, l2, u2 = _walk([accessory])
            label, url = label or l2, url or u2
        link = getattr(part, "url", None)
        if isinstance(link, str) and link:
            label, url = label or str(getattr(part, "label", "") or ""), url or link
    return texts, label, url


def look_of(message: discord.Message) -> Look:
    """How a list message looks now."""
    components = list(getattr(message, "components", None) or [])
    flags = getattr(message, "flags", None)
    if getattr(flags, "components_v2", False):
        texts, label, url = _walk(components)
        return Look("v2", "\n".join(texts), label, url)
    _, _, url = _walk(components)
    return Look("text", message.content, "", url)


def message_link(message: discord.Message) -> str:
    """The link a message's button points to now ("" when it has none), in either layout."""
    return look_of(message).link


def entry_view(entry: RosterEntry) -> dict[str, object]:
    """What the page may show of an entry: exactly the fields the list message is made from."""
    return {
        "id": entry.id,
        "name": entry.name,
        "kind": entry.kind,
        "isGroup": entry.is_group,
        "alsoCalled": list(entry.also_called),
        "groups": list(entry.groups),
        "groupsMore": entry.groups_more,
        "ties": list(entry.ties),
        "tiesMore": entry.ties_more,
    }


class NpcRosterStore:
    """`npc_roster` and the tidy-up of `npc_notes` (migration 0159), for the bot's role."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(
        self, guild_id: int, campaign_id: str, entries: Sequence[RosterEntry], now: int
    ) -> None:
        """Make the stored view exactly these entries (every one the list lists, not only those
        that fit in its messages), and settle the notes: their NPC's name, and who is listed."""
        from psycopg.types.json import Jsonb

        ids = [e.id for e in entries]
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM npc_roster WHERE guild_id = %s AND campaign_id = %s"
                " AND NOT (entry_id = ANY(%s))",
                (guild_id, campaign_id, ids),
            )
            for entry in entries:
                await conn.execute(
                    "INSERT INTO npc_roster (guild_id, campaign_id, entry_id, view, updated_at)"
                    " VALUES (%s, %s, %s, %s, %s)"
                    " ON CONFLICT (guild_id, campaign_id, entry_id) DO UPDATE"
                    " SET view = EXCLUDED.view, updated_at = EXCLUDED.updated_at"
                    " WHERE npc_roster.view IS DISTINCT FROM EXCLUDED.view",
                    (guild_id, campaign_id, entry.id, Jsonb(entry_view(entry)), now),
                )
            await conn.execute(
                "UPDATE npc_notes n SET name = left(r.view ->> 'name', 200)"
                " FROM npc_roster r WHERE n.guild_id = %s AND n.campaign_id = %s"
                " AND r.guild_id = n.guild_id AND r.campaign_id = n.campaign_id"
                " AND r.entry_id = n.entry_id AND n.name <> left(r.view ->> 'name', 200)",
                (guild_id, campaign_id),
            )
            await conn.execute(
                "UPDATE npc_notes n SET orphaned_at = %s WHERE n.guild_id = %s"
                " AND n.campaign_id = %s AND n.orphaned_at IS NULL"
                " AND NOT (n.entry_id = ANY(%s))",
                (now, guild_id, campaign_id, ids),
            )
            await conn.execute(
                "UPDATE npc_notes SET orphaned_at = NULL WHERE guild_id = %s AND campaign_id = %s"
                " AND orphaned_at IS NOT NULL AND entry_id = ANY(%s)",
                (guild_id, campaign_id, ids),
            )

    async def sweep(self, guild_ids: Collection[int], now: int) -> int:
        """Delete the notes whose NPC has not been listed for 30 days. The number deleted (only
        the count is ever logged)."""
        gone = 0
        for guild_id in guild_ids:
            async with self._db.guild(guild_id) as conn:
                cur = await conn.execute(
                    "DELETE FROM npc_notes WHERE guild_id = %s AND orphaned_at IS NOT NULL"
                    " AND orphaned_at <= %s",
                    (guild_id, now - ORPHAN_DAYS * 86400),
                )
                gone += cur.rowcount
        if gone:
            log.info("NPC notes: %d note(s) on names no longer listed were deleted", gone)
        return gone
