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
KEEP_S = 3600  # an answer is read in seconds: a row an hour old is gone, name and all
SWEEP_EVERY_S = 3600.0
_swept: dict[int, float] = {}  # when each server's old rows were last cleared


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
        """Is there a question `claim` would take now (the same test, so an idle server costs one
        cheap read and no claim)."""
        now_ms = int(self._now() * 1000)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM member_lookups WHERE guild_id = %s AND created_at >= %s"
                " AND (state = 'waiting' OR (state = 'working' AND claimed_at < %s)) LIMIT 1",
                (guild_id, now_ms // 1000 - GIVE_UP_S, now_ms - RETAKE_AFTER_S * 1000),
            )
            return await cur.fetchone() is not None

    async def sweep(self, guild_id: int) -> int:
        """Remove rows older than `KEEP_S`, whatever their state: the name goes with them."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM member_lookups WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_S),
            )
            return cur.rowcount


async def run(bot: Any, guild_id: int) -> int:
    """One round for one server: up to `PER_ROUND` questions answered. Returns how many."""
    guild = bot.get_guild(guild_id)
    if guild is None:
        return 0
    lookups = MemberLookups(bot.campaigns.db)
    stamp = time.monotonic()
    if stamp - _swept.get(guild_id, float("-inf")) >= SWEEP_EVERY_S:
        _swept[guild_id] = stamp
        await lookups.sweep(guild_id)
    if not await lookups.waiting(guild_id):
        return 0  # (the common case: one cheap read)
    found = notfound = failed = 0
    for lookup_id, target, claim in await lookups.claim(guild_id):
        try:
            member = guild.get_member(target) or await guild.fetch_member(target)
            if getattr(member, "bot", False):  # (no person)
                outcome, name = "not_here", ""
            else:
                outcome, name = "found", shown_name(member.display_name, member.name)
        except discord.NotFound:
            outcome, name = "not_here", ""
        except Exception as exc:  # Discord could not say: the page is told, never left spinning
            failed += 1
            log.info("A member lookup failed (%s)", type(exc).__name__)
            await _settle(lookups.fail(guild_id, lookup_id, claim))
            continue
        if outcome == "found":
            found += 1
        else:
            notfound += 1
        await _settle(lookups.answer(guild_id, lookup_id, claim, outcome=outcome, shown=name))
    total = found + notfound + failed
    if total:
        log.info("Member lookups: %d found, %d not here, %d failed", found, notfound, failed)
    return total


async def _settle(write: Any) -> None:
    """One question's answer is written on its own: a database trouble loses that one (it is taken
    again after `RETAKE_AFTER_S`), never the others in the round."""
    try:
        await write
    except Exception as exc:
        log.warning("A member lookup answer could not be written (%s)", type(exc).__name__)
