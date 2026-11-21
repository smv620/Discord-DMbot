"""The lease on a kind of background work (#3842): one holder at a time across processes, an
expired lease taken over, a renewed one kept, and the audio worker making nothing while another
process holds the audio lease. Database tests: skipped where there is no Postgres."""

from __future__ import annotations

import asyncio

from dmbot.worker_lease import WorkerLease
from tests.pg import DatabaseTest


class Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def __call__(self) -> float:
        return self.now


class TheLease(DatabaseTest):
    def lease(self, clock: Clock, kind: str = "audio") -> WorkerLease:
        return WorkerLease(self.db, kind, ttl_s=60, clock=clock, renew_every_s=0.01)

    async def test_two_holders_never_hold_it_at_once(self) -> None:
        clock = Clock()
        a, b = self.lease(clock), self.lease(clock)  # (two processes: one table)
        self.assertTrue(await a.acquire("one"))
        self.assertFalse(await b.acquire("two"))
        await a.release("one")
        self.assertTrue(await b.acquire("two"))

    async def test_an_expired_lease_is_taken_over_and_the_old_holder_cannot_renew(self) -> None:
        clock = Clock()
        lease = self.lease(clock)
        self.assertTrue(await lease.acquire("dead"))
        clock.now += 59
        self.assertFalse(await lease.acquire("new"))  # (still held)
        clock.now += 2
        self.assertTrue(await lease.acquire("new"))
        self.assertFalse(await lease.renew("dead"))

    async def test_a_renewed_lease_is_not_taken(self) -> None:
        clock = Clock()
        lease = self.lease(clock)
        self.assertTrue(await lease.acquire("one"))
        clock.now += 50
        self.assertTrue(await lease.renew("one"))
        clock.now += 50  # (past the first end, inside the renewed one)
        self.assertFalse(await lease.acquire("two"))

    async def test_a_kind_is_its_own_lease(self) -> None:
        clock = Clock()
        self.assertTrue(await self.lease(clock).acquire("one"))
        self.assertTrue(await self.lease(clock, "imports").acquire("two"))

    async def test_hold_excludes_a_second_task_and_frees_the_lease_after(self) -> None:
        clock = Clock()
        lease = self.lease(clock)
        async with lease.hold() as first:
            self.assertTrue(first)
            async with lease.hold() as second:
                self.assertFalse(second)
            await asyncio.sleep(0.05)  # (the background renewal runs while it is held)
        async with lease.hold() as again:
            self.assertTrue(again)

    async def test_a_failed_body_still_gives_the_lease_back(self) -> None:
        clock = Clock()
        lease = self.lease(clock)
        with self.assertRaises(RuntimeError):
            async with lease.hold():
                raise RuntimeError("boom")
        self.assertTrue(await lease.acquire("next"))
