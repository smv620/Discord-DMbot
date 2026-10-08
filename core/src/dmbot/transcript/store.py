"""Stored session transcripts (#41, #125).

Every session gets a row, and every piece of speech DMbot wrote down from someone who
agreed is a line. Lines arrive in batches (`TranscriptBuffer`, saved every few seconds),
so a session never waits on the database. Each save also updates the session's line
count and speakers, so listing sessions never reads their lines. The data types and the
buffer are in `dmbot.transcript.models`, which needs no database. Like every store,
reads and writes go through `Database.guild`, so Postgres only shows that server's rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from typing import Any

from dmbot.db import Database, row_int
from dmbot.transcript.models import Line, TranscriptSession

RECENT_SESSIONS = 25  # Discord's limit for a menu


class TranscriptStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def open_session(
        self, guild_id: int, campaign_id: str, started_at: int, engine: str = ""
    ) -> str:
        """The session's ID, creating its row. A session resumed after a restart (same
        campaign and start time) gets its own row back. `engine`: which speech-to-text
        writes it down ("engine model host"), added if it's new to the session."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at, engines)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (campaign_id, guild_id, started_at)"
                " DO UPDATE SET ended_at = NULL, engines = CASE"
                "   WHEN EXCLUDED.engines = '{}' OR transcript_sessions.engines @> EXCLUDED.engines"
                "   THEN transcript_sessions.engines"
                "   ELSE transcript_sessions.engines || EXCLUDED.engines END"
                " RETURNING id",
                (uuid.uuid4().hex, guild_id, campaign_id, started_at, [engine] if engine else []),
            )
            row = await cur.fetchone()
            assert row is not None
            return str(row["id"])

    async def end_session(self, guild_id: int, session_id: str, ended_at: int) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE transcript_sessions SET ended_at = %s WHERE id = %s",
                (ended_at, session_id),
            )

    async def add_lines(self, guild_id: int, session_id: str, lines: Sequence[Line]) -> list[int]:
        """Save lines, and the session's counts, in one transaction; the new rows' IDs."""
        if not lines:
            return []
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO transcript_lines"
                " (guild_id, session_id, started_ms, user_id, heard, text, duration_ms, topic)"
                " SELECT %s, %s, l.started_ms, l.user_id, l.heard, l.text, l.duration_ms,"
                " l.topic"
                " FROM unnest(%s::bigint[], %s::bigint[], %s::text[], %s::text[], %s::int[],"
                " %s::text[]) AS l(started_ms, user_id, heard, text, duration_ms, topic)"
                " RETURNING id",
                (
                    guild_id,
                    session_id,
                    [line.started_ms for line in lines],
                    [line.user_id for line in lines],
                    [line.heard for line in lines],
                    # NULL while the cleaned text is the same as heard (no names fixed)
                    [None if line.text == line.heard else line.text for line in lines],
                    [line.duration_ms for line in lines],
                    [line.topic for line in lines],
                ),
            )
            ids = [int(r["id"]) for r in await cur.fetchall()]
            await conn.execute(
                "UPDATE transcript_sessions SET line_count = line_count + %s,"
                " speakers = ARRAY(SELECT DISTINCT u FROM unnest(speakers || %s::bigint[]) AS u"
                "   ORDER BY u)"
                " WHERE id = %s",
                (len(ids), sorted({line.user_id for line in lines}), session_id),
            )
        return ids

    async def relabel_line(
        self, guild_id: int, session_id: str, user_id: int, started_ms: int, text: str
    ) -> int:
        """A saved line's cleaned words changed (an Undo, #296). What was heard never
        changes; `text` goes back to NULL when it's the same as heard. How many lines
        changed (0: it wasn't saved)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE transcript_lines SET text = NULLIF(%s, heard)"
                " WHERE session_id = %s AND user_id = %s AND started_ms = %s",
                (text, session_id, user_id, started_ms),
            )
            return cur.rowcount

    async def set_topic(
        self, guild_id: int, session_id: str, user_id: int, started_ms: int, topic: str
    ) -> int:
        """A saved line's topic, from the off-topic filter (#52). What was heard never
        changes. How many lines changed (0: it wasn't saved)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE transcript_lines SET topic = %s"
                " WHERE session_id = %s AND user_id = %s AND started_ms = %s",
                (topic, session_id, user_id, started_ms),
            )
            return cur.rowcount

    async def remove_lines(self, guild_id: int, session_id: str, ids: Collection[int]) -> None:
        """Take saved lines back out (someone pressed Stop while they were being saved),
        and count the session again."""
        if not ids:
            return
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM transcript_lines WHERE session_id = %s AND id = ANY(%s)",
                (session_id, list(ids)),
            )
            await conn.execute(
                "UPDATE transcript_sessions SET"
                " line_count = (SELECT count(*) FROM transcript_lines WHERE session_id = %s),"
                " speakers = ARRAY(SELECT DISTINCT user_id FROM transcript_lines"
                "   WHERE session_id = %s ORDER BY user_id)"
                " WHERE id = %s",
                (session_id, session_id, session_id),
            )

    async def sessions(
        self, guild_id: int, campaign_id: str, *, limit: int = RECENT_SESSIONS
    ) -> list[TranscriptSession]:
        """The campaign's sessions that have lines, newest first, numbered from the
        first (Session 1). Reads sessions only, never their lines."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT * FROM (SELECT *,"
                " row_number() OVER (ORDER BY started_at, id) AS number"
                " FROM transcript_sessions WHERE campaign_id = %s AND line_count > 0) AS s"
                " ORDER BY started_at DESC, id DESC LIMIT %s",
                (campaign_id, limit),
            )
            return [_session(r) for r in await cur.fetchall()]

    async def session_counts(self, guild_id: int) -> dict[str, int]:
        """Saved sessions (with at least one line) per campaign id in this server, in
        one query. Campaigns with none are left out."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT campaign_id, count(*) AS n FROM transcript_sessions"
                " WHERE line_count > 0 GROUP BY campaign_id"
            )
            return {r["campaign_id"]: r["n"] for r in await cur.fetchall()}

    async def session(self, guild_id: int, session_id: str) -> TranscriptSession | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT s.*, (SELECT count(*) FROM transcript_sessions e"
                "   WHERE e.campaign_id = s.campaign_id AND e.line_count > 0"
                "   AND (e.started_at, e.id) <= (s.started_at, s.id)) AS number"
                " FROM transcript_sessions s WHERE s.id = %s",
                (session_id,),
            )
            row = await cur.fetchone()
            return None if row is None else _session(row)

    async def lines(self, guild_id: int, session_id: str) -> list[Line]:
        """The session's lines in the order the speech started."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT started_ms, user_id, heard, coalesce(text, heard) AS text, duration_ms,"
                " topic FROM transcript_lines WHERE session_id = %s ORDER BY started_ms, id",
                (session_id,),
            )
            return [
                Line(
                    int(r["started_ms"]),
                    int(r["user_id"]),
                    r["heard"],
                    r["text"],
                    int(r["duration_ms"]),
                    r["topic"],
                )
                for r in await cur.fetchall()
            ]


def _session(row: dict[str, Any]) -> TranscriptSession:
    return TranscriptSession(
        id=str(row["id"]),
        guild_id=int(row["guild_id"]),
        campaign_id=str(row["campaign_id"]),
        started_at=int(row["started_at"]),
        ended_at=row_int(row, "ended_at"),
        lines=int(row["line_count"]),
        speakers=tuple(sorted(int(u) for u in row["speakers"])),
        number=int(row["number"]),
        engines=tuple(row["engines"]),
    )
