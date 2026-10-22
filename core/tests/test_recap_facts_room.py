"""The fact check's output room (#2345): sized for the recap and asked again, with twice the room,
when the answer is cut off, so a long recap is not shipped with its check silently skipped."""

from __future__ import annotations

from dmbot.ai import Reply
from dmbot.recaps import pipeline as pl
from tests import recap_fixtures as fx
from tests.test_recap_jobs import WorkerHarness


class TheFactsRoom(WorkerHarness):
    def test_the_room_grows_with_the_recap_and_never_shrinks_below_the_old_one(self) -> None:
        self.assertEqual(pl.facts_room(3), pl.FACTS_TOKENS)
        self.assertGreater(pl.facts_room(60), pl.FACTS_TOKENS)

    async def test_a_cut_off_fact_check_is_asked_again_with_twice_the_room(self) -> None:
        cut = Reply("{", True, 10, 10, "m")
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
        )
        self.careful.facts = [cut, fx.CLEAN_FACTS]
        job = await self.go()
        self.assertEqual(job.state, "ready")
        first, second = self.careful.fact_tokens[:2]
        self.assertEqual(second, 2 * first)
