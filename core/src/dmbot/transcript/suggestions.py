"""A player's suggested fix to their own line of the cleaned transcript (#1491; docs/PLAN.md,
"Transcript Cleaner"). The bot advises; the DM decides: nothing changes until the DM presses
Accept, and even then only the line's cleaned `text` changes, never what was heard.

This file is the rules (pure) and the table (`SuggestionStore`). The buttons and the DM-screen
line are in `dmbot.ui.line_fix`; writing an accepted fix into a line is `Bot.apply_line_fix`.

- Only the line's speaker may suggest, and only for a line of theirs that was said (never a DM
  sidebar line, never a typed one: those are not speech, and a sidebar line is the DM's).
- At most `MAX_CHARS` characters, one line of plain text, one suggestion per line at a time.
- A suggestion that waits past the end of the next session is dropped, without a message.
"""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database

MAX_CHARS = 200
PICK_LINES = 10  # the speaker's newest lines offered to pick from
MAX_OPEN = 3  # fixes one person may have waiting for the DM at once
OPEN, ACCEPTED, DECLINED, DROPPED = "open", "accepted", "declined", "dropped"


def tidy(text: str) -> str | None:
    """The suggested words as one line of plain text, or None if they can't be used: empty, or
    longer than `MAX_CHARS`. Control and invisible direction characters are removed (they can
    flip how a line reads), and runs of spaces and line breaks become one space."""
    kept = "".join(c for c in text if unicodedata.category(c) not in ("Cf", "Cc") or c in " \t\n")
    one = " ".join(kept.split())
    return one if 0 < len(one) <= MAX_CHARS else None


@dataclass(frozen=True, slots=True)
class Suggestion:
    id: int
    guild_id: int
    campaign_id: str
    session_id: str
    started_ms: int
    user_id: int
    suggested: str
    state: str
    base_text: str = ""


@dataclass(frozen=True, slots=True)
class Own:
    """One of the speaker's lines, to pick from: when it started and its cleaned words."""

    started_ms: int
    heard: str
    text: str
    user_id: int  # whose line it is (set by the reads below)


@dataclass(frozen=True, slots=True)
class Waiting:
    """A suggestion still waiting for the DM, with its line as it reads now."""

    id: int
    user_id: int
    suggested: str
    heard: str
    text: str


_COLUMNS = "id, guild_id, campaign_id, session_id, started_ms, user_id, suggested, state, base_text"


def _row(r: dict[str, Any]) -> Suggestion:
    return Suggestion(
        int(r["id"]),
        int(r["guild_id"]),
        str(r["campaign_id"]),
        str(r["session_id"]),
        int(r["started_ms"]),
        int(r["user_id"]),
        str(r["suggested"]),
        str(r["state"]),
        str(r["base_text"]),
    )


class SuggestionStore:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def own_lines(
        self, guild_id: int, session_id: str, user_id: int, *, limit: int = PICK_LINES
    ) -> list[Own]:
        """This person's newest spoken lines of the session (table speech only), newest first,
        for them to pick from."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT started_ms, heard, coalesce(text, heard) AS text FROM transcript_lines"
                " WHERE session_id = %s AND user_id = %s AND sidebar IS NULL AND NOT typed"
                " ORDER BY started_ms DESC, id DESC LIMIT %s",
                (session_id, user_id, limit),
            )
            return [
                Own(int(r["started_ms"]), r["heard"], r["text"], user_id)
                for r in await cur.fetchall()
            ]

    async def recent_lines(
        self, guild_id: int, session_id: str, *, limit: int = PICK_LINES
    ) -> list[Own]:
        """The session's newest spoken lines of ANY speaker (table speech only: never a DM
        sidebar line, never typed chat), newest first, for a DM to pick from (#1499)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT started_ms, user_id, heard, coalesce(text, heard) AS text"
                " FROM transcript_lines WHERE session_id = %s AND sidebar IS NULL AND NOT typed"
                " ORDER BY started_ms DESC, id DESC LIMIT %s",
                (session_id, limit),
            )
            return [
                Own(int(r["started_ms"]), r["heard"], r["text"], int(r["user_id"]))
                for r in await cur.fetchall()
            ]

    async def line(
        self, guild_id: int, session_id: str, user_id: int, started_ms: int
    ) -> Own | None:
        """One of this person's spoken lines, or None (not theirs, not speech, or not there)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT started_ms, heard, coalesce(text, heard) AS text FROM transcript_lines"
                " WHERE session_id = %s AND user_id = %s AND started_ms = %s"
                " AND sidebar IS NULL AND NOT typed ORDER BY id LIMIT 1",
                (session_id, user_id, started_ms),
            )
            r = await cur.fetchone()
        return None if r is None else Own(int(r["started_ms"]), r["heard"], r["text"], user_id)

    async def suggest(
        self,
        guild_id: int,
        campaign_id: str,
        session_id: str,
        user_id: int,
        started_ms: int,
        suggested: str,
    ) -> Suggestion | str:
        """Record a suggestion for the speaker's own line. The new suggestion, or why not:
        "none" (no such line of theirs), "open" (this line already has one waiting) or "many"
        (they have `MAX_OPEN` waiting already). One statement does the work, under a lock for
        this person, so two at once can't both pass the limit."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(%s)", (user_id,))
            cur = await conn.execute(
                "INSERT INTO transcript_suggestions (guild_id, campaign_id, session_id,"
                " started_ms, user_id, suggested, base_text, created_at)"
                " SELECT %s, %s, l.session_id, l.started_ms, l.user_id, %s,"
                "   coalesce(l.text, l.heard), %s"
                " FROM transcript_lines l JOIN transcript_sessions s"
                "   ON s.id = l.session_id AND s.guild_id = l.guild_id"
                " WHERE l.session_id = %s AND l.user_id = %s AND l.started_ms = %s"
                " AND l.sidebar IS NULL AND NOT l.typed AND s.campaign_id = %s"
                " AND (SELECT count(*) FROM transcript_suggestions t"
                "      WHERE t.campaign_id = %s AND t.user_id = %s AND t.state = 'open') < %s"
                " ORDER BY l.id LIMIT 1"
                " ON CONFLICT (session_id, user_id, started_ms) WHERE state = 'open' DO NOTHING"
                f" RETURNING {_COLUMNS}",
                (
                    guild_id,
                    campaign_id,
                    suggested,
                    int(self._now()),
                    session_id,
                    user_id,
                    started_ms,
                    campaign_id,
                    campaign_id,
                    user_id,
                    MAX_OPEN,
                ),
            )
            row = await cur.fetchone()
            if row is not None:
                return _row(row)
            # Not made: say why (all in this person's own rows, under the same lock).
            cur = await conn.execute(
                "SELECT"
                " (SELECT count(*) FROM transcript_suggestions WHERE session_id = %s"
                "   AND user_id = %s AND started_ms = %s AND state = 'open') AS here,"
                " (SELECT count(*) FROM transcript_suggestions WHERE campaign_id = %s"
                "   AND user_id = %s AND state = 'open') AS waiting",
                (session_id, user_id, started_ms, campaign_id, user_id),
            )
            why = await cur.fetchone()
        if why is not None and int(why["here"]) > 0:
            return "open"
        if why is not None and int(why["waiting"]) >= MAX_OPEN:
            return "many"
        return "none"

    async def get(self, guild_id: int, suggestion_id: int) -> Suggestion | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM transcript_suggestions WHERE id = %s", (suggestion_id,)
            )
            row = await cur.fetchone()
        return None if row is None else _row(row)

    async def waiting(self, guild_id: int, campaign_id: str, *, limit: int = 50) -> list[Waiting]:
        """The campaign's open suggestions, oldest first, each with its line's words now (a
        suggestion whose line is gone is left out). For the reminder at the next session start."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT s.id, s.user_id, s.suggested, l.heard, coalesce(l.text, l.heard) AS text"
                " FROM transcript_suggestions s CROSS JOIN LATERAL ("
                "  SELECT heard, text FROM transcript_lines l"
                "  WHERE l.session_id = s.session_id AND l.user_id = s.user_id"
                "  AND l.started_ms = s.started_ms AND l.sidebar IS NULL AND NOT l.typed"
                "  ORDER BY l.id LIMIT 1) l"
                " WHERE s.campaign_id = %s AND s.state = 'open'"
                " ORDER BY s.created_at, s.id LIMIT %s",
                (campaign_id, limit),
            )
            rows = await cur.fetchall()
        return [
            Waiting(int(r["id"]), int(r["user_id"]), r["suggested"], r["heard"], r["text"])
            for r in rows
        ]

    async def session_counts(self, guild_id: int, session_id: str) -> tuple[int, int]:
        """How many fixes players suggested for this session, and how many still wait (#1510)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS total, count(*) FILTER (WHERE state = 'open') AS waiting"
                " FROM transcript_suggestions WHERE session_id = %s",
                (session_id,),
            )
            row = await cur.fetchone()
        return (int(row["total"]), int(row["waiting"])) if row is not None else (0, 0)

    async def decide(self, guild_id: int, suggestion_id: int, state: str, by: int) -> bool:
        """The DM's answer (accepted or declined). True only for the call that closed it: a
        second press, or one on a dropped suggestion, changes nothing."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE transcript_suggestions SET state = %s, decided_at = %s, decided_by = %s"
                " WHERE id = %s AND state = 'open'",
                (state, int(self._now()), by, suggestion_id),
            )
            return cur.rowcount == 1

    async def reopen(self, guild_id: int, suggestion_id: int) -> None:
        """An Accept that couldn't be written goes back to waiting, so the DM can try again. If
        the player has made a new suggestion for that line meanwhile, this one is dropped instead
        (only one may wait)."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE transcript_suggestions s SET"
                " state = CASE WHEN EXISTS (SELECT 1 FROM transcript_suggestions o"
                "   WHERE o.session_id = s.session_id AND o.user_id = s.user_id"
                "   AND o.started_ms = s.started_ms AND o.state = 'open' AND o.id <> s.id)"
                "   THEN 'dropped' ELSE 'open' END,"
                " decided_at = NULL, decided_by = NULL"
                " WHERE s.id = %s AND s.state = 'accepted'",
                (suggestion_id,),
            )

    async def drop_old(self, guild_id: int, campaign_id: str, keep: Sequence[str]) -> int:
        """Drop the campaign's waiting suggestions for any session but these (the one that just
        ended and the one before it): a suggestion that waits past the end of the next session
        goes, without a message. How many were dropped."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE transcript_suggestions SET state = 'dropped', decided_at = %s"
                " WHERE campaign_id = %s AND state = 'open' AND session_id <> ALL(%s)",
                (int(self._now()), campaign_id, list(keep)),
            )
            return cur.rowcount
