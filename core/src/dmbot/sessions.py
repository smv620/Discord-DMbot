"""The session being played right now in each server, saved so a restart can resume it.

`/dmbot start` saves a session and `/dmbot stop` removes it. When a process starts, it
looks up the saved sessions for the servers on its shards and rejoins them (see
`DMBot.resume_sessions`). Like every store, reads and writes for a server go through
`Database.guild`, so Postgres only shows that server's row.

Every save and every removal is logged at INFO with its reason (IDs only), so a row that
goes missing can be traced from the log (#147).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from dmbot.db import Conn, Database, row_int
from dmbot.logs import log_context
from dmbot.sharding import ShardSettings

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SavedSession:
    guild_id: int
    campaign_id: str
    voice_channel_id: int
    screen_channel_id: int
    started_by: int
    started_at: int
    notice_posted: bool
    resume_count: int = 0
    last_resumed_at: int | None = None


class SessionStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(self, session: SavedSession) -> None:
        """Record (or replace) the server's active session."""
        async with self._db.guild(session.guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO active_sessions (guild_id, campaign_id, voice_channel_id,"
                " screen_channel_id, started_by, started_at, notice_posted)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (guild_id) DO UPDATE SET"
                " campaign_id = EXCLUDED.campaign_id,"
                " voice_channel_id = EXCLUDED.voice_channel_id,"
                " screen_channel_id = EXCLUDED.screen_channel_id,"
                " started_by = EXCLUDED.started_by,"
                " started_at = EXCLUDED.started_at,"
                " notice_posted = EXCLUDED.notice_posted,"
                " resume_count = 0, last_resumed_at = NULL"
                # xmax is 0 only for a row this statement inserted, not one it updated.
                # A Postgres internal, not documented behaviour: recheck on upgrades.
                " RETURNING (xmax = 0) AS inserted",
                (
                    session.guild_id,
                    session.campaign_id,
                    session.voice_channel_id,
                    session.screen_channel_id,
                    session.started_by,
                    session.started_at,
                    session.notice_posted,
                ),
            )
            await conn.execute(
                "INSERT INTO live_session_guilds (guild_id) VALUES (%s)"
                " ON CONFLICT (guild_id) DO NOTHING",
                (session.guild_id,),
            )
            row = await cur.fetchone()
        with log_context(guild_id=session.guild_id, campaign_id=session.campaign_id):
            log.info(
                "Session saved%s: voice channel %s, started by user %s",
                " in place of the one saved before" if row and not row["inserted"] else "",
                session.voice_channel_id,
                session.started_by,
            )

    async def get(self, guild_id: int) -> SavedSession | None:
        async with self._db.guild(guild_id) as conn:
            return await _select(conn, guild_id)

    async def note_resume(self, guild_id: int, now: int, streak_window_s: int) -> int:
        """Count a resume. Returns how many resumes in a row this is: the count restarts
        at 1 when the previous resume was more than `streak_window_s` ago."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE active_sessions SET"
                " resume_count = CASE WHEN last_resumed_at IS NOT NULL"
                "   AND %s - last_resumed_at <= %s THEN resume_count + 1 ELSE 1 END,"
                " last_resumed_at = %s"
                " WHERE guild_id = %s RETURNING resume_count",
                (now, streak_window_s, now, guild_id),
            )
            row = await cur.fetchone()
            return int(row["resume_count"]) if row else 0

    async def mark_notice_posted(self, guild_id: int) -> None:
        """Players have been told DMbot is listening; don't repeat it after a restart."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE active_sessions SET notice_posted = TRUE WHERE guild_id = %s",
                (guild_id,),
            )

    async def clear(self, guild_id: int, reason: str) -> bool:
        """Forget the server's saved session. `reason` goes in the log (IDs only).
        Returns whether a saved session was there to remove."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM active_sessions WHERE guild_id = %s RETURNING campaign_id",
                (guild_id,),
            )
            row = await cur.fetchone()
            cur = await conn.execute(
                "DELETE FROM live_session_guilds WHERE guild_id = %s RETURNING guild_id",
                (guild_id,),
            )
            routed = await cur.fetchone()
        with log_context(guild_id=guild_id):
            if row is not None:
                with log_context(campaign_id=row["campaign_id"]):
                    log.info("Saved session removed: %s", reason)
            elif routed is not None:
                log.info("Cleared a leftover restart note (no session was saved): %s", reason)
            else:
                log.info("No saved session to remove: %s", reason)  # INFO while #147 is open
        return row is not None

    async def guilds_to_resume(self, shards: ShardSettings) -> list[int]:
        """Servers on these shards that had a session running. IDs only."""
        async with self._db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT guild_id FROM live_session_guilds"
                " WHERE ((guild_id >> 22) %% %s) = ANY(%s) ORDER BY guild_id",
                (shards.count, list(shards.ids)),
            )
            return [int(r["guild_id"]) for r in await cur.fetchall()]


async def _select(conn: Conn, guild_id: int) -> SavedSession | None:
    cur = await conn.execute("SELECT * FROM active_sessions WHERE guild_id = %s", (guild_id,))
    row = await cur.fetchone()
    if row is None:
        return None
    return SavedSession(
        guild_id=int(row["guild_id"]),
        campaign_id=row["campaign_id"],
        voice_channel_id=int(row["voice_channel_id"]),
        screen_channel_id=int(row["screen_channel_id"]),
        started_by=int(row["started_by"]),
        started_at=int(row["started_at"]),
        notice_posted=bool(row["notice_posted"]),
        resume_count=int(row["resume_count"]),
        last_resumed_at=row_int(row, "last_resumed_at"),
    )
