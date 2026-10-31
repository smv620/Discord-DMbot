"""The game clock's log (#3091, part 3): what game time a moment of real time had. Reads only;
the rows are written by a trigger on the clock tables (migration 0305), so every change to a clock
is in it.

A line spoken at real time T has the game minute of the newest log row at or before T: the clock
as it stood when the line was spoken, never as it reads later. Before the first row, or with no
clock, the answer is None, which every reader takes as "time not known". While the party is split
the newest row can belong to any group (a line does not say which group its speaker was in), which
is the clock the buttons were last used on.
"""

from __future__ import annotations

from dmbot.db import Conn, Database


async def minute_at(conn: Conn, guild_id: int, campaign_id: str, at: int) -> int | None:
    """The game minute of the clock at real time `at` (Unix seconds), or None when not known."""
    cur = await conn.execute(
        "SELECT minute FROM game_clock_log WHERE guild_id = %s AND campaign_id = %s AND at <= %s"
        " ORDER BY at DESC, seq DESC LIMIT 1",
        (guild_id, campaign_id, at),
    )
    row = await cur.fetchone()
    return None if row is None else int(row["minute"])


class ClockLog:
    """`minute_at` for a caller that holds a database, not a connection."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def minute_at(self, guild_id: int, campaign_id: str, at: int) -> int | None:
        async with self._db.guild(guild_id) as conn:
            return await minute_at(conn, guild_id, campaign_id, at)
