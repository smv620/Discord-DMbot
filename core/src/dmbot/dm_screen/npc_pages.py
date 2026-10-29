"""What each NPC's page on the website is made from, and the tidy-up of people's private notes
(#2265; docs/PLAN.md, "Every NPC opens its own page").

The page shows what the players may know and nothing more. A pass (`refresh`) writes the entries
`RecallService.roster` lists for the players, the one function and its rules, to `npc_roster`; the
website has no right on the memory tables, so that copy is what its door reads. The pass runs when
a campaign's memory changes (at most once a campaign a minute) and once for every campaign when
the bot starts. It does not need a Discord channel: since #2874 there is none, and the website's
story graph is the way into a page. The same pass keeps each person's private notes tidy: a note's
NPC name is refreshed, a note whose NPC is no longer listed is marked (30 days, then the daily
sweep deletes it), and one whose NPC is listed again is unmarked. A merge moves notes in
`MemoryStore.merge`. Notes are never read, logged or sent anywhere here: only counted.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence

from dmbot.db import Database
from dmbot.memory.recall import RecallService, RosterEntry

log = logging.getLogger(__name__)

ORPHAN_DAYS = 30  # a note on a name no longer listed is kept this long
SETTLE_S = 3.0  # a burst of changes (a merge, a confirm-all) becomes one pass this much later
PASS_GAP_S = 60.0  # at most one pass for a campaign a minute: a busy session mustn't flood it


def pass_delay(last_pass: float | None, now: float) -> float:
    """Seconds to wait before the next pass for a campaign: a short settle, and never sooner
    than `PASS_GAP_S` after the last one (`last_pass`, on the same clock as `now`)."""
    if last_pass is None:
        return SETTLE_S
    return max(SETTLE_S, last_pass + PASS_GAP_S - now)


def entry_view(entry: RosterEntry) -> dict[str, object]:
    """What the page may show of an entry: exactly the fields the players' view lists."""
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


async def refresh(
    recall: RecallService, roster: NpcRosterStore, guild_id: int, campaign_id: str, now: int
) -> int:
    """Write the campaign's players' view of its NPCs for the pages, and settle the notes. The
    number of entries listed. Raises on a database failure: the caller logs the type and tries
    again."""
    entries = await recall.roster(guild_id, campaign_id)
    await roster.save(guild_id, campaign_id, entries, now)
    return len(entries)
