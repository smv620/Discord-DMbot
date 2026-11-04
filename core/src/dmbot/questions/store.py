"""The stored counts and reminder timers of "Questions for you" (#3435). Counts, times and
message ids only, never a name or the words of a question."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from dmbot.db import Database
from dmbot.questions.counts import Counted


class QuestionCounts:
    """`question_counts`: the bot writes it, the website reads it through a DM-only door."""

    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def read(self, guild_id: int, campaign_id: str) -> dict[str, Counted]:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT kind, n, oldest, settled FROM question_counts"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            rows = await cur.fetchall()
        return {
            str(r["kind"]): Counted(
                int(r["n"]),
                None if r["oldest"] is None else int(r["oldest"]),
                None if r["settled"] is None else int(r["settled"]),
            )
            for r in rows
        }

    async def write(self, guild_id: int, campaign_id: str, counts: Mapping[str, Counted]) -> None:
        """Store these counts (a kind not in `counts` keeps its old row)."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            for kind, found in counts.items():
                await conn.execute(
                    "INSERT INTO question_counts"
                    " (guild_id, campaign_id, kind, n, oldest, settled, counted_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (guild_id, campaign_id, kind) DO UPDATE SET n = EXCLUDED.n,"
                    " oldest = EXCLUDED.oldest, settled = EXCLUDED.settled,"
                    " counted_at = EXCLUDED.counted_at",
                    (guild_id, campaign_id, kind, found.n, found.oldest, found.settled, now),
                )


def total(counts: Mapping[str, Counted]) -> int:
    from dmbot.questions import kinds

    return sum(c.n for k, c in counts.items() if k in kinds.SHOWN)


def answered(before: Mapping[str, Counted], after: Mapping[str, Counted]) -> bool:
    """Whether something was answered between two counts (#3435): a kind that waited now waits
    less (a few things also lower a count without an answer, such as "Stop recording me" or a
    recap text edit; that only resets a reminder a little early), or, for a kind whose questions
    also expire, more answers were given. A kind that was not counted before (the first round, a
    count that failed) is never an answer."""
    from dmbot.questions import kinds

    for kind, now in after.items():
        if kind not in kinds.SHOWN:
            continue
        was = before.get(kind)
        if was is None:
            continue
        if kind in kinds.EXPIRING:
            if (now.settled or 0) > (was.settled or 0):
                return True
        elif now.n < was.n:
            return True
    return False


def grew(before: Mapping[str, Counted], after: Mapping[str, Counted]) -> bool:
    """Whether a new question arrived: some kind waits more than before."""
    from dmbot.questions import kinds

    return any(
        now.n > (before[k].n if k in before else 0) for k, now in after.items() if k in kinds.SHOWN
    )
