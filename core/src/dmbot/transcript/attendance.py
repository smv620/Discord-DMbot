"""Who took part in a stored session (#3036): the people whose lines it holds, for "The party".

Only user ids are read, never a line's text. A session's speakers are the people who sat at the
table, so this is the one fact the party rule needs from the transcript. Everything is scoped to
one server and one session, as every transcript read is."""

from __future__ import annotations

from dmbot.db import Database


async def session_speakers(db: Database, guild_id: int, session_id: str) -> set[int]:
    """The distinct people who have a line in this stored session, for this server only."""
    async with db.guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT DISTINCT user_id FROM transcript_lines WHERE guild_id = %s AND session_id = %s",
            (guild_id, session_id),
        )
        return {int(r["user_id"]) for r in await cur.fetchall()}
