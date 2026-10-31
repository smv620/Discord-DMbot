"""Session notes: each session's beat sheet, kept with the campaign (#3026).

The recap's beat sheet used to live only in the recap job, as a step toward the recap. A DM's
session notes are the same sheet, kept for good: it survives the deletion of transcript lines
(Delete my transcripts, Stop recording me, "Keep transcripts for") and goes only with the campaign.
One current sheet for each session; a redone recap replaces it. A beat's `lines` point at numbered
lines of the transcript as it was read; when lines of a session are deleted those numbers no longer
mean anything (the numbering shifts), so every pointer of that session is dropped
(`transcript.store._drop_note_pointers`, in the deleting transaction) and each beat's text stays.
Words only, per campaign and isolated by row-level security; it goes in the campaign's backup
(`SessionNotesSection`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database
from dmbot.recaps import beats as bt

MAX_SESSIONS = 1000  # notes in one campaign's backup
MAX_SHEET_CHARS = 200_000  # one sheet's JSON (the sheet itself holds at most bt.MAX_BEATS beats)
INT64_MAX = 2**63 - 1
DAMAGED = (
    "This backup file is damaged or isn't a DMbot campaign backup. "
    "Pick the file you got from `/dmbot backup`."
)


@dataclass(frozen=True, slots=True)
class Notes:
    session_at: int
    beats: list[Any]  # the stored sheet; `bt.from_json` reads it
    written_at: int
    job_created_at: int  # the recap job it came from (0: from a backup)

    def sheet(self) -> list[bt.Beat]:
        return bt.from_json(self.beats)


def _notes(row: Any) -> Notes:
    return Notes(
        int(row["session_at"]),
        list(row["beats"]),
        int(row["written_at"]),
        int(row["job_created_at"]),
    )


_COLUMNS = "session_at, beats, written_at, job_created_at"


async def keep_from_job(conn: Conn, guild_id: int, campaign_id: str, session_at: int) -> None:
    """Keep the beat sheet the recap job holds as the session's notes, replacing the ones there.
    A job with no beats writes nothing: a recap started over clears its job's beats, not the
    notes."""
    await conn.execute(
        "INSERT INTO session_notes (guild_id, campaign_id, session_at, beats, written_at,"
        " job_created_at) SELECT guild_id, campaign_id, session_at, beats, updated_at, created_at"
        " FROM recap_jobs WHERE guild_id = %s AND campaign_id = %s AND session_at = %s"
        " AND jsonb_typeof(beats) = 'array' AND jsonb_array_length(beats) > 0"
        " ON CONFLICT (guild_id, campaign_id, session_at) DO UPDATE SET beats = excluded.beats,"
        " written_at = excluded.written_at, job_created_at = excluded.job_created_at",
        (guild_id, campaign_id, session_at),
    )


class SessionNotesStore:
    """Reads of the session notes. The writing is the recap pipeline's (`write`)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, guild_id: int, campaign_id: str, session_at: int) -> Notes | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM session_notes WHERE guild_id = %s AND campaign_id = %s"
                " AND session_at = %s",
                (guild_id, campaign_id, session_at),
            )
            row = await cur.fetchone()
            return None if row is None else _notes(row)

    async def all(
        self,
        guild_id: int,
        campaign_id: str,
        *,
        before: int | None = None,
        last: int | None = None,
        transcript_gone: bool = False,
    ) -> list[Notes]:
        """Every session's notes, oldest session first. `before` keeps only sessions earlier than
        that one and `last` only the latest that many (the website's list and the writer's
        context). `transcript_gone`: only sessions whose transcript no longer exists (no session
        row, or its lines were deleted), which is when the writer needs the notes (#3026)."""
        gone = (
            " AND NOT EXISTS (SELECT 1 FROM transcript_sessions s WHERE s.guild_id = n.guild_id"
            " AND s.campaign_id = n.campaign_id AND s.started_at = n.session_at"
            " AND s.transcripts_deleted_at IS NULL AND (s.line_count > 0 OR EXISTS ("
            "SELECT 1 FROM transcript_lines l WHERE l.session_id = s.id)))"
            if transcript_gone
            else ""
        )
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT n.session_at, n.beats, n.written_at, n.job_created_at"
                " FROM session_notes n WHERE n.guild_id = %s AND n.campaign_id = %s"
                " AND (%s::bigint IS NULL OR n.session_at < %s)"
                + gone
                + " ORDER BY n.session_at DESC LIMIT %s",
                (guild_id, campaign_id, before, before, last),
            )
            return [_notes(r) for r in reversed(await cur.fetchall())]


def numbered(notes: Notes) -> str:
    """The beats as a numbered list, one beat to a line (a Discord message, the writer's context).
    Nothing is shortened: a platform's own limit splits the text into parts."""
    return "\n".join(f"{b.number}. {b.what}" for b in notes.sheet())


class SessionNotesSection:
    """The session notes in campaign backups (an `ExportSection`)."""

    name = "session_notes"
    _KEYS = frozenset({"session_at", "beats", "written_at", "job_created_at"})

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            f"SELECT {_COLUMNS} FROM session_notes WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY session_at",
            (guild_id, campaign_id),
        )
        return [
            {
                "session_at": n.session_at,
                "beats": n.beats,
                "written_at": n.written_at,
                "job_created_at": n.job_created_at,
            }
            for n in map(_notes, await cur.fetchall())
        ]

    def check(self, rows: list[Any]) -> list[Notes]:
        if not isinstance(rows, list) or len(rows) > MAX_SESSIONS:
            raise CampaignError(DAMAGED)
        out: list[Notes] = []
        seen: set[int] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != self._KEYS:
                raise CampaignError(DAMAGED)
            at, written, created, sheet = (
                row["session_at"], row["written_at"], row["job_created_at"], row["beats"]
            )  # fmt: skip
            if (
                any(type(n) is not int for n in (at, written, created))
                or not 0 < at <= INT64_MAX
                or not 0 <= written <= INT64_MAX
                or not 0 <= created <= INT64_MAX
                or at in seen
                or not isinstance(sheet, list)
                or not sheet
                or len(sheet) > bt.MAX_BEATS
                or len(json.dumps(sheet)) > MAX_SHEET_CHARS
                or len(bt.from_json(sheet)) != len(sheet)  # every beat must read back
            ):
                raise CampaignError(DAMAGED)
            seen.add(at)
            # (the canonical form: nothing but the beats' own fields is stored from a file)
            out.append(Notes(at, bt.as_json(bt.from_json(sheet)), written, created))
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        checked = (
            rows
            if isinstance(rows, list) and rows and all(isinstance(r, Notes) for r in rows)
            else self.check(rows or [])
        )
        for n in checked:
            # (the backup's own notes win over any made from its recaps' beats, in either order)
            await conn.execute(
                "INSERT INTO session_notes (guild_id, campaign_id, session_at, beats, written_at,"
                " job_created_at) VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, session_at) DO UPDATE SET"
                " beats = excluded.beats, written_at = excluded.written_at,"
                " job_created_at = excluded.job_created_at",
                (
                    guild_id,
                    campaign_id,
                    n.session_at,
                    Jsonb(n.beats),
                    n.written_at,
                    n.job_created_at,
                ),
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM session_notes WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
