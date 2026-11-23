"""The queue that hands a live session's slow after-session reads to the worker (#4905 4a): one row
for each block, taken by one holder at a time, a dead lease taken again, one server never sees
another's. Real Postgres; invented ids."""

from __future__ import annotations

from dmbot.transcript import after_session_jobs as jobs
from tests.test_memory_store import GUILD_A, GUILD_B, MemoryTest

SESSION = "s" * 8


class TheQueue(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.clock = [1_700_000_000.0]
        self.queue = jobs.AfterSessionJobs(self.db, clock=lambda: self.clock[0])

    async def hand(self, block: str = jobs.READS) -> bool:
        return await self.queue.add(GUILD_A, self.c, SESSION, block, {7, 3})

    async def test_a_block_is_handed_over_once_and_waits(self) -> None:
        self.assertTrue(await self.hand())
        self.assertTrue(await self.hand())  # (a restart that ends the session again: no second row)
        self.assertEqual(await self.queue.state(GUILD_A, self.c, SESSION, jobs.READS), "waiting")
        (job,) = await self.queue.open(GUILD_A)
        self.assertEqual((job.session_id, job.block, job.dm_ids), (SESSION, "reads", (3, 7)))
        self.assertIsNone(await self.queue.state(GUILD_A, self.c, SESSION, jobs.LATE))

    async def test_only_one_holder_takes_it_and_only_it_can_end_it(self) -> None:
        await self.hand()
        self.assertTrue(await self.queue.claim(GUILD_A, self.c, SESSION, "reads", "worker"))
        self.assertFalse(await self.queue.claim(GUILD_A, self.c, SESSION, "reads", "bot"))
        self.assertEqual(await self.queue.open(GUILD_A), [])  # (running on a live lease)
        self.assertFalse(await self.queue.renew(GUILD_A, self.c, SESSION, "reads", "bot"))
        self.assertFalse(await self.queue.finish(GUILD_A, self.c, SESSION, "reads", "bot", ok=True))
        self.assertTrue(
            await self.queue.finish(GUILD_A, self.c, SESSION, "reads", "worker", ok=True)
        )
        self.assertEqual(await self.queue.state(GUILD_A, self.c, SESSION, "reads"), "done")
        self.assertFalse(await self.queue.claim(GUILD_A, self.c, SESSION, "reads", "bot"))

    async def test_a_dead_lease_is_taken_again_and_a_renewed_one_is_not(self) -> None:
        await self.hand()
        await self.queue.claim(GUILD_A, self.c, SESSION, "reads", "worker")
        self.clock[0] += jobs.STALE_S - 10
        self.assertTrue(await self.queue.renew(GUILD_A, self.c, SESSION, "reads", "worker"))
        self.clock[0] += jobs.STALE_S - 10
        self.assertFalse(await self.queue.claim(GUILD_A, self.c, SESSION, "reads", "bot"))
        self.clock[0] += 20  # (not renewed since)
        self.assertEqual([j.block for j in await self.queue.open(GUILD_A)], ["reads"])
        self.assertTrue(await self.queue.claim(GUILD_A, self.c, SESSION, "reads", "bot"))
        # the old holder's late finish does nothing
        self.assertFalse(
            await self.queue.finish(GUILD_A, self.c, SESSION, "reads", "worker", ok=True)
        )

    async def test_a_running_block_says_what_it_is_on_and_only_its_holder_can(self) -> None:
        await self.hand()
        self.assertEqual(
            await self.queue.status(GUILD_A, self.c, SESSION, "reads"), jobs.Status("waiting")
        )
        await self.queue.claim(GUILD_A, self.c, SESSION, "reads", "worker-1")
        self.assertFalse(
            await self.queue.step(GUILD_A, self.c, SESSION, "reads", "worker-2", jobs.STORY, 1, 4)
        )
        self.assertTrue(
            await self.queue.step(GUILD_A, self.c, SESSION, "reads", "worker-1", jobs.STORY, 2, 4)
        )
        self.assertEqual(
            await self.queue.status(GUILD_A, self.c, SESSION, "reads"),
            jobs.Status("running", jobs.STORY, 2, 4),
        )
        await self.queue.finish(GUILD_A, self.c, SESSION, "reads", "worker-1", ok=True)
        self.assertEqual(
            await self.queue.status(GUILD_A, self.c, SESSION, "reads"), jobs.Status("done")
        )
        self.assertIsNone(await self.queue.status(GUILD_A, self.c, SESSION, "late"))

    async def test_a_block_given_back_waits_again_and_only_its_holder_can_give_it(self) -> None:
        await self.hand()
        await self.queue.claim(GUILD_A, self.c, SESSION, "reads", "worker-1")
        self.assertFalse(await self.queue.release(GUILD_A, self.c, SESSION, "reads", "worker-2"))
        self.assertTrue(await self.queue.release(GUILD_A, self.c, SESSION, "reads", "worker-1"))
        self.assertEqual(await self.queue.state(GUILD_A, self.c, SESSION, "reads"), "waiting")
        self.assertTrue(await self.queue.claim(GUILD_A, self.c, SESSION, "reads", "worker-2"))

    async def test_a_failed_block_is_over_and_the_other_block_is_its_own(self) -> None:
        await self.hand()
        await self.hand(jobs.LATE)
        await self.queue.claim(GUILD_A, self.c, SESSION, "reads", "worker")
        await self.queue.finish(GUILD_A, self.c, SESSION, "reads", "worker", ok=False)
        self.assertEqual(await self.queue.state(GUILD_A, self.c, SESSION, "reads"), "failed")
        self.assertEqual([j.block for j in await self.queue.open(GUILD_A)], ["late"])

    async def test_another_server_never_sees_it_and_an_unknown_block_is_refused(self) -> None:
        await self.hand()
        self.assertEqual(await self.queue.open(GUILD_B), [])
        self.assertIsNone(await self.queue.state(GUILD_B, self.c, SESSION, "reads"))
        self.assertFalse(await self.queue.add(GUILD_B, self.c, SESSION, "reads", {1}))
        with self.assertRaises(ValueError):
            await self.queue.add(GUILD_A, self.c, SESSION, "everything", {1})

    async def test_it_goes_with_its_campaign(self) -> None:
        await self.hand()
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "DELETE FROM campaigns WHERE guild_id = %s AND id = %s", (GUILD_A, self.c)
            )
        self.assertEqual(await self.queue.open(GUILD_A), [])
