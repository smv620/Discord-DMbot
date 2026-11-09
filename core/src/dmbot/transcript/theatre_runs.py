"""The check that only one script of a session is made at a time (#3244, slice 5b). A row in
`theatre_runs` says a run is going; it is a database row, not a flag in memory, so a restart of the
bot cannot start a second run beside it. A run that has not moved for `STALE_S` (the bot died) may
be taken over, so a crash does not lock the session for good.

Counts and times only; nothing from a session is kept here.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Collection

from dmbot.db import Database

# A run that has not moved for this long is taken to be dead. `moved` is called between parts, so
# the longest single part (one AI call, however it is retried) must finish well inside this.
STALE_S = 30 * 60

# Sessions that ended (or were imported) longer ago than this are not given a script by themselves
# (#3901); the website's button makes those. Shared with the recap's wait, so it never waits for a
# script that will not come.
AUTO_SINCE_S = 7 * 24 * 3600
# The moment a session counts from: an imported session from when it was imported, a live one from
# when it ended (an import keeps the original session's old date).
SESSION_AT = "COALESCE(i.imported_at, s.ended_at)"


class TheatreRuns:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def begin(self, guild_id: int, session_id: str) -> str | None:
        """Start a run and return its token, which `moved` and `finish` must give back. None when
        one is going and has moved recently (the caller says "A script is already being made for
        this session."). A finished, failed or dead run is replaced. Two presses at once get one
        token between them: the conflict check runs under the row's lock."""
        now = int(self._now())
        token = uuid.uuid4().hex
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO theatre_runs (guild_id, session_id, token, state, started_at,"
                " moved_at) VALUES (%s, %s, %s, 'running', %s, %s)"
                " ON CONFLICT (guild_id, session_id) DO UPDATE SET token = EXCLUDED.token,"
                " state = 'running', started_at = EXCLUDED.started_at,"
                " moved_at = EXCLUDED.moved_at, part = 0, parts = 0"
                " WHERE theatre_runs.state <> 'running' OR theatre_runs.moved_at < %s",
                (guild_id, session_id, token, now, now, now - STALE_S),
            )
            return token if cur.rowcount else None

    async def skip(self, guild_id: int, session_id: str) -> None:
        """Record that no script will be made by itself for this session (its campaign's plan has
        no scripts): a finished-without-a-script row, so neither the next round nor the recap's wait
        looks at it again. The website's button still makes one (`begin` replaces a failed row)."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO theatre_runs"
                " (guild_id, session_id, token, state, started_at, moved_at)"
                " SELECT s.guild_id, s.id, 'skipped', 'failed', %s, %s FROM transcript_sessions s"
                " WHERE s.guild_id = %s AND s.id = %s ON CONFLICT DO NOTHING",
                (now, now, guild_id, session_id),
            )

    async def skip_session_at(self, guild_id: int, campaign_id: str, session_at: int) -> None:
        """`skip`, for the session a recap job is for (its `session_at` is the session's start)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id FROM transcript_sessions WHERE guild_id = %s AND campaign_id = %s"
                " AND started_at = %s",
                (guild_id, campaign_id, session_at),
            )
            row = await cur.fetchone()
        if row is not None:
            await self.skip(guild_id, str(row["id"]))

    async def state(self, guild_id: int, session_id: str) -> str | None:
        """The latest run's state ('running', 'done' or 'failed'), or None when there was none."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT state FROM theatre_runs WHERE guild_id = %s AND session_id = %s",
                (guild_id, session_id),
            )
            row = await cur.fetchone()
        return None if row is None else str(row["state"])

    async def sessions_without_script(
        self,
        guild_id: int,
        *,
        since: int,
        settled_before: int,
        limit: int,
        skip_campaigns: Collection[str] = (),
    ) -> list[tuple[str, str]]:
        """(campaign, session) ids of sessions that should get their script by themselves
        (#3901): ended at or after `since` (older ones are the website button's) and at or before
        `settled_before` (the transcript is final), with lines, not deleted, no run ever begun
        (a failed one is not tried again by itself; a run that stopped moving, because the bot was
        stopped in the middle, is), and, for an imported session, its name scan finished. Not those
        of `skip_campaigns` (ones whose plan has no scripts). Oldest first, at most `limit`. Ids
        only."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT s.campaign_id, s.id FROM transcript_sessions s"
                " LEFT JOIN theatre_runs r ON r.guild_id = s.guild_id AND r.session_id = s.id"
                " LEFT JOIN session_imports i ON i.guild_id = s.guild_id AND i.session_id = s.id"
                " WHERE s.guild_id = %s AND s.line_count > 0"
                " AND (r.session_id IS NULL OR (r.state = 'running' AND r.moved_at < %s))"
                " AND s.transcripts_deleted_at IS NULL AND s.ended_at IS NOT NULL"
                f" AND {SESSION_AT} >= %s AND {SESSION_AT} <= %s"
                " AND NOT (s.campaign_id = ANY(%s))"
                " AND (i.session_id IS NULL OR (i.state = 'done' AND i.scan_finished))"
                f" ORDER BY {SESSION_AT}, s.id LIMIT %s",
                (
                    guild_id,
                    int(self._now()) - STALE_S,
                    since,
                    settled_before,
                    list(skip_campaigns),
                    limit,
                ),
            )
            return [(str(r["campaign_id"]), str(r["id"])) for r in await cur.fetchall()]

    async def moved(
        self, guild_id: int, session_id: str, token: str, part: int, parts: int
    ) -> bool:
        """The run got to this part (this is also what keeps it from looking dead). False: the row
        is no longer this run's (it was taken over), and the run should stop."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE theatre_runs SET moved_at = %s, part = %s, parts = %s"
                " WHERE guild_id = %s AND session_id = %s AND token = %s AND state = 'running'",
                (int(self._now()), part, parts, guild_id, session_id, token),
            )
            return bool(cur.rowcount)

    async def finish(self, guild_id: int, session_id: str, token: str, *, ok: bool) -> bool:
        """End the run, so the next press can start another. False: it was taken over."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE theatre_runs SET state = %s, moved_at = %s"
                " WHERE guild_id = %s AND session_id = %s AND token = %s AND state = 'running'",
                ("done" if ok else "failed", int(self._now()), guild_id, session_id, token),
            )
            return bool(cur.rowcount)
