"""Answer the website's "who is this Discord ID?" (#4225, point 5).

A person picker takes a typed id; the page queues a row in `member_lookups` and the bot looks the
member up in that server with `guild.fetch_member` (a plain request, no privileged intent) and
writes the name for the asker's page to show: "Eric (eric_92)", the display name with the username
after it when they differ. Not in the server, or a bot account, is `not_here`. Nothing is written
into a campaign here; the row holds the name only until the page has read it (rows go after a day).
Counts only in the log, never a name or an id.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import discord

from dmbot.db import Database
from dmbot.member_names import shown_name

log = logging.getLogger(__name__)

PER_ROUND = 3  # questions answered in a server each round
GIVE_UP_S = 600  # the door reads a question older than this as lost
RETAKE_AFTER_S = 120  # a claim that stopped moving this long ago is taken again


class MemberLookups:
    def __init__(self, db: Database, *, clock: Any = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim(self, guild_id: int) -> list[tuple[str, int, int]]:
        """The oldest questions waiting (or taken and dropped), marked as being worked on:
        (id, the id asked about, the claim stamp)."""
        now_ms = int(self._now() * 1000)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE member_lookups SET state = 'working', claimed_at = %s"
                " WHERE (guild_id, id) IN (SELECT guild_id, id FROM member_lookups"
                " WHERE guild_id = %s AND created_at >= %s AND (state = 'waiting'"
                " OR (state = 'working' AND claimed_at < %s))"
                " ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED) RETURNING id, target",
                (now_ms, guild_id, now_ms // 1000 - GIVE_UP_S, now_ms - RETAKE_AFTER_S * 1000,
                 PER_ROUND),
            )  # fmt: skip
            return [(str(r["id"]), int(r["target"]), now_ms) for r in await cur.fetchall()]

    async def answer(
        self, guild_id: int, lookup_id: str, stamp: int, *, outcome: str, shown: str = ""
    ) -> bool:
        """Write the answer, only if this worker still holds the question."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE member_lookups SET state = 'done', outcome = %s, shown = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (outcome, shown[:220], guild_id, lookup_id, stamp),
            )
            return cur.rowcount == 1

    async def fail(self, guild_id: int, lookup_id: str, stamp: int) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE member_lookups SET state = 'failed' WHERE guild_id = %s AND id = %s"
                " AND state = 'working' AND claimed_at = %s",
                (guild_id, lookup_id, stamp),
            )

    async def waiting(self, guild_id: int) -> bool:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM member_lookups WHERE guild_id = %s AND state IN ('waiting',"
                " 'working') LIMIT 1",
                (guild_id,),
            )
            return await cur.fetchone() is not None


async def run(bot: Any, guild_id: int) -> int:
    """One round for one server: up to `PER_ROUND` questions answered. Returns how many."""
    guild = bot.get_guild(guild_id)
    if guild is None:
        return 0
    lookups = MemberLookups(bot.campaigns.db)
    if not await lookups.waiting(guild_id):
        return 0  # (the common case: one cheap read)
    found = notfound = failed = 0
    for lookup_id, target, stamp in await lookups.claim(guild_id):
        try:
            member = guild.get_member(target) or await guild.fetch_member(target)
        except discord.NotFound:
            await lookups.answer(guild_id, lookup_id, stamp, outcome="not_here")
            notfound += 1
            continue
        except Exception as exc:  # Discord could not say: the page is told, never left spinning
            await lookups.fail(guild_id, lookup_id, stamp)
            failed += 1
            log.info("A member lookup failed (%s)", type(exc).__name__)
            continue
        if getattr(member, "bot", False):
            await lookups.answer(guild_id, lookup_id, stamp, outcome="not_here")  # (no person)
            notfound += 1
            continue
        name = shown_name(member.display_name, member.name)
        await lookups.answer(guild_id, lookup_id, stamp, outcome="found", shown=name)
        found += 1
    log.info("Member lookups: %d found, %d not here, %d failed", found, notfound, failed)
    return found + notfound + failed
