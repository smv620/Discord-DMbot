"""The NPC list's link to each NPC's page on the website, and what the page is made from (#2265;
docs/PLAN.md, "Every NPC opens its own page").

Each message of the NPC list gets one small link button, 📖 Open, to
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
from urllib.parse import urlsplit

import discord

from dmbot.db import Database
from dmbot.memory.recall import RosterEntry

log = logging.getLogger(__name__)

OPEN_LABEL = "Open"
OPEN_EMOJI = "📖"
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


def open_view(url: str) -> discord.ui.View | None:
    """The one link button of a list message, or None when there is no link."""
    if not url:
        return None
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label=OPEN_LABEL, emoji=OPEN_EMOJI, url=url))
    return view


def message_link(message: discord.Message) -> str:
    """The link a message's button points to now ("" when it has none), to tell whether the
    message needs its button added, changed or taken away."""
    for row in getattr(message, "components", None) or []:
        for child in getattr(row, "children", None) or []:
            url = getattr(child, "url", None)
            if isinstance(url, str) and url:
                return url
    return ""


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
