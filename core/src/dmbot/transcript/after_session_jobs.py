"""The queue that hands a live session's slow after-session reads to the worker (#4905 slice 4a).

At the end of a live session the bot has about sixty steps to run. A few of them are slow AI reads
that need only ids (the story read and what depends on it, then importance and the story threads).
With `DMBOT_WORKER` on, the bot writes one row for each of the two blocks and the worker takes it.
A row holds ids, times, a lease and a state, never a word of the session. The lease is how a block
is never run twice at once: the worker, or the bot's own fallback when no worker takes the row in
time, takes it with `claim` and keeps it with `renew`; one nobody renewed for `STALE_S` can be
taken again, so a restart carries on.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection
from dataclasses import dataclass

from dmbot.db import Database

READS = "reads"  # the story read, where the story is, scenes, who is this, grades, the report
LATE = "late"  # importance and the story threads
BLOCKS = (READS, LATE)
STALE_S = 15 * 60  # a lease not renewed for this long is dead: one AI call takes minutes at most
WAITING, RUNNING, DONE, FAILED = "waiting", "running", "done", "failed"


@dataclass(frozen=True, slots=True)
class Job:
    guild_id: int
    campaign_id: str
    session_id: str
    block: str
    dm_ids: tuple[int, ...]
    state: str


def _job(row: dict[str, object]) -> Job:
    return Job(
        int(row["guild_id"]),  # type: ignore[call-overload]
        str(row["campaign_id"]),
        str(row["session_id"]),
        str(row["block"]),
        tuple(int(x) for x in row["dm_ids"]),  # type: ignore[attr-defined]
        str(row["state"]),
    )


class AfterSessionJobs:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def add(
        self, guild_id: int, campaign_id: str, session_id: str, block: str, dm_ids: Collection[int]
    ) -> bool:
        """Hand a block over. One row for each session and block: a second call (a restart that
        ends the session again) changes nothing. False when the campaign is gone."""
        if block not in BLOCKS:
            raise ValueError(block)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO after_session_jobs"
                " (guild_id, campaign_id, session_id, block, dm_ids, created_at)"
                " SELECT %s, c.id, %s, %s, %s, %s FROM campaigns c"
                " WHERE c.guild_id = %s AND c.id = %s"
                " ON CONFLICT DO NOTHING",
                (
                    guild_id,
                    session_id,
                    block,
                    sorted(set(dm_ids)),
                    int(self._now()),
                    guild_id,
                    campaign_id,
                ),
            )
            if cur.rowcount:
                return True
            cur = await conn.execute(
                "SELECT 1 FROM after_session_jobs WHERE guild_id = %s AND campaign_id = %s"
                " AND session_id = %s AND block = %s",
                (guild_id, campaign_id, session_id, block),
            )
            return await cur.fetchone() is not None

    async def claim(
        self, guild_id: int, campaign_id: str, session_id: str, block: str, who: str
    ) -> bool:
        """Take the block to run it: it must be waiting, or running on a lease nobody has renewed
        for `STALE_S`. False when someone else holds it or it is over."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE after_session_jobs SET state = 'running', claimed_by = %s, lease_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND session_id = %s AND block = %s"
                " AND (state = 'waiting' OR (state = 'running' AND lease_at < %s))",
                (who[:40], now, guild_id, campaign_id, session_id, block, now - STALE_S),
            )
            return bool(cur.rowcount)

    async def renew(
        self, guild_id: int, campaign_id: str, session_id: str, block: str, who: str
    ) -> bool:
        """Keep the lease while slow work goes on. False when it is no longer this holder's."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE after_session_jobs SET lease_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND session_id = %s AND block = %s"
                " AND state = 'running' AND claimed_by = %s",
                (int(self._now()), guild_id, campaign_id, session_id, block, who[:40]),
            )
            return bool(cur.rowcount)

    async def release(
        self, guild_id: int, campaign_id: str, session_id: str, block: str, who: str
    ) -> bool:
        """Give a block back, unfinished, so the next round takes it again: for a holder that was
        stopped (a deploy) before it was done. Nothing happens if the lease is no longer its."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE after_session_jobs SET state = 'waiting', claimed_by = '', lease_at = 0"
                " WHERE guild_id = %s AND campaign_id = %s AND session_id = %s AND block = %s"
                " AND state = 'running' AND claimed_by = %s",
                (guild_id, campaign_id, session_id, block, who[:40]),
            )
            return bool(cur.rowcount)

    async def finish(
        self,
        guild_id: int,
        campaign_id: str,
        session_id: str,
        block: str,
        who: str,
        *,
        ok: bool,
    ) -> bool:
        """End a block this holder runs: done, or failed (the bot goes on without it)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE after_session_jobs SET state = %s, finished_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND session_id = %s AND block = %s"
                " AND state = 'running' AND claimed_by = %s",
                (
                    DONE if ok else FAILED,
                    int(self._now()),
                    guild_id,
                    campaign_id,
                    session_id,
                    block,
                    who[:40],
                ),
            )
            return bool(cur.rowcount)

    async def state(
        self, guild_id: int, campaign_id: str, session_id: str, block: str
    ) -> str | None:
        """Where a block is, or None when it was never handed over."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT state FROM after_session_jobs WHERE guild_id = %s AND campaign_id = %s"
                " AND session_id = %s AND block = %s",
                (guild_id, campaign_id, session_id, block),
            )
            row = await cur.fetchone()
        return None if row is None else str(row["state"])

    async def open(self, guild_id: int, limit: int = 10) -> list[Job]:
        """The blocks a worker may take now: waiting, or running on a dead lease. Oldest first."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT guild_id, campaign_id, session_id, block, dm_ids, state"
                " FROM after_session_jobs WHERE state = 'waiting'"
                " OR (state = 'running' AND lease_at < %s)"
                " ORDER BY created_at, session_id, block LIMIT %s",
                (int(self._now()) - STALE_S, limit),
            )
            return [_job(r) for r in await cur.fetchall()]
