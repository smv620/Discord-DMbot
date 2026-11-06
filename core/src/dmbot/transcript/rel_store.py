"""Where the relationship reader's ties for a session are kept (#3244 slice 6, stage 1 of #3242):
DM-only working data for one comparison against the old reader, never shown to anyone and never in
a backup. Reads and writes go through `Database.guild`, so Postgres only shows that server's rows.

**Nothing reads this but the comparison.** No list, card, graph, recap, table answer or website
door may call `RelStore.read` (a guard test names the files that may); storing real connections
from these ties is stage 2, a separate decision after the comparison shows no pair worse.

The promises are the script store's (`theatre_store`), kept here and not left to the caller:
- **A row exists only while its sources do.** `replace` re-reads the lines a row cites inside the
  transaction that saves it and drops a row when any is gone (a person erased their words while
  the AI was away), is not this session's, or is not one of a speaker who agrees (`agreed`).
- **A row goes with the words it holds**, structurally: triggers on `transcript_lines` (migration
  0345) remove the rows that cite a deleted line and the session's rows when a line's words,
  speaker or owner change. `forget_person` and `forget_session` are called as well by
  `delete_person` and a session's retention.
Counts only come back; nothing here logs a word of the session.
"""

from __future__ import annotations

import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database

MAX_SOURCES = 200
MAX_SPEAKERS = 50


@dataclass(frozen=True, slots=True)
class TieRow:
    """One tie to keep, as the run decided it: `kind` is the type kept, `ai_kind` the reader's own
    and `chosen_by` how (`rel_types.USED` or `AI_PICK`); `source_ids` are transcript line ids."""

    a: str
    b: str
    kind: str
    ai_kind: str
    chosen_by: str
    word: str | None
    sentence: str
    source_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Saved:
    kept: int
    dropped: int  # sources gone, not this session's, a speaker who no longer agrees, or too big


class RelStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def replace(
        self, guild_id: int, session_id: str, rows: Sequence[TieRow], agreed: Collection[int]
    ) -> Saved:
        """Make this session's ties exactly `rows`, in one transaction. A row is left out (and
        counted) when any source line is gone, is not a table-talk line of this session, belongs to
        a person not in `agreed`, or it cites too many lines or people. A save with no usable row
        changes nothing, so a failed or empty run never wipes good ties."""
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
            allowed = set(agreed)
            good: list[tuple[TieRow, list[int], list[int]]] = []
            for row in rows:
                sources = sorted(set(row.source_ids))
                if not sources or len(sources) > MAX_SOURCES:
                    continue
                if not all(n in who for n in sources):
                    continue  # a source went (or was never this session's)
                users = sorted({who[n] for n in sources})
                if len(users) > MAX_SPEAKERS or not set(users) <= allowed:
                    continue  # (a person who stopped: their words are never kept)
                if not row.sentence.strip() or not row.a.strip() or not row.b.strip():
                    continue
                good.append((row, users, sources))
            if not good:
                return Saved(0, len(rows))
            await conn.execute(
                "DELETE FROM rel_ties WHERE guild_id = %s AND session_id = %s",
                (guild_id, session_id),
            )
            now = int(time.time())
            for row, users, sources in good:
                await conn.execute(
                    "INSERT INTO rel_ties (guild_id, session_id, a, b, kind, ai_kind, chosen_by,"
                    " word, sentence, user_ids, source_ids, made_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        guild_id, session_id, row.a, row.b, row.kind, row.ai_kind, row.chosen_by,
                        row.word, row.sentence.replace("\x00", ""), users, sources, now,
                    ),
                )  # fmt: skip
            return Saved(len(good), len(rows) - len(good))

    async def read(self, guild_id: int, session_id: str) -> list[TieRow]:
        """The ties, for dev1's comparison only (see the module docstring)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT a, b, kind, ai_kind, chosen_by, word, sentence, source_ids FROM rel_ties"
                " WHERE guild_id = %s AND session_id = %s ORDER BY id",
                (guild_id, session_id),
            )
            return [
                TieRow(
                    str(r["a"]), str(r["b"]), str(r["kind"]), str(r["ai_kind"]),
                    str(r["chosen_by"]), None if r["word"] is None else str(r["word"]),
                    str(r["sentence"]), tuple(int(x) for x in r["source_ids"]),
                )
                for r in await cur.fetchall()
            ]  # fmt: skip


async def forget_person(
    conn: Any, guild_id: int, user_id: int, campaign_id: str | None = None
) -> int:
    """Every tie made from this person's words, in the caller's transaction (called by
    `TranscriptStore.delete_person`). A row that lists them goes whole, even when others spoke in
    it too. `campaign_id`: only that campaign's sessions (a DM's removal of one speaker)."""
    cur = await conn.execute(
        "DELETE FROM rel_ties WHERE guild_id = %s AND user_ids @> ARRAY[%s]::bigint[]"
        " AND (%s::text IS NULL OR session_id IN (SELECT t.id FROM transcript_sessions t"
        "   WHERE t.guild_id = %s AND t.campaign_id = %s))",
        (guild_id, user_id, campaign_id, guild_id, campaign_id),
    )
    return int(cur.rowcount)


async def forget_session(conn: Any, guild_id: int, session_id: str) -> int:
    """The ties of one session, in the caller's transaction (a session's retention)."""
    cur = await conn.execute(
        "DELETE FROM rel_ties WHERE guild_id = %s AND session_id = %s", (guild_id, session_id)
    )
    return int(cur.rowcount)
