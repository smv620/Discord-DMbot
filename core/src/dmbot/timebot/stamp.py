"""The game time of a moment, from the game clock's log (#3091, part 3). Reads only; the rows are
written by a trigger on the clock tables (migration 0305), so every change to a clock is in it.

A line spoken at real time T has the game minute of the newest log row at or before T: the clock
as it stood when the line was spoken, never as it reads later. Before the first row, or with no
clock, the answer is None, which every reader takes as "time not known".

Which clock: the speaker's group if they are in one (`group`, 1 to 3), otherwise the main clock.
While the party is split the main clock stands still (it is the clock from before the split, the
clock again at Rejoin), so asking for it then would give a minute the table has long left: that
case is "not known", not the stale minute. (Nothing records which group a character is in yet, so
a caller has no group to give today and a line spoken during a split is not known.)
"""

from __future__ import annotations

from dmbot.db import Conn


async def game_time_at(
    conn: Conn, guild_id: int, campaign_id: str, spoken_at: int, group: int | None = None
) -> int | None:
    """The game minute of the clock at real time `spoken_at` (Unix seconds), or None when not
    known: no clock yet, a moment before the first change, or the main clock asked for while the
    party is split."""
    cur = await conn.execute(
        "SELECT position, minute FROM game_clock_log"
        " WHERE guild_id = %s AND campaign_id = %s AND at <= %s"
        + (" AND position = %s" if group is not None else "")
        + " ORDER BY at DESC, seq DESC LIMIT 1",
        (guild_id, campaign_id, spoken_at, *(() if group is None else (group,))),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    if group is None and row["position"] is not None:
        return (
            None  # (a group moved last: the party is split, and the main clock is not the table's)
        )
    return int(row["minute"])
