"""Which of a time limit's two private messages were said (quests slice 4b; design
`docs/design/quests.md` §8.4; Supervisor's choice A on #4448; migration 0508).

Each message is said once for each part row: "soon" (a game day before, or halfway when set less
than two days ahead) and "passed" ("time's up"). The claim is written before the message is sent
(ON CONFLICT DO NOTHING), so two clock moves at the same moment say it once. It is kept apart from
the change log, so neither an Undo nor a clock set back and forward again repeats a message, and a
deadline moved is a new part row, told afresh. When the clock jumps straight past a limit, both
are claimed and only "time's up" is said: "soon" never comes after it. Counts only in the log.
"""

from __future__ import annotations

from typing import Any

from dmbot.memory.time_limits import PASSED, SOON


async def claim(
    conn: Any, guild_id: int, campaign_id: str, part_id: str, stage: str, at: int
) -> bool:
    """Mark this message said; False when it was said already (here or by a move at the same time).
    For PASSED, "soon" is marked said too, so it never follows."""
    stages = (SOON, PASSED) if stage == PASSED else (SOON,)
    fresh = False
    for s in stages:
        cur = await conn.execute(
            "INSERT INTO time_limit_told (guild_id, campaign_id, part_id, stage, told_at)"
            " VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (guild_id, campaign_id, part_id, s, max(at, 1)),
        )
        if s == stage:
            fresh = bool(cur.rowcount)
    return fresh
