"""How much is waiting on one server, per kind, and how long the oldest has waited (#3846, part of
#3845): for the worker's log line. Counts and seconds only. A row's words (names, text, ids) never
reach the line, so nothing from a row can be read out of it. The age of a recap counts from when
it was first queued, so a retry looks old; that is deliberate for now.

Each count is one query inside `db.guild`, so another server's rows are never counted. A clock a
second behind gives an age of 0, never a negative one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.db import Database

# The three kinds, always in this order in the line.
KINDS = ("recaps", "audio", "imports")


@dataclass(frozen=True, slots=True)
class Depth:
    kind: str  # "recaps" | "audio" | "imports"
    waiting: int  # how many are waiting
    oldest_s: int  # seconds the oldest has waited, 0 when none wait


# One query per kind: how many are waiting, and the earliest known time they have waited since. A
# zero or missing time is "unknown": the row still counts as waiting, but it never sets the oldest
# wait (NULLIF turns 0 into NULL, and MIN ignores NULL). Recaps count from when they were first
# queued, so a retry looks old; that is deliberate for now.
_QUERIES = {
    "recaps": (
        "SELECT COUNT(*) AS n, MIN(NULLIF(created_at, 0)) AS since FROM recap_jobs"
        " WHERE guild_id = %s AND state = 'queued'"
    ),
    "audio": (
        "SELECT COUNT(*) AS n, MIN(COALESCE(NULLIF(audio_asked_at, 0), NULLIF(approved_at, 0),"
        " NULLIF(updated_at, 0))) AS since FROM recap_jobs"
        " WHERE guild_id = %s AND state = 'ready' AND audio = 'queued'"
    ),
    "imports": (
        "SELECT COUNT(*) AS n, MIN(NULLIF(imported_at, 0)) AS since FROM session_imports"
        " WHERE guild_id = %s AND state = 'waiting'"
    ),
}


async def queue_depths(db: Database, guild_id: int, now: int) -> tuple[Depth, ...]:
    """The three kinds for one server, in the order of `KINDS`."""
    depths: list[Depth] = []
    for kind in KINDS:
        async with db.guild(guild_id) as conn:
            cur = await conn.execute(_QUERIES[kind], (guild_id,))
            row = await cur.fetchone()
        waiting = int(row["n"]) if row else 0
        since = row["since"] if row else None
        oldest = max(0, now - int(since)) if since is not None else 0
        depths.append(Depth(kind, waiting, oldest))
    return tuple(depths)


def depth_line(depths: Sequence[Depth]) -> str:
    """e.g. 'queue: recaps 2 (oldest 41 s), audio 0, imports 1 (oldest 7 s)'. Numbers and fixed
    words only."""
    parts = []
    for depth in depths:
        if depth.waiting == 0:
            parts.append(f"{depth.kind} 0")
        else:
            parts.append(f"{depth.kind} {depth.waiting} (oldest {depth.oldest_s} s)")
    return "queue: " + ", ".join(parts)
