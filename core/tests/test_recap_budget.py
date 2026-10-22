"""A recap's beat sheet is never cut off at a fixed limit (#2341): a cut-off answer is asked for
again with twice the room, a failure names its check and does not use up the DM's Try again."""

from __future__ import annotations

from dmbot.ai import Reply
from dmbot.recaps import beats as bt
from dmbot.recaps import pipeline as pl
from tests import recap_fixtures as fx
from tests.test_recap_jobs import WorkerHarness


class TheBeatRoom(WorkerHarness):
    def test_the_room_holds_a_full_window_and_doubles_on_a_retry(self) -> None:
        self.assertGreaterEqual(bt.room(0), bt.WINDOW_BEATS * bt.BEAT_TOKENS)
        self.assertEqual(bt.room(1), 2 * bt.room(0))

    async def test_a_beat_sheet_cut_off_is_asked_for_again_with_twice_the_room(self) -> None:
        cut = Reply(fx.BEATS_ANSWER[:100], True, 10, 10, "m")
        await self.setUpWorker(
            fx.Canned(cut, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(fx.BEATS_ANSWER),
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertEqual(self.fast.tokens[0], bt.room(0))
        self.assertEqual(self.careful.tokens[0], 2 * self.fast.tokens[0])

    async def test_two_cut_off_answers_fail_plainly_name_the_check_and_keep_the_try(self) -> None:
        cut = Reply(fx.BEATS_ANSWER[:100], True, 10, 10, "m")
        await self.setUpWorker(fx.Canned(cut), fx.Canned(cut))
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET tries = 1")
        job = await self.go()
        self.assertEqual(job.state, "failed")
        self.assertIn(pl.TOO_LONG, job.last_error)
        self.assertIn("(check: cut off)", job.last_error)
        self.assertEqual(job.tries, 0)  # our failure, not the DM's try

    async def test_no_one_speaking_still_costs_the_try(self) -> None:
        async def nobody() -> list[int]:
            return []

        await self.setUpWorker(fx.Canned(), fx.Canned(), consenting=nobody)
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET tries = 1")
        job = await self.go()
        self.assertEqual((job.state, job.last_error, job.tries), ("failed", pl.NOTHING, 1))


class TheRequeue(WorkerHarness):
    async def test_a_failed_recap_starts_over_with_nothing_spent(self) -> None:
        from dmbot.devtools import recap_requeue

        cut = Reply(fx.BEATS_ANSWER[:100], True, 10, 10, "m")
        await self.setUpWorker(fx.Canned(cut), fx.Canned(cut))
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET tries = 2")
        self.assertEqual((await self.go()).state, "failed")
        self.assertTrue(await self.store.requeue(1, self.one.id, 100))
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(
            (job.state, job.attempt, job.tries, job.last_error, job.calls), ("queued", 0, 0, "", 0)
        )
        self.assertFalse(await self.store.requeue(1, self.one.id, 999))
        self.assertEqual(recap_requeue.parse_args(["1", "c", "100"]).session_at, 100)


class TheGiveBack(WorkerHarness):
    async def tries_after(self, **fail: object) -> int:
        await self.setUpWorker(fx.Canned(), fx.Canned())
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET tries = 2")
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        await self.worker._fail(1, self.one.id, job, pl.SERVICE, **fail)  # type: ignore[arg-type]
        after = await self.store.get(1, self.one.id, 100)
        assert after is not None
        return after.tries

    async def test_a_failure_of_ours_gives_one_try_back(self) -> None:
        self.assertEqual(await self.tries_after(ours=True), 1)

    async def test_a_limit_or_the_allowance_keeps_the_try(self) -> None:
        self.assertEqual(await self.tries_after(), 2)  # (the cap, the day's limit, the allowance)

    async def test_failing_twice_gives_back_only_once(self) -> None:
        self.assertEqual(await self.tries_after(ours=True), 1)
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        await self.worker._fail(1, self.one.id, job, pl.SERVICE, ours=True)
        again = await self.store.get(1, self.one.id, 100)
        assert again is not None
        self.assertEqual(again.tries, 1)

    async def test_a_good_recap_is_never_started_over(self) -> None:
        await self.setUpWorker(fx.Canned(), fx.Canned())
        self.assertFalse(await self.store.requeue(1, self.one.id, 100))  # still queued, not failed
