"""The card round decides whether and how much to work (#5050 b3b): no database, no AI."""

from __future__ import annotations

import unittest
from collections.abc import Sequence

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

    async def mentions(self, ids: Sequence[str]) -> dict[str, str]:
        self.looked.append(ids)
        return {i: "kept" for i in ids}

    async def go(self, runner: CardRunner, ids: Sequence[str], version: int = 1) -> Pass:
        p = Pass()
        await runner.work(
            p, 1, "c", version=version, sessions=2, entity_ids=ids,
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
        runner = CardRunner(calls_per_pass=2, clock=self.day)
        first = await self.go(runner, ["a", "b", "c"])
        self.assertEqual((first.calls, self.said), (2, ["a", "b"]))
        second = await self.go(runner, ["a", "b", "c"])  # (not marked finished)
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
        runner = CardRunner(calls_per_pass=1, clock=self.day)
        self.words = {"a": "unchanged", "b": "little"}
        p = await self.go(runner, ["a", "b", "c"])
        self.assertEqual((p.calls, self.said), (1, ["a", "b", "c"]))

    async def test_mentions_are_looked_up_in_batches(self) -> None:
        runner = CardRunner(calls_per_pass=999, clock=self.day)
        ids = [str(i) for i in range(45)]
        await self.go(runner, ids)
        self.assertEqual([len(b) for b in self.looked], [20, 20, 5])
