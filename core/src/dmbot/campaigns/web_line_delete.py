"""The bot's side of the website's "Delete my transcripts" (#2780, #2769).

The website's role cannot touch the transcript tables, and an erasure has to hold each running
session's save lock and drop what the person has said and not yet saved, so a request is a row
(`web_line_deletions`) that this module claims one at a time and carries out with exactly what
Discord's 🗑 button runs: `DMBot.delete_my_lines`. There is no second erasure here. The row is
only the person's id, a stage and two counts: never their words.

The row names the only person whose lines may go (the one who asked). A failure deletes nothing
(the erasure is one transaction), and the row ends `failed` so the page can say so in Discord's
words. A person with no lines left (deleted in Discord meanwhile) ends `done` with 0.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from dmbot.db import Database

log = logging.getLogger(__name__)

PER_ROUND = 3  # requests carried out in one round for one server
RETAKE_AFTER_S = 600  # a claim this old belongs to a bot that stopped
KEEP_FOR_S = 86_400  # a request goes a day after it was made

FAILED = "failed"  # anything unexpected; nothing was deleted, asking again is fine


@dataclass(frozen=True, slots=True)
class Request:
    id: str
    user_id: int  # the person asking, and the only person whose lines may go
    claimed_at: int  # this worker's claim: only it may write


class Host(Protocol):
    """What the erasure needs from the running bot, so tests can stand in for it."""

    async def delete_my_lines(self, guild_id: int, user_id: int) -> tuple[int, int]: ...


class WebLineDeletions:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Request | None:
        """The oldest waiting request of this server (or one stalled for ten minutes), marked as
        being worked on. Nothing is deleted for a request that is not marked first."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_line_deletions SET state = 'working', stage = 'deleting',"
                " claimed_at = %s, updated_at = %s"
                " WHERE (guild_id, id) = (SELECT guild_id, id FROM web_line_deletions"
                " WHERE guild_id = %s AND (state = 'waiting' OR (state = 'working'"
                " AND claimed_at < %s)) AND created_at >= %s ORDER BY created_at LIMIT 1"
                " FOR UPDATE SKIP LOCKED)"
                " RETURNING id, user_id",
                (now, now, guild_id, now - RETAKE_AFTER_S, now - KEEP_FOR_S),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Request(row["id"], int(row["user_id"]), now)

    async def done(self, guild_id: int, item: Request, lines: int, sessions: int) -> bool:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_line_deletions SET state = 'done', stage = 'done', lines = %s,"
                " sessions = %s, updated_at = %s WHERE guild_id = %s AND id = %s"
                " AND state = 'working' AND claimed_at = %s",
                (lines, sessions, int(self._now()), guild_id, item.id, item.claimed_at),
            )
            return bool(cur.rowcount)

    async def failed(self, guild_id: int, item: Request, error: str) -> bool:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_line_deletions SET state = 'failed', stage = 'failed', error = %s,"
                " updated_at = %s WHERE guild_id = %s AND id = %s AND state = 'working'"
                " AND claimed_at = %s",
                (error, int(self._now()), guild_id, item.id, item.claimed_at),
            )
            return bool(cur.rowcount)

    async def sweep(self, guild_id: int) -> int:
        """Delete requests older than `KEEP_FOR_S`, whatever their state."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM web_line_deletions WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


async def carry_out(host: Host, guild_id: int, item: Request, store: WebLineDeletions) -> str:
    """Carry out one claimed request and mark it. Returns the outcome word ("" when it worked).
    Never raises. Only counts are logged (by `delete_my_lines`), never the person's id."""
    try:
        lines, sessions = await host.delete_my_lines(guild_id, item.user_id)
        if not await store.done(guild_id, item, lines, sessions):
            return FAILED  # not this worker's row any more
        return ""
    except Exception as exc:  # the type only: an error can quote a row
        log.warning("A website line deletion failed: %s", type(exc).__name__)
        try:
            await store.failed(guild_id, item, FAILED)
        except Exception:
            log.warning("A website line deletion could not be marked failed")
        return FAILED
