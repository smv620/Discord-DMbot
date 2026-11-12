"""A D&D Beyond sheet link waiting for a character to have a player (#4205).

The wizard lets the DM give a sheet link for a character whose player is not known yet. A sheet is
kept for whoever plays the character (`SheetStore`), so until then the link waits here: the
address, already reduced to the one shape DMbot fetches, and the time it was put. The bot reads a
waiting link once its character has a player, then removes the row whatever the answer. Nothing is
fetched for a row that still waits. Addresses and ids only; no sheet, no name, no person.
"""

from __future__ import annotations

from dataclasses import dataclass

from dmbot.db import Database

KEEP_S = 90 * 24 * 3600  # a link nobody's character ever got a player for is let go


@dataclass(frozen=True, slots=True)
class Wait:
    campaign_id: str
    entity_id: str
    url: str
    player: int  # who plays the character now


class SheetWaits:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def hold(
        self, guild_id: int, campaign_id: str, entity_id: str, url: str, now: int
    ) -> None:
        """Keep the link for this character (a second one replaces the first)."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO sheet_waits (guild_id, campaign_id, entity_id, url, created_at)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, entity_id)"
                " DO UPDATE SET url = EXCLUDED.url, created_at = EXCLUDED.created_at",
                (guild_id, campaign_id, entity_id, url, now),
            )

    async def ready(self, guild_id: int, limit: int = 1) -> list[Wait]:
        """The oldest waiting links whose character has a player now (a real Discord person: a
        player without Discord has a negative number and no sheet to ask about)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT w.campaign_id, w.entity_id, w.url, e.played_by FROM sheet_waits w"
                " JOIN memory_entities e ON e.guild_id = w.guild_id"
                " AND e.campaign_id = w.campaign_id AND e.id = w.entity_id"
                " WHERE w.guild_id = %s AND e.played_by > 0"
                " AND e.status NOT IN ('merged', 'rejected')"
                " ORDER BY w.created_at, w.entity_id LIMIT %s",
                (guild_id, limit),
            )
            return [
                Wait(r["campaign_id"], r["entity_id"], r["url"], int(r["played_by"]))
                for r in await cur.fetchall()
            ]

    async def drop(self, guild_id: int, campaign_id: str, entity_id: str) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM sheet_waits WHERE guild_id = %s AND campaign_id = %s"
                " AND entity_id = %s",
                (guild_id, campaign_id, entity_id),
            )

    async def sweep(self, guild_id: int, now: int) -> int:
        """Let go of links older than `KEEP_S`; how many."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM sheet_waits WHERE guild_id = %s AND created_at < %s",
                (guild_id, now - KEEP_S),
            )
            return cur.rowcount

    async def waiting(self, guild_id: int) -> int:
        """How many links wait in this server (for a cheap "is there anything" look)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM sheet_waits WHERE guild_id = %s", (guild_id,)
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0
