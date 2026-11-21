"""The lease on a kind of background work (#3842): one holder at a time across processes, an
expired lease taken over, a renewed one kept, and the audio worker making nothing while another
process holds the audio lease. Database tests: skipped where there is no Postgres."""

from __future__ import annotations

import asyncio
from typing import Any

from dmbot.worker_lease import Slots, WorkerLease
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

    async def until_of(self) -> int:
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT leased_until FROM worker_leases WHERE kind = 'audio'")
            row = await cur.fetchone()
            assert row is not None
            return int(row["leased_until"])

    async def test_the_heartbeat_pushes_the_end_out_while_it_is_held(self) -> None:
        clock = Clock()
        lease = self.lease(clock)
        async with lease.hold():
            before = await self.until_of()
            clock.now += 30
            await asyncio.sleep(0.1)
            self.assertGreater(await self.until_of(), before)

    async def test_the_same_holder_can_take_it_again(self) -> None:
        clock = Clock()
        lease = self.lease(clock)
        self.assertTrue(await lease.acquire("one"))
        self.assertTrue(await lease.acquire("one"))

    async def test_a_lost_lease_ends_the_heartbeat_without_hurting_the_work(self) -> None:
        clock = Clock()
        lease = self.lease(clock)
        async with lease.hold() as got:
            self.assertTrue(got)
            clock.now += 61  # (past its end before a beat ran)
            self.assertTrue(await self.lease(clock).acquire("taker"))
            await asyncio.sleep(0.1)  # (the beat finds it lost and stops; the body goes on)
        self.assertFalse(await self.lease(clock).acquire("other"))  # (the taker's, not freed)

    async def test_a_cancelled_task_gives_the_lease_back(self) -> None:
        clock = Clock()
        lease = self.lease(clock)
        entered = asyncio.Event()

        async def work() -> None:
            async with lease.hold():
                entered.set()
                await asyncio.sleep(60)

        task = asyncio.create_task(work())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(await lease.acquire("next"))


class TheSlots(DatabaseTest):
    """At most n holders of a kind at once, across processes (#3845)."""

    def slots(self) -> Any:
        return Slots(self.db, "recap_slot", 3, ttl_s=60, clock=Clock(), renew_every_s=0.01)

    async def test_three_hold_at_once_and_a_fourth_finds_none(self) -> None:
        a, b = self.slots(), self.slots()  # (two processes)
        async with a.hold() as one, b.hold() as two, a.hold() as three, b.hold() as four:
            self.assertEqual([one, two, three, four], [True, True, True, False])

    async def test_a_slot_given_back_is_taken_again(self) -> None:
        slots = self.slots()
        async with slots.hold() as one, slots.hold() as two, slots.hold() as three:
            self.assertTrue(one and two and three)
        async with slots.hold() as again:
            self.assertTrue(again)

    async def test_a_slot_is_given_back_when_the_body_raises(self) -> None:
        slots = self.slots()
        with self.assertRaises(RuntimeError):
            async with slots.hold(), slots.hold(), slots.hold():
                raise RuntimeError("boom")
        async with slots.hold() as one, slots.hold() as two, slots.hold() as three:
            self.assertTrue(one and two and three)

    def test_a_kind_has_one_to_twenty_six_slots(self) -> None:
        for bad in (0, 27):
            with self.assertRaises(ValueError):
                Slots(object(), "recap_slot", bad, ttl_s=60)  # type: ignore[arg-type]
