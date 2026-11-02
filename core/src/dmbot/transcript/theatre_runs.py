"""The check that only one script of a session is made at a time (#3244, slice 5b). A row in
`theatre_runs` says a run is going; it is a database row, not a flag in memory, so a restart of the
bot cannot start a second run beside it. A run that has not moved for `STALE_S` (the bot died) may
be taken over, so a crash does not lock the session for good.

Counts and times only; nothing from a session is kept here.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from dmbot.db import Database

STALE_S = 30 * 60  # a run that has not moved for this long is taken to be dead


class TheatreRuns:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def begin(self, guild_id: int, session_id: str) -> bool:
        """Start a run. False when one is going and has moved recently (the caller says "A script
        is already being made for this session."). A finished, failed or dead run is replaced."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO theatre_runs (guild_id, session_id, state, started_at, moved_at)"
                " VALUES (%s, %s, 'running', %s, %s)"
                " ON CONFLICT (guild_id, session_id) DO UPDATE SET state = 'running',"
                " started_at = EXCLUDED.started_at, moved_at = EXCLUDED.moved_at,"
                " part = 0, parts = 0"
                " WHERE theatre_runs.state <> 'running' OR theatre_runs.moved_at < %s",
                (guild_id, session_id, now, now, now - STALE_S),
            )
            return bool(cur.rowcount)

    async def moved(self, guild_id: int, session_id: str, part: int, parts: int) -> None:
        """The run got to this part (this is also what keeps it from looking dead)."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE theatre_runs SET moved_at = %s, part = %s, parts = %s"
                " WHERE guild_id = %s AND session_id = %s AND state = 'running'",
                (int(self._now()), part, parts, guild_id, session_id),
            )

    async def finish(self, guild_id: int, session_id: str, *, ok: bool) -> None:
        """End the run, so the next press can start another."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE theatre_runs SET state = %s, moved_at = %s"
                " WHERE guild_id = %s AND session_id = %s",
                ("done" if ok else "failed", int(self._now()), guild_id, session_id),
            )
