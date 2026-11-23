"""The card round decides whether and how much to work (#5050 b3b): no database, no AI."""

from __future__ import annotations

import unittest
from collections.abc import Sequence

from dmbot.memory.card_mentions import Budget
from dmbot.memory.card_runner import CardRunner, Pass


class _Day:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Round(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.day = _Day()
        self.said: list[str] = []
        self.words: dict[str, str] = {}
        self.looked: list[Sequence[str]] = []

    async def who(self, entity_id: str) -> str:
        self.said.append(entity_id)
        return self.words.get(entity_id, "written")

    async def mentions(self, ids: Sequence[str], budget: Budget) -> dict[str, str]:
        self.looked.append(ids)
        word = self.mention_word
        for _ in ids:
            if word != "never" and not budget.take():  # (each scene is one AI call)
                return {i: "scene-later" for i in ids}
        return {i: word for i in ids}

    mention_word = "kept"

    async def go(
        self,
        runner: CardRunner,
        ids: Sequence[str],
        version: int = 1,
        cap: int = 99,
        agreed: int = 0,
    ) -> Pass:
        p = Pass(budget=Budget(cap))
        await runner.work(
            p, 1, "c", version=version, sessions=2, agreed=agreed, entity_ids=ids,
            who=self.who, mentions=self.mentions,
        )  # fmt: skip
        return p

    async def test_an_unchanged_campaign_is_skipped_the_next_pass(self) -> None:
        runner = CardRunner(clock=self.day)
        await self.go(runner, ["a", "b"])
        again = await self.go(runner, ["a", "b"])
        self.assertEqual(again.skipped, 1)
        self.assertEqual(self.said, ["a", "b"])

    async def test_a_new_version_works_the_campaign_again(self) -> None:
        runner = CardRunner(clock=self.day)
        await self.go(runner, ["a"])
        again = await self.go(runner, ["a"], version=2)
        self.assertEqual(again.worked, 1)
        self.assertEqual(self.said, ["a", "a"])

    async def test_the_pass_stops_at_its_cap_and_finishes_next_time(self) -> None:
        runner = CardRunner(clock=self.day)
        first = await self.go(runner, ["a", "b", "c"], cap=2)
        self.assertEqual((first.calls, self.said), (2, ["a", "b"]))
        second = await self.go(runner, ["a", "b", "c"], cap=2)  # (not marked finished)
        self.assertEqual(second.worked, 1)

    async def test_a_failure_waits_before_the_next_try(self) -> None:
        runner = CardRunner(backoff_s=100, clock=self.day)
        self.words["a"] = "failed"
        first = await self.go(runner, ["a"])
        self.assertEqual(first.failed, 1)
        self.day.now = 50
        self.assertEqual((await self.go(runner, ["a"])).skipped, 1)
        self.day.now = 101
        self.words["a"] = "written"
        self.assertEqual((await self.go(runner, ["a"])).worked, 1)

    async def test_words_that_make_no_call_do_not_use_the_cap(self) -> None:
        runner = CardRunner(clock=self.day)
        self.words = {"a": "unchanged", "b": "little"}
        p = await self.go(runner, ["a", "b", "c"], cap=2)
        self.assertEqual((p.calls, self.said), (2, ["a", "b", "c"]))

    async def test_mentions_are_looked_up_in_batches(self) -> None:
        runner = CardRunner(clock=self.day)
        self.words = {str(i): "unchanged" for i in range(45)}
        ids = [str(i) for i in range(45)]
        await self.go(runner, ids)
        self.assertEqual([len(b) for b in self.looked], [20, 20, 5])

    async def test_the_scenes_count_against_the_cap_too(self) -> None:
        runner = CardRunner(clock=self.day)
        self.words = {str(i): "unchanged" for i in range(30)}
        p = await self.go(runner, [str(i) for i in range(30)], cap=5)
        self.assertEqual(p.calls, 5)  # (not 30 scenes: the first batch ran out at 5)

    async def test_a_failing_scene_sets_the_wait(self) -> None:
        runner = CardRunner(backoff_s=100, clock=self.day)
        self.words = {"a": "unchanged"}
        await self.go(runner, ["a"], cap=0)  # stopped by the cap, nothing failed
        runner2 = CardRunner(backoff_s=100, clock=self.day)

        async def later(ids: Sequence[str], budget: Budget) -> dict[str, str]:
            return {i: "scene-later" for i in ids}

        p = Pass(budget=Budget(9))
        await runner2.work(
            p, 1, "c", version=1, sessions=1, agreed=0, entity_ids=["a"],
            who=self.who, mentions=later,
        )  # fmt: skip
        self.assertEqual(p.failed, 1)
        self.assertTrue(runner2.fresh(1, "c", 1, 1, 0))  # (waiting)
        self.day.now = 101
        self.assertFalse(runner2.fresh(1, "c", 1, 1, 0))

    async def test_a_person_who_stops_makes_the_campaign_due_again(self) -> None:
        runner = CardRunner(clock=self.day)
        self.words = {"a": "unchanged"}
        await self.go(runner, ["a"], agreed=7)
        self.assertTrue(runner.fresh(1, "c", 1, 2, 7))
        self.assertFalse(runner.fresh(1, "c", 1, 2, 8))

    async def test_an_exception_ends_that_campaign_and_nothing_else(self) -> None:
        runner = CardRunner(backoff_s=100, clock=self.day)

        async def boom(entity_id: str) -> str:
            raise RuntimeError("quoted words")

        p = Pass()
        await runner.work(
            p, 1, "c", version=1, sessions=1, agreed=0, entity_ids=["a"],
            who=boom, mentions=self.mentions,
        )  # fmt: skip
        self.assertEqual(p.failed, 1)
        self.assertTrue(runner.fresh(1, "c", 1, 1, 0))

    def test_the_campaigns_start_from_a_different_one_each_pass(self) -> None:
        runner = CardRunner(clock=self.day)
        firsts = {runner.rotated(["x", "y", "z"])[0] for _ in range(3)}
        self.assertEqual(firsts, {"x", "y", "z"})
