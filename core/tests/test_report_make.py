"""Making one session's after-session report (#2661): the grading and the sort run in order under
one call limit, progress is a stage and two counts, one run at a time, and a step that could not
finish leaves its items for next time. The AI is canned. Invented names."""

from __future__ import annotations

import json
from typing import Any

from dmbot.ai import AIOutOfFunds, Reply
from dmbot.memory import report_make as rm
from dmbot.memory import report_store as rs
from dmbot.memory.contradiction_grades import GradeStore
from dmbot.memory.models import new_id
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest

SESSION = 700


class Canned:
    """Answers a grading question with every pair major and a sort question with every case
    `disguised`, as the two kinds of question say what they are in their system text."""

    def __init__(self, *, fail_on: int | None = None, funds_on: int | None = None) -> None:
        self.calls = 0
        self.systems: list[str] = []
        self.fail_on, self.funds_on = fail_on, funds_on

    async def complete(self, system: str, text: str, *, max_tokens: int = 0) -> Reply:
        self.calls += 1
        self.systems.append(system)
        if self.funds_on == self.calls:
            raise AIOutOfFunds("no credit")
        if self.fail_on == self.calls:
            raise RuntimeError("down")
        rows = [ln for ln in text.splitlines() if ln[:1].isdigit()]
        if '"sorts"' in system:
            return Reply(
                json.dumps({"sorts": [
                    {"n": n, "sort": "disguised", "reason": "A cover story."}
                    for n in range(1, len(rows) + 1)
                ]}),
                False,
            )  # fmt: skip
        return Reply(
            json.dumps({"grades": [
                {"n": n, "grade": "major", "reason": "Both at once."}
                for n in range(1, len(rows) + 1)
            ]}),
            False,
        )  # fmt: skip


class Making(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = 1_700_000_000
        self.reports = rs.ReportStore(self.db, clock=lambda: self.now)
        self.grades = GradeStore(self.db)
        self.bram, self.mira = await self.add("Bram"), await self.add("Mira")
        self.ally = (
            (await self.relate(self.bram, "ally_of", self.mira, status="confirmed")).value[0].id
        )
        await self.relate(self.bram, "enemy_of", self.mira, status="confirmed")
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE memory_beliefs SET session_at = 0 WHERE session_at IS NULL")
            cur = await conn.execute(
                "SELECT id FROM memory_perspectives WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = 'truth'",
                (GUILD_A, self.c),
            )
            row = await cur.fetchone()
            assert row is not None
            # the DM's view says "not allies": the party's belief in it is then a difference
            await conn.execute(
                "INSERT INTO memory_beliefs (guild_id, campaign_id, id, perspective_id,"
                " relation_id, confidence, status, session_at, evidence_ids, seq, recorded_at)"
                " SELECT %s, %s, %s, %s, %s, 0.0, 'false_evidence', 1, '{}',"
                " COALESCE(max(seq), 0) + 1, 1 FROM memory_beliefs"
                " WHERE guild_id = %s AND campaign_id = %s",
                (GUILD_A, self.c, new_id(), row["id"], self.ally, GUILD_A, self.c),
            )

    async def go(self, client: Canned, **kw: Any) -> rm.Made:
        kw.setdefault("max_calls", 10)
        return await rm.make(
            self.memory, self.grades, self.reports, client, GUILD_A, self.c, SESSION,
            session_started_at=500, **kw,
        )  # fmt: skip

    async def run_of(self) -> rs.Run:
        run = await self.reports.run(GUILD_A, self.c, SESSION)
        assert run is not None
        return run

    async def test_it_grades_then_sorts_and_ends_ready(self) -> None:
        client = Canned()
        made = await self.go(client)
        self.assertEqual((made.ran, made.failed), (True, ""))
        self.assertGreater(len(await self.grades.rows(GUILD_A, self.c)), 0)
        stored = await self.reports.differences(GUILD_A, self.c)
        self.assertEqual([(d.sort, d.reason) for d in stored], [("disguised", "A cover story.")])
        self.assertEqual(made.differences, 1)
        run = await self.run_of()
        self.assertEqual((run.state, run.reason), ("ready", ""))
        self.assertEqual(client.calls, made.calls)
        # the grading question came before the sort question
        self.assertIn('"grades"', client.systems[0])
        self.assertIn('"sorts"', client.systems[-1])

    async def test_the_progress_names_the_stage_with_counts_and_no_words(self) -> None:
        seen: list[tuple[str, int, int]] = []
        real = self.reports.progress

        async def watch(g: int, c: str, at: int, token: int, stage: str, d: int, t: int) -> None:
            seen.append((stage, d, t))
            await real(g, c, at, token, stage, d, t)

        self.reports.progress = watch  # type: ignore[method-assign,assignment]
        await self.go(Canned())
        stages = [s for s, _, _ in seen]
        self.assertEqual(stages[0], "reading")
        self.assertLess(stages.index("grading"), stages.index("sorting"))
        self.assertTrue(all(isinstance(d, int) and isinstance(t, int) for _, d, t in seen))
        self.assertIn(("sorting", 1, 1), seen)

    async def test_a_second_run_for_the_same_session_does_not_start(self) -> None:
        await self.reports.begin(GUILD_A, self.c, SESSION)  # one is going
        client = Canned()
        made = await self.go(client)
        self.assertEqual((made.ran, client.calls), (False, 0))
        self.assertIn("already being made", made.line())

    async def test_what_was_sorted_is_not_asked_again_and_the_grades_neither(self) -> None:
        await self.go(Canned())
        again = Canned()
        made = await self.go(again)
        self.assertEqual((made.failed, made.sorted, again.calls), ("", 0, 0))

    async def test_the_call_limit_is_shared_and_what_is_left_waits_for_next_time(self) -> None:
        client = Canned()
        made = await self.go(client, max_calls=1)  # the grading takes the only call
        self.assertEqual((client.calls, made.sorted), (1, 0))
        self.assertEqual(made.failed, "")
        self.assertEqual(await self.reports.differences(GUILD_A, self.c), [])  # not "false"
        self.assertEqual(made.not_sorted, 1)  # the one never asked about is still counted
        run = await self.run_of()
        # ready, but marked so the page never shows an unfinished report as whole
        self.assertEqual((run.state, run.reason), ("ready", rm.LEFT))
        later = Canned()
        done = await self.go(later, max_calls=5)
        self.assertEqual(done.sorted, 1)
        self.assertEqual(len(await self.reports.differences(GUILD_A, self.c)), 1)

    async def test_a_progress_write_that_fails_does_not_lose_the_work(self) -> None:
        async def broken(*args: Any, **kw: Any) -> None:
            raise RuntimeError("db")

        self.reports.progress = broken  # type: ignore[method-assign]
        made = await self.go(Canned())
        self.assertEqual((made.failed, made.sorted), ("", 1))
        self.assertEqual(len(await self.reports.differences(GUILD_A, self.c)), 1)
        self.assertGreater(len(await self.grades.rows(GUILD_A, self.c)), 0)

    async def test_a_call_limit_during_grading_marks_the_report_not_whole(self) -> None:
        client = Canned()
        made = await self.go(client, max_calls=0)  # not one call for the grading
        self.assertEqual((client.calls, made.failed), (0, ""))
        assert made.graded is not None
        self.assertGreater(made.graded.left, 0)  # counted from what was found, not what was asked
        self.assertEqual((await self.run_of()).reason, rm.LEFT)

    async def test_a_run_taken_over_cannot_finish_the_one_that_took_over(self) -> None:
        takeover: list[int | None] = []

        class Slow(Canned):
            async def complete(inner, system: str, text: str, *, max_tokens: int = 0) -> Reply:
                if not takeover:  # the first call: the run stalls, another one takes over
                    self.now += rs.STALLED_S + 1
                    takeover.append(await self.reports.begin(GUILD_A, self.c, SESSION))
                return await super().complete(system, text, max_tokens=max_tokens)

        made = await self.go(Slow())
        self.assertEqual(made.failed, "")  # the old run thought it finished
        (token,) = takeover
        self.assertIsNotNone(token)
        run = await self.run_of()
        # but its finish changed nothing: the new run is still the one making the report
        self.assertEqual((run.state, run.started_at), ("making", token))

    async def test_a_run_cancelled_by_a_restart_is_closed_as_failed_not_left_making(self) -> None:
        import asyncio

        class Cancelled(Canned):
            async def complete(self, system: str, text: str, *, max_tokens: int = 0) -> Reply:
                raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await self.go(Cancelled())
        run = await self.run_of()
        self.assertEqual((run.state, run.reason), ("failed", rm.FAILED))

    async def test_each_call_is_counted_before_it_is_made(self) -> None:
        events: list[str] = []
        client = Canned()

        async def count() -> None:
            events.append(f"count{client.calls}")

        made = await self.go(client, count_call=count)
        self.assertEqual(events, [f"count{n}" for n in range(made.calls)])

    async def test_out_of_funds_fails_the_run_with_a_code_and_no_sort_is_kept(self) -> None:
        made = await self.go(Canned(funds_on=1))
        self.assertEqual(made.failed, rm.OUT_OF_FUNDS)
        run = await self.run_of()
        self.assertEqual((run.state, run.reason), ("failed", "out_of_funds"))
        self.assertEqual(await self.reports.differences(GUILD_A, self.c), [])

    async def test_an_unexpected_error_fails_the_run_with_a_code_never_its_words(self) -> None:
        self.reports.drop_differences_except = self._boom  # type: ignore[method-assign]
        made = await self.go(Canned())
        run = await self.run_of()
        self.assertEqual((made.failed, run.state, run.reason), (rm.FAILED, "failed", "failed"))
        self.assertNotIn("down", made.line())

    @staticmethod
    async def _boom(*args: Any, **kw: Any) -> int:
        raise RuntimeError("down")

    async def test_the_log_line_is_counts_only(self) -> None:
        made = await self.go(Canned())
        line = made.line()
        self.assertIn("differences=1", line)
        self.assertNotIn("cover story", line)
        self.assertNotIn("Bram", line)

    async def test_another_campaign_is_untouched(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Sunken City", 1)).id
        await self.go(Canned())
        self.assertEqual(await self.reports.differences(GUILD_A, other), [])
        self.assertIsNone(await self.reports.run(GUILD_A, other, SESSION))
