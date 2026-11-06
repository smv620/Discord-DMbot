"""The offers to set the game clock from an imported session (#2979, #3199): one row for each
imported session that was offered a change, with the game minute and what became of it. Written
where Discord's offer is made and by both ways of answering it (Discord's buttons and the
website's queued choice), so they never disagree: whoever answers first wins, and the other finds
the offer is no longer waiting. A minute and a state code only, never a word of the session.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from dmbot.db import Database

OFFERED, APPLYING = "offered", "applying"
SET, DISMISSED, MOVED_ON, SPLIT, EXPIRED = "set", "dismissed", "moved_on", "split", "expired"
ENDS = (SET, DISMISSED, MOVED_ON, SPLIT, EXPIRED)


@dataclass(frozen=True, slots=True)
class Offer:
    session_id: str
    minute: int
    state: str


class ImportClockOffers:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def offer(
        self, guild_id: int, campaign_id: str, session_id: str, minute: int, state: str = OFFERED
    ) -> None:
        """Record the offer for this session (`offered`, or `split` when the party is split and
        nothing can be offered). Older offers of the campaign still waiting are expired in the
        same statement's transaction: only the newest session's offer is ever live."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE import_clock_offers SET state = 'expired', updated_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND state IN ('offered', 'applying')"
                " AND session_id <> %s",
                (now, guild_id, campaign_id, session_id),
            )
            await conn.execute(
                "INSERT INTO import_clock_offers (guild_id, campaign_id, session_id, minute,"
                " state, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, session_id) DO UPDATE SET"
                " minute = EXCLUDED.minute, state = EXCLUDED.state,"
                " updated_at = EXCLUDED.updated_at",
                (guild_id, campaign_id, session_id, minute, state, now, now),
            )

    async def get(self, guild_id: int, campaign_id: str, session_id: str) -> Offer | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT session_id, minute, state FROM import_clock_offers"
                " WHERE guild_id = %s AND campaign_id = %s AND session_id = %s",
                (guild_id, campaign_id, session_id),
            )
            row = await cur.fetchone()
        return None if row is None else Offer(row["session_id"], int(row["minute"]), row["state"])

    async def take(self, guild_id: int, campaign_id: str, session_id: str) -> Offer | None:
        """Hold the offer for one press: `offered` becomes `applying`. None when it is not
        waiting (answered, expired or never made): the press has nothing to do."""
        return await self._take("session_id = %s", guild_id, campaign_id, session_id)

    async def take_minute(self, guild_id: int, campaign_id: str, minute: int) -> Offer | None:
        """The same for a Discord press: its button carries the minute, not the session."""
        return await self._take("minute = %s", guild_id, campaign_id, minute)

    async def _take(
        self, where: str, guild_id: int, campaign_id: str, value: object
    ) -> Offer | None:
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE import_clock_offers SET state = 'applying', updated_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND session_id = ("
                " SELECT session_id FROM import_clock_offers WHERE guild_id = %s"
                f" AND campaign_id = %s AND state = 'offered' AND created_at >= %s AND {where}"
                " ORDER BY created_at DESC LIMIT 1) AND state = 'offered'"
                " RETURNING session_id, minute, state",
                (now, guild_id, campaign_id, guild_id, campaign_id, now - 7 * 86400, value),
            )
            row = await cur.fetchone()
        return None if row is None else Offer(row["session_id"], int(row["minute"]), row["state"])

    async def settle(self, guild_id: int, campaign_id: str, session_id: str, state: str) -> None:
        """End a held offer (or one still waiting) with its state."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE import_clock_offers SET state = %s, updated_at = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND session_id = %s AND state IN ('offered', 'applying')",
                (state, int(self._now()), guild_id, campaign_id, session_id),
            )

    async def known_minute(self, guild_id: int, campaign_id: str, minute: int) -> bool:
        """Whether any offer for this campaign was made at this minute (answered or not): a press
        for an offer from before this table existed finds none and goes on as it always did."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM import_clock_offers WHERE guild_id = %s AND campaign_id = %s"
                " AND minute = %s LIMIT 1",
                (guild_id, campaign_id, minute),
            )
            return await cur.fetchone() is not None

    async def finish(self, guild_id: int, campaign_id: str, session_id: str, state: str) -> None:
        """End an offer a press held with `state`; `offered` puts it back (the press was refused
        and nothing moved)."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE import_clock_offers SET state = %s, updated_at = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND session_id = %s AND state = 'applying'",
                (state, int(self._now()), guild_id, campaign_id, session_id),
            )

    async def dismiss(self, guild_id: int, campaign_id: str, session_id: str) -> bool:
        """Not now, for one session's offer. False when it was not waiting."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE import_clock_offers SET state = 'dismissed', updated_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND session_id = %s"
                " AND state = 'offered'",
                (int(self._now()), guild_id, campaign_id, session_id),
            )
            return bool(cur.rowcount)

    async def dismiss_all(self, guild_id: int, campaign_id: str) -> int:
        """Discord's Not now: its button does not say which session, and only the newest session
        is ever offered, so it ends whatever is waiting for the campaign. How many."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE import_clock_offers SET state = 'dismissed', updated_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND state = 'offered'",
                (int(self._now()), guild_id, campaign_id),
            )
            return int(cur.rowcount)
