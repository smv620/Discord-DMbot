"""One pass over a campaign's contradiction candidates (#2660): find, grade the ones not graded yet,
keep the grades. The AI is canned; invented names."""

from __future__ import annotations

import json
from typing import Any

from dmbot.ai import Reply
from dmbot.memory import contradiction_run as run
from dmbot.memory.contradiction_grades import GradeStore
from tests.test_contradiction_grader import Canned
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest


def answer(*grades: tuple[int, str, str], cut: bool = False) -> Reply:
    return Reply(
        json.dumps({"grades": [{"n": n, "grade": g, "reason": r} for n, g, r in grades]}), cut
    )


class Passing(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.grades = GradeStore(self.db)
        bram, mira = await self.add("Bram"), await self.add("Mira")
        await self.relate(bram, "ally_of", mira, status="confirmed")
        await self.relate(bram, "enemy_of", mira, status="confirmed")

    async def go(self, client: Canned, **kw: Any) -> run.Report:
        kw.setdefault("max_calls", 5)
        return await run.run(
            self.memory, self.grades, client, GUILD_A, self.c, session_started_at=500, **kw
        )

    async def test_the_candidates_are_graded_and_kept(self) -> None:
        # (the DM's view and the party's copy each hold both: two points of view, two candidates)
        client = Canned(answer((1, "major", "Both at once."), (2, "minor", "A copy.")))
        report = await self.go(client)
        self.assertEqual((report.found, report.asked, report.major, report.minor), (2, 2, 1, 1))
        rows = await self.grades.rows(GUILD_A, self.c)
        self.assertEqual(
            sorted((r.grade, r.reason, r.session_started_at) for r in rows),
            [("major", "Both at once.", 500), ("minor", "A copy.", 500)],
        )
        self.assertNotIn("Both", report.line())  # a count line, no words

    async def test_a_graded_candidate_is_not_asked_about_again(self) -> None:
        await self.go(Canned(answer((1, "minor", "Small."), (2, "minor", "Small."))))
        client = Canned()
        report = await self.go(client)
        self.assertEqual((report.found, report.asked, client.asked), (2, 0, []))

    async def test_a_cut_answer_is_asked_again_next_time_and_never_minor(self) -> None:
        first = await self.go(Canned(Reply('{"grades": [{"n": 1, "gra', True)))
        self.assertEqual((first.not_graded, first.minor, first.cut), (2, 0, 1))
        client = Canned(answer((1, "major", "Now."), (2, "major", "Now.")))
        second = await self.go(client)
        self.assertEqual((second.asked, second.major), (2, 2))
        self.assertEqual({r.grade for r in await self.grades.rows(GUILD_A, self.c)}, {"major"})

    async def test_no_calls_left_asks_nothing(self) -> None:
        client = Canned()
        report = await self.go(client, max_calls=0)
        self.assertEqual((report.found, report.asked, client.asked), (2, 0, []))
        self.assertEqual(await self.grades.rows(GUILD_A, self.c), [])

    async def test_each_call_is_counted(self) -> None:
        counted: list[int] = []

        async def count() -> None:
            counted.append(1)

        await self.go(Canned(answer()), count_call=count)
        self.assertEqual(len(counted), 1)

    async def test_nothing_found_costs_no_call(self) -> None:
        elsewhere = (await self.campaigns.create(GUILD_A, "Quiet", 7)).id
        client = Canned()
        report = await run.run(
            self.memory, self.grades, client, GUILD_A, elsewhere, session_started_at=1, max_calls=3
        )
        self.assertEqual((report.found, report.calls, client.asked), (0, 0, []))
