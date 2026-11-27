"""Where a session's theatrical transcript is kept (#3244): the DM's working copy, made from the
cleaned lines, never shown to players and never in a backup. Reads and writes go through
`Database.guild`, so Postgres only shows that server's rows.

Two promises are kept here and not left to the caller:
- **A row exists only while its sources do.** `replace` re-reads the lines a row cites, inside the
  transaction that saves it, and drops a row when any is gone (a person erased their words while
  the AI was away) or when a source is not in this session. It also drops a row whose speaker is not
  one of its sources' speakers. Counts only come back.
- **A row goes with the words it holds, structurally.** Two triggers on `transcript_lines`
  (migration 0317) remove the script rows that cite a line when it is deleted, and the session's
  whole script when a line's words, speaker or owner change, so no path (an Undo, a respell and its
  undo, an import's cleaning, a stopped person's lines taken back, any future one) can forget.
  `forget_person` and `forget_session` are called as well by `delete_person` and a session's
  retention: a person's rows are found by the id they list, whoever else spoke in them. A deleted
  session or campaign cascades.

The store does not know who still agrees to be recorded: the run (slice 3) builds its scenes only
from consenting people (`theatre.scenes`) and checks again before it saves. A save with no rows
changes nothing, so a failed or empty run never wipes a good script.
"""

from __future__ import annotations

import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database


@dataclass(frozen=True, slots=True)
class Row:
    """One line of the script to keep: who says it, the words with brackets, and the transcript
    line ids it was made from (the lineage)."""

    user_id: int
    text: str
    source_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Saved:
    """What a save did, as counts only. `dropped`: rows whose sources are gone, are not this
    session's, or whose speaker is not among them, or that are empty. `too_big`: rows citing more
    than `MAX_SOURCES` lines or `MAX_SPEAKERS` people; the caller splits those and saves again,
    so a long thought is never lost to a limit."""

    kept: int
    dropped: int
    too_big: int = 0


MAX_SOURCES = 200
MAX_SPEAKERS = 50


@dataclass(frozen=True, slots=True)
class Stored:
    """One kept line, in order."""

    position: int
    user_id: int
    user_ids: tuple[int, ...]
    source_ids: tuple[int, ...]
    text: str


class TheatreStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def replace(self, guild_id: int, session_id: str, rows: Sequence[Row]) -> Saved:
        """Make this session's script exactly `rows` (in order), in one transaction. A row whose
        sources are not all lines of this session, whose speaker is not among its sources'
        speakers, or whose text is empty is left out and counted. A row over `MAX_SOURCES` or
        `MAX_SPEAKERS` is counted apart (`too_big`) for the caller to split. Only table-talk lines
        of the cleaned transcript can be cited (never the DM sidebar). No text is cut for length."""
        if not rows:
            return Saved(0, 0)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM transcript_sessions WHERE guild_id = %s AND id = %s FOR UPDATE",
                (guild_id, session_id),
            )
            if await cur.fetchone() is None:
                return Saved(0, len(rows))
            wanted = sorted({n for r in rows for n in r.source_ids})
            who: dict[int, int] = {}
            for batch in (wanted[i : i + 1000] for i in range(0, len(wanted), 1000)):
                cur = await conn.execute(
                    "SELECT id, user_id FROM transcript_lines WHERE guild_id = %s"
                    " AND session_id = %s AND sidebar IS NULL AND id = ANY(%s)",
                    (guild_id, session_id, batch),
                )
                who.update({int(r["id"]): int(r["user_id"]) for r in await cur.fetchall()})
            good: list[tuple[int, list[int], list[int], str]] = []
            too_big = 0
            for row in rows:
                text = row.text.replace("\x00", "").strip()
                if not text or not row.source_ids:
                    continue
                if len(set(row.source_ids)) > MAX_SOURCES:
                    too_big += 1
                    continue
                if not all(n in who for n in row.source_ids):
                    continue  # a source went (or was never this session's)
                users = sorted({who[n] for n in row.source_ids})
                if len(users) > MAX_SPEAKERS:
                    too_big += 1
                    continue
                if row.user_id not in users:
                    continue
                good.append((row.user_id, users, sorted(set(row.source_ids)), text))
            if not good:  # nothing usable: the script already kept is not wiped for it
                return Saved(0, len(rows) - too_big, too_big)
            await conn.execute(
                "DELETE FROM theatre_lines WHERE guild_id = %s AND session_id = %s",
                (guild_id, session_id),
            )
            now = int(time.time())
            for position, (user_id, users, sources, text) in enumerate(good):
                await conn.execute(
                    "INSERT INTO theatre_lines (guild_id, session_id, position, user_id, user_ids,"
                    " source_ids, text, made_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (guild_id, session_id, position, user_id, users, sources, text, now),
                )
            return Saved(len(good), len(rows) - len(good) - too_big, too_big)

    async def read(self, guild_id: int, session_id: str) -> list[Stored]:
        """The script in order. For the DM's page and download only: the caller has checked that
        the person asking is the campaign's DM."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT position, user_id, user_ids, source_ids, text FROM theatre_lines"
                " WHERE guild_id = %s AND session_id = %s ORDER BY position",
                (guild_id, session_id),
            )
            return [
                Stored(
                    int(r["position"]), int(r["user_id"]),
                    tuple(int(x) for x in r["user_ids"]),
                    tuple(int(x) for x in r["source_ids"]), str(r["text"]),
                )
                for r in await cur.fetchall()
            ]  # fmt: skip

    async def read_by_start(self, guild_id: int, campaign_id: str, session_at: int) -> list[Stored]:
        """The script of the session that started at `session_at` (what a recap job is keyed by),
        for the recap's checks (#3901): only the campaign's own session, in order. Nothing is
        returned for a session with no script."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT l.position, l.user_id, l.user_ids, l.source_ids, l.text"
                " FROM theatre_lines l JOIN transcript_sessions s"
                " ON s.guild_id = l.guild_id AND s.id = l.session_id"
                " WHERE l.guild_id = %s AND s.campaign_id = %s AND s.started_at = %s"
                " ORDER BY l.position",
                (guild_id, campaign_id, session_at),
            )
            return [
                Stored(
                    int(r["position"]), int(r["user_id"]),
                    tuple(int(x) for x in r["user_ids"]),
                    tuple(int(x) for x in r["source_ids"]), str(r["text"]),
                )
                for r in await cur.fetchall()
            ]  # fmt: skip


async def forget_person(
    conn: Any, guild_id: int, user_id: int, campaign_id: str | None = None
) -> int:
    """Every script line made from this person's words, in the caller's transaction (called by
    `TranscriptStore.delete_person`). A row that lists them goes whole, even when others spoke in
    it too: a script line is one thought, and keeping half of it would keep their words.
    `campaign_id`: only that campaign's sessions (a DM's removal of one speaker, #3305)."""
    cur = await conn.execute(
        "DELETE FROM theatre_lines WHERE guild_id = %s AND user_ids @> ARRAY[%s]::bigint[]"
        " AND (%s::text IS NULL OR session_id IN (SELECT t.id FROM transcript_sessions t"
        "   WHERE t.guild_id = %s AND t.campaign_id = %s))",
        (guild_id, user_id, campaign_id, guild_id, campaign_id),
    )
    return int(cur.rowcount)


async def forget_session(conn: Any, guild_id: int, session_id: str) -> int:
    """The whole script of one session, in the caller's transaction (called when a session's
    transcript is deleted by retention; the session row stays, so the cascade does not fire)."""
    cur = await conn.execute(
        "DELETE FROM theatre_lines WHERE guild_id = %s AND session_id = %s", (guild_id, session_id)
    )
    return int(cur.rowcount)


async def script_input(
    db: Database, guild_id: int, session_id: str, agreed: Collection[int]
) -> list[tuple[int, int, str, bool]]:
    """(line id, user id, cleaned words, typed) of the lines the script may be made from, in
    the order they were said: the speech and the typed table chat of people who agree (typed lines
    are marked, #3244). Not the DM sidebar or table questions, not pictures and nothing the
    off-topic filter hid. A line the cleaner left alone is its heard words, so the
    caller starts a run only for a session that has ended and been cleaned."""
    if not agreed:
        return []
    async with db.guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT id, user_id, coalesce(text, heard) AS text, typed FROM transcript_lines"
            " WHERE guild_id = %s AND session_id = %s AND sidebar IS NULL"
            " AND NOT image AND topic <> 'off_topic' AND user_id = ANY(%s)"
            " AND coalesce(text, heard) <> '' ORDER BY started_ms, id",
            (guild_id, session_id, sorted(agreed)),
        )
        return [
            (int(r["id"]), int(r["user_id"]), str(r["text"]), bool(r["typed"]))
            for r in await cur.fetchall()
        ]
