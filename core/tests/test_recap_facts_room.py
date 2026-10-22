"""The fact check's output room (#2345): sized for the recap and asked again, with twice the room,
when the answer is cut off, so a long recap is not shipped with its check silently skipped."""

from __future__ import annotations

from typing import Any

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

    async def run_with(self, facts: list[Reply | str], *, ceiling: Any = None) -> list[Any]:
        """Run a recap whose fact check gets `facts`; returns the call events the observer saw."""
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
        )
        self.careful.facts = list(facts)
        events: list[Any] = []
        self.worker._on_call = events.append
        if ceiling is not None:
            self.worker._ceiling = ceiling  # type: ignore[method-assign]
        self.job = await self.go()
        return [e for e in events if e.stage == "facts"]

    async def test_cut_twice_asks_twice_and_the_report_says_cut_off(self) -> None:
        cut = Reply("{", True, 10, 10, "m")
        seen = await self.run_with([cut, cut])
        first, second = self.careful.fact_tokens[:2]
        self.assertEqual(second, 2 * first)
        self.assertEqual([e.limit for e in seen], [second])  # (the call that came back last)
        self.assertEqual(seen[-1].checks, ["cut off"])

    async def test_a_ceiling_reached_before_the_retry_asks_once_and_reports_that_room(self) -> None:
        cut = Reply("{", True, 10, 10, "m")

        def ceiling(job: Any) -> int:  # the first fact call may go, the retry may not
            return 10**6 if not self.careful.fact_tokens else 0

        seen = await self.run_with([cut], ceiling=ceiling)
        self.assertEqual(len(self.careful.fact_tokens), 1)
        self.assertEqual([e.limit for e in seen], [self.careful.fact_tokens[0]])

    async def test_the_retry_counts_as_a_call_and_as_spend(self) -> None:
        cut = Reply("{", True, 10, 10, "m")
        await self.run_with([cut, fx.CLEAN_FACTS])
        made = len(self.fast.sent) + len(self.careful.sent) + len(self.careful.fact_tokens)
        self.assertEqual(len(self.careful.fact_tokens), 2)
        self.assertEqual((self.job.calls, self.job.spent), (made, made))
