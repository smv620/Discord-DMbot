"""The DM-screen notice for a peek at the relationship web (#2404): "<name> peeked at the
relationship web", posted once for each visit.

A player who turns the peek on (after the page's warning) asks the website's peek door, which
records the visit in `web_peeks` (only when the campaign's DM screen lets players peek). The bot
posts one line for each new row on the campaign's DM screen and marks it posted, so a restart never
posts it twice. The name is the person's own display name as the website kept it; it is made plain
text here (no mention can ping anyone). A row is deleted a day after it was posted.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import discord

from dmbot.db import Database

KEEP_FOR_S = 86_400  # a posted row is kept a day (the sweep), then gone
PER_ROUND = 10  # notices taken in one round for one server


@dataclass(frozen=True, slots=True)
class Peek:
    campaign_id: str
    name: str


def notice(name: str) -> str:
    """The line for the DM screen. A name with markdown or a mention in it does neither."""
    plain = discord.utils.escape_markdown(discord.utils.escape_mentions(name))
    return f"👀 {plain} peeked at the relationship web."


class PeekNotices:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def take(self, guild_id: int) -> list[Peek]:
        """The notices not yet posted, marked posted as they are taken (once, even if two rounds
        overlap). If the post then fails the notice is lost, which is better than posting twice."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_peeks SET posted_at = %s WHERE (guild_id, campaign_id, user_id, visit)"
                " IN (SELECT guild_id, campaign_id, user_id, visit FROM web_peeks"
                "  WHERE guild_id = %s AND posted_at IS NULL ORDER BY created_at LIMIT %s"
                "  FOR UPDATE SKIP LOCKED) RETURNING campaign_id, name, created_at",
                (int(self._now()), guild_id, PER_ROUND),
            )
            rows = sorted(await cur.fetchall(), key=lambda r: r["created_at"])
        return [Peek(str(r["campaign_id"]), str(r["name"])) for r in rows]

    async def sweep(self, guild_id: int) -> int:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM web_peeks WHERE guild_id = %s AND posted_at IS NOT NULL"
                " AND posted_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)
