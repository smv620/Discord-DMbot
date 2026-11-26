"""A lease on one kind of background work, shared by every process (#3842).

"One recap's audio at a time" used to be an `asyncio.Lock`, which only holds inside one process.
With a worker beside the bot there can be two processes, so the lock is a row (`worker_leases`):
claimed with one conditional upsert (free, expired, or the caller's own token), renewed while the
work runs, and given back at the end. A holder that dies stops renewing, and the next claim after
`leased_until` takes over, so nothing waits on a dead process for longer than the lease. Each
`hold()` makes its own random token, so two tasks in one process exclude each other as well.
Nothing about a session is stored: a kind word, a token and a time.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import AsyncIterator, Callable

from dmbot.db import Database

log = logging.getLogger(__name__)


class WorkerLease:
    """Times come from each process's own clock; processes on one server agree to well within the
    lease (minutes), so a database clock is not worth the cost of testing with it."""

    def __init__(
        self,
        db: Database,
        kind: str,
        *,
        ttl_s: int,
        clock: Callable[[], float] = time.time,
        renew_every_s: float | None = None,
    ) -> None:
        self._db = db
        self._kind = kind
        self._ttl = ttl_s
        self._now = clock
        self._every = renew_every_s if renew_every_s is not None else max(1.0, ttl_s / 3)

    async def acquire(self, holder: str) -> bool:
        """Take the lease if it is free, expired or already this holder's."""
        now = int(self._now())
        async with self._db.unscoped() as conn:
            cur = await conn.execute(
                "INSERT INTO worker_leases (kind, holder, leased_until) VALUES (%s, %s, %s)"
                " ON CONFLICT (kind) DO UPDATE SET holder = EXCLUDED.holder,"
                " leased_until = EXCLUDED.leased_until"
                " WHERE worker_leases.leased_until <= %s OR worker_leases.holder = EXCLUDED.holder",
                (self._kind, holder, now + self._ttl, now),
            )
            return bool(cur.rowcount)

    async def renew(self, holder: str) -> bool:
        """Push the end out, only if the lease is still this holder's."""
        async with self._db.unscoped() as conn:
            cur = await conn.execute(
                "UPDATE worker_leases SET leased_until = %s WHERE kind = %s AND holder = %s",
                (int(self._now()) + self._ttl, self._kind, holder),
            )
            return bool(cur.rowcount)

    async def release(self, holder: str) -> None:
        """Give the lease back at once (only this holder's own)."""
        async with self._db.unscoped() as conn:
            await conn.execute(
                "UPDATE worker_leases SET leased_until = 0 WHERE kind = %s AND holder = %s",
                (self._kind, holder),
            )

    async def _keep_renewing(self, holder: str) -> None:
        """Renew every third of the lease, so one failed beat still leaves two thirds of the time.
        A lost lease is logged and the work goes on: the recap's own lease and the job's deadline
        end it before another recap's audio could be harmed for long."""
        while True:
            await asyncio.sleep(self._every)
            try:
                if not await self.renew(holder):
                    log.warning("Worker lease (%s): lost while working", self._kind)
                    return
            except Exception as exc:  # (a hiccup: the next beat tries again, the ttl is long)
                log.warning("Worker lease (%s): renew failed (%s)", self._kind, type(exc).__name__)

    @contextlib.asynccontextmanager
    async def hold(self) -> AsyncIterator[bool]:
        """`async with lease.hold() as got:` - got is False when another holder has it. While
        inside, the lease is renewed in the background; on the way out it is given back."""
        holder = secrets.token_hex(16)
        beat: asyncio.Task[None] | None = None
        try:
            # (inside the try: a cancel after the upsert commits must still give the lease back)
            if not await self.acquire(holder):
                yield False
                return
            beat = asyncio.create_task(self._keep_renewing(holder))
            yield True
        finally:
            if beat is not None:
                beat.cancel()
                # (gather, not a suppressed await: a cancel aimed at this task still gets through)
                await asyncio.gather(beat, return_exceptions=True)
            try:
                await asyncio.shield(self.release(holder))
            except Exception as exc:  # (it lapses by itself at `leased_until`)
                log.warning(
                    "Worker lease (%s): release failed (%s)", self._kind, type(exc).__name__
                )


class Slots:
    """A few leases of one kind, so at most `n` jobs of that kind run at once across every process
    (#3845: recaps three at a time). A kind's slots are `<kind>_a`, `<kind>_b`, ... (a lease's kind
    is letters and '_' only, so at most 26). A slot lost while its job runs (its renewals failed
    for the whole lease) is only logged: the job's own lease bounds how long the cap can be
    exceeded."""

    def __init__(
        self,
        db: Database,
        kind: str,
        n: int,
        *,
        ttl_s: int,
        clock: Callable[[], float] = time.time,
        renew_every_s: float | None = None,
    ) -> None:
        if not 1 <= n <= 26:
            raise ValueError("a kind has 1 to 26 slots")
        self._leases = []
        for i in range(n):
            name = f"{kind}_{chr(ord('a') + i)}"
            self._leases.append(
                WorkerLease(db, name, ttl_s=ttl_s, clock=clock, renew_every_s=renew_every_s)
            )

    @contextlib.asynccontextmanager
    async def hold(self) -> AsyncIterator[bool]:
        """`async with slots.hold() as got:` - got is False when every slot is taken."""
        async with contextlib.AsyncExitStack() as stack:
            for lease in self._leases:
                if await stack.enter_async_context(lease.hold()):
                    yield True
                    return
            yield False
