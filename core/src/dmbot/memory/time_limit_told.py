"""Which of a time limit's two private messages were said (quests slice 4b; design
`docs/design/quests.md` §8.4; Supervisor's choice A on #4448; migration 0508).

Each message is said once for each limit (its `first_id`) and date: "soon" (a game day before, or
halfway when set less than two days ahead) and "passed" ("time's up"). The claim is written before
the message is sent (ON CONFLICT DO NOTHING), so two clock moves at the same moment say it once.
It is kept apart from the change log, so neither an Undo nor a clock set back and forward again
repeats a message. A deadline moved to another date is told afresh; a limit made hard, or ticked
and unticked, is not. When the clock jumps straight past a limit, both are claimed and only
"time's up" is said: "soon" never comes after it. Counts only in the log.
"""

from __future__ import annotations

from typing import Any

from dmbot.memory.time_limits import PASSED, SOON


async def told(conn: Any, guild_id: int, campaign_id: str) -> set[tuple[str, int, str]]:
    """What was said already, as (first_id, due minute, stage): checked before anything heavier,
    so a passed limit costs one small read per clock move (the reviewer on 4b)."""
    cur = await conn.execute(
        "SELECT first_id, due_minute, stage FROM time_limit_told"
        " WHERE guild_id = %s AND campaign_id = %s",
        (guild_id, campaign_id),
    )
    return {
        (str(r["first_id"]), int(r["due_minute"]), str(r["stage"])) for r in await cur.fetchall()
    }


async def claim(
    conn: Any, guild_id: int, campaign_id: str, first_id: str, due: int, stage: str, at: int
) -> bool:
    """Mark this message said for this limit and date; False when it was said already (here or by
    a move at the same time). For PASSED, "soon" is marked said too, so it never follows."""
    stages = (SOON, PASSED) if stage == PASSED else (SOON,)
    fresh = False
    for s in stages:
        cur = await conn.execute(
            "INSERT INTO time_limit_told (guild_id, campaign_id, first_id, due_minute, stage,"
            " told_at) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (guild_id, campaign_id, first_id, due, s, max(at, 1)),
        )
        if s == stage:
            fresh = bool(cur.rowcount)
    return fresh
