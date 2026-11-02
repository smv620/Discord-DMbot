"""Where a session's theatrical transcript is kept (#3244): the DM's working copy, made from the
cleaned lines, never shown to players and never in a backup. Reads and writes go through
`Database.guild`, so Postgres only shows that server's rows.

Two promises are kept here and not left to the caller:
- **A row exists only while its sources do.** `replace` re-reads the lines a row cites, inside the
  transaction that saves it, and drops a row when any is gone (a person erased their words while
  the AI was away) or when a source is not in this session. It also drops a row whose speaker is not
  one of its sources' speakers. Counts only come back.
- **A row goes with the words it holds.** `forget_person` and `forget_session` are called by the
  transcript store's own erase paths (`delete_person`, a session's retention) in their transaction.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
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
    """What a save did, as counts only."""

    kept: int
    dropped: int


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
        speakers, or whose text is empty is left out and counted. Nothing is cut for length."""
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
                    " AND session_id = %s AND id = ANY(%s)",
                    (guild_id, session_id, batch),
                )
                who.update({int(r["id"]): int(r["user_id"]) for r in await cur.fetchall()})
            await conn.execute(
                "DELETE FROM theatre_lines WHERE guild_id = %s AND session_id = %s",
                (guild_id, session_id),
            )
            now = int(time.time())
            kept = 0
            for row in rows:
                if not row.text.strip() or not row.source_ids:
                    continue
                if not all(n in who for n in row.source_ids):
                    continue  # a source went (or was never this session's)
                users = sorted({who[n] for n in row.source_ids})
                if row.user_id not in users or len(users) > 50 or len(row.source_ids) > 200:
                    continue
                await conn.execute(
                    "INSERT INTO theatre_lines (guild_id, session_id, position, user_id, user_ids,"
                    " source_ids, text, made_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (guild_id, session_id, kept, row.user_id, users,
                     sorted(set(row.source_ids)), row.text, now),
                )  # fmt: skip
                kept += 1
            return Saved(kept, len(rows) - kept)

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


async def forget_person(conn: Any, guild_id: int, user_id: int) -> int:
    """Every script line made from this person's words, in the caller's transaction (called by
    `TranscriptStore.delete_person`). A row that lists them goes whole, even when others spoke in
    it too: a script line is one thought, and keeping half of it would keep their words."""
    cur = await conn.execute(
        "DELETE FROM theatre_lines WHERE guild_id = %s AND %s = ANY(user_ids)",
        (guild_id, user_id),
    )
    return int(cur.rowcount)


async def forget_session(conn: Any, guild_id: int, session_id: str) -> int:
    """The whole script of one session, in the caller's transaction (called when a session's
    transcript is deleted by retention; the session row stays, so the cascade does not fire)."""
    cur = await conn.execute(
        "DELETE FROM theatre_lines WHERE guild_id = %s AND session_id = %s", (guild_id, session_id)
    )
    return int(cur.rowcount)
