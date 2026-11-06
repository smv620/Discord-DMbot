"""The game time of a moment, from the game clock's log (#3091, part 3). Reads only; the rows are
written by a trigger on the clock tables (migration 0305), so every change to a clock is in it.

A line spoken at real time T has the game minute of the newest log row at or before T: the clock
as it stood when the line was spoken, never as it reads later. Before the first row, or with no
clock, the answer is None, which every reader takes as "time not known".

Which clock: the log knows when the party split and came back together, so the newest row at or
before the moment says which. A main-clock row is the table's clock whatever group is asked for
(the party was together). A group row means the party was split: the asked group's newest row since
the last main-clock row is its clock, and with no group asked for the answer is None, because the
main clock stands still while split and its minute is one the table has left. Which group a speaker
was in comes from the DM's placements (`group_at`, `group_of_speakers`, #3131); a speaker nobody
placed is not known.
"""

from __future__ import annotations

from collections.abc import Sequence

from dmbot.db import Conn


async def game_time_at(
    conn: Conn, guild_id: int, campaign_id: str, spoken_at: int, group: int | None = None
) -> int | None:
    """The game minute of the clock at real time `spoken_at` (Unix seconds), or None when not
    known: no clock yet, a moment before the first change, the party split with no group given,
    or a group that was not there."""
    cur = await conn.execute(
        "SELECT seq, position, minute FROM game_clock_log"
        " WHERE guild_id = %s AND campaign_id = %s AND at <= %s"
        " ORDER BY at DESC, seq DESC LIMIT 1",
        (guild_id, campaign_id, spoken_at),
    )
    newest = await cur.fetchone()
    if newest is None:
        return None
    if newest["position"] is None:
        return int(newest["minute"])  # together: the one clock, whatever group was asked for
    if group is None:
        return None  # split, and the main clock is not the table's
    cur = await conn.execute(
        "SELECT minute FROM game_clock_log WHERE guild_id = %s AND campaign_id = %s"
        " AND at <= %s AND position = %s AND seq > COALESCE("
        "  (SELECT max(m.seq) FROM game_clock_log m WHERE m.guild_id = %s AND m.campaign_id = %s"
        "   AND m.position IS NULL AND m.at <= %s), 0)"
        " ORDER BY at DESC, seq DESC LIMIT 1",
        (guild_id, campaign_id, spoken_at, group, guild_id, campaign_id, spoken_at),
    )
    row = await cur.fetchone()
    return None if row is None else int(row["minute"])


async def group_at(
    conn: Conn, guild_id: int, campaign_id: str, character_id: str, at: int
) -> int | None:
    """The split-party group this player character was in at real time `at`, or None when they
    were in none or had not been placed (#3131): their newest row at or before it."""
    cur = await conn.execute(
        "SELECT position FROM game_clock_group_log WHERE guild_id = %s AND campaign_id = %s"
        " AND character_id = %s AND at <= %s ORDER BY at DESC, seq DESC LIMIT 1",
        (guild_id, campaign_id, character_id, at),
    )
    row = await cur.fetchone()
    return None if row is None or row["position"] is None else int(row["position"])


async def group_of_speakers(
    conn: Conn, guild_id: int, campaign_id: str, user_ids: Sequence[int], spoken_at: int
) -> int | None:
    """The group a line's speaker(s) were in when it was spoken, or None when that cannot be told.
    A line carries a person, never a character, so this looks at the kept player characters those
    people play: only the ones that were in a group at that moment count. Exactly one group among
    them gives it; characters who were not placed (or were in no group) are ignored, and a speaker
    who plays none here (the DM, say) is ignored too. Two or more different groups, or none at all,
    are not known: DMbot never guesses whose voice it was. Outside a split nothing is placed and the
    answer is None, which `game_time_at` does not need (the party was together)."""
    groups: set[int] = set()
    for user_id in dict.fromkeys(user_ids):
        cur = await conn.execute(
            "SELECT id FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
            " AND type = 'character' AND role = 'player_character' AND status = 'confirmed'"
            " AND played_by = %s ORDER BY id",
            (guild_id, campaign_id, user_id),
        )
        for row in await cur.fetchall():
            placed = await group_at(conn, guild_id, campaign_id, str(row["id"]), spoken_at)
            if placed is not None:
                groups.add(placed)
    return next(iter(groups)) if len(groups) == 1 else None
