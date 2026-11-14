"""DMbot joins a name itself only when sure (#3602, slice 4): the pure rule, the step with and
without the switch, consent right before the change. Invented names; no database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.memory import who_apply
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.who_answers import ENTITY, NEW, StoredAnswer
from tests.test_who_step import FakeAnswers, FakeClient, reply, waiting

KEEP, GONE = "a" * 32, "b" * 32


def ent(how: str = "sure", target: str | None = KEEP) -> StoredAnswer:
    return StoredAnswer(ENTITY, how, target_id=target)


class TheRule(unittest.TestCase):
    def test_only_a_sure_answer_naming_an_entry_may_apply(self) -> None:
        self.assertEqual(who_apply.may_apply([ent()]), ent())
        self.assertIsNone(who_apply.may_apply([]))
        self.assertIsNone(who_apply.may_apply([ent("likely")]))
        self.assertIsNone(who_apply.may_apply([ent("maybe")]))
        self.assertIsNone(who_apply.may_apply([StoredAnswer(NEW, "sure", new_kind="place")]))
        self.assertIsNone(who_apply.may_apply([ent(target=None)]))

    def test_only_the_best_ranked_answer_counts(self) -> None:
        self.assertIsNone(who_apply.may_apply([ent("likely"), ent("sure", "c" * 32)]))
        self.assertEqual(who_apply.may_apply([ent(), ent("likely", "c" * 32)]), ent())


class TheJoiner(unittest.IsolatedAsyncioTestCase):
    async def go(self, answers: list[StoredAnswer], error: Exception | None = None) -> Any:
        memory = SimpleNamespace(join_by_ai=AsyncMock(side_effect=error))
        word = await who_apply.joiner(memory, 1, "c")(GONE, answers)  # type: ignore[arg-type]
        return word, memory.join_by_ai

    async def test_a_sure_answer_joins_through_the_one_door_with_the_kept_entry_first(self) -> None:
        word, door = await self.go([ent()])
        self.assertEqual(word, who_apply.JOINED)
        door.assert_awaited_once_with(1, "c", KEEP, GONE)

    async def test_an_unsure_answer_never_reaches_the_store(self) -> None:
        word, door = await self.go([ent("likely")])
        self.assertEqual((word, door.await_count), (who_apply.SKIPPED, 0))

    async def test_a_refusal_and_a_failure_become_words_not_exceptions(self) -> None:
        word, _ = await self.go([ent()], MemoryRuleError("no"))
        self.assertEqual(word, who_apply.REFUSED)
        word, _ = await self.go([ent()], RuntimeError("boom"))
        self.assertEqual(word, who_apply.FAILED)


class InTheStep(unittest.IsolatedAsyncioTestCase):
    async def step(
        self, how: str, *, apply: Any = "default", after: set[int] | None = None, **kw: Any
    ) -> tuple[Any, Any, Any]:
        p = waiting(speakers=(8,))
        calls: list[Any] = []

        async def joined(entity: str, answers: list[StoredAnswer]) -> str:
            calls.append((entity, answers))
            return who_apply.JOINED

        applier = joined if apply == "default" else apply
        import dmbot.memory.who_step as step

        client, answers = FakeClient(reply(p, ("c1", how))), FakeAnswers()
        # (the shared `run` has no `apply`: this one passes it straight to the step)
        counts = {"n": 0}

        async def consenting() -> Any:
            counts["n"] += 1
            return after if (after is not None and counts["n"] > 2) else {8, 9}

        async def count() -> None:
            return None

        report = await step.run(
            client, answers,  # type: ignore[arg-type]
            [p], guild_id=1, campaign_id="c", consenting=consenting, model=lambda: "m",
            session_started_at=500, max_calls=5, count_call=count, apply=applier,
        )  # fmt: skip
        return report, calls, answers

    async def test_a_sure_answer_is_joined_and_counted(self) -> None:
        report, calls, _ = await self.step("sure")
        self.assertEqual((report.joined, len(calls), report.ranked), (1, 1, 1))

    async def test_an_unsure_answer_is_ranked_and_not_applied(self) -> None:
        report, calls, _ = await self.step("likely")
        self.assertEqual((report.joined, calls, report.ranked), (0, [], 1))

    async def test_with_no_apply_nothing_is_applied(self) -> None:
        report, calls, _ = await self.step("sure", apply=None)
        self.assertEqual((report.joined, calls, report.ranked), (0, [], 1))

    async def test_consent_is_asked_once_more_right_before_the_change(self) -> None:
        report, calls, _ = await self.step("sure", after={9})  # (8 stopped after the answer)
        self.assertEqual((report.joined, calls), (0, []))
        self.assertGreaterEqual(report.dropped_for_consent, 1)

    async def test_a_refusal_and_a_failure_are_counted(self) -> None:
        async def refuse(entity: str, answers: list[StoredAnswer]) -> str:
            return who_apply.REFUSED

        async def fail(entity: str, answers: list[StoredAnswer]) -> str:
            return who_apply.FAILED

        report, _, _ = await self.step("sure", apply=refuse)
        self.assertEqual((report.joined, report.join_refused, report.join_failed), (0, 1, 0))
        report, _, _ = await self.step("sure", apply=fail)
        self.assertEqual((report.joined, report.join_refused, report.join_failed), (0, 0, 1))
