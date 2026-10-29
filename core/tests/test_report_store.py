"""The after-session report's storage (#2661): the differences with PlotBot's sort, the DM's
answers kept apart from the items, and the progress of making a report. Invented names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.memory import report_store as rs
from dmbot.memory.differences import CLASHES, DENIED, Difference
from dmbot.memory.report_backup import ReportAnswersSection
from tests.test_memory_store import GUILD_A, GUILD_B, ConfirmedMemoryTest

NOW = 1_700_000_000


class Storing(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = rs.ReportStore(self.db, clock=lambda: NOW)
        self.bram = await self.add("Bram")
        self.mira = await self.add("Mira")
        self.held = await self.link("ally_of")
        self.truth = await self.link("enemy_of")
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT id FROM memory_perspectives WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = 'party'",
                (GUILD_A, self.c),
            )
            row = await cur.fetchone()
        assert row is not None
        self.party = str(row["id"])

    async def link(self, predicate: str, *, back: bool = False) -> str:
        a, b = (self.mira, self.bram) if back else (self.bram, self.mira)
        made = await self.relate(a, predicate, b, status="confirmed")
        return str(made.value[0].id)

    def clash(self, **kw: Any) -> Difference:
        return Difference(self.party, "party", self.held, self.truth, CLASHES, False, False, **kw)

    def denied(self) -> Difference:
        return Difference(self.party, "party", self.held, None, DENIED, True, False)

    async def test_a_sort_is_kept_with_its_reason(self) -> None:
        saved = await self.store.save_differences(
            GUILD_A,
            self.c,
            [rs.SortedDifference(self.clash(), rs.DISGUISED, "A lie, told well.", 500)],
        )
        self.assertEqual(saved, 1)
        (row,) = await self.store.differences(GUILD_A, self.c)
        self.assertEqual(
            (row.sort, row.reason, row.kind), ("disguised", "A lie, told well.", CLASHES)
        )
        self.assertEqual((row.relation_id, row.truth_relation_id), (self.held, self.truth))
        self.assertEqual((row.session_started_at, row.found_at), (500, NOW))

    async def test_a_denial_has_no_truth_connection(self) -> None:
        await self.store.save_differences(
            GUILD_A, self.c, [rs.SortedDifference(self.denied(), rs.FALSE, "Never happened.")]
        )
        (row,) = await self.store.differences(GUILD_A, self.c)
        self.assertEqual((row.kind, row.truth_relation_id, row.times_unknown), (DENIED, None, True))

    async def test_an_unknown_sort_is_not_sorted_never_false(self) -> None:
        await self.store.save_differences(
            GUILD_A, self.c, [rs.SortedDifference(self.clash(), "whatever")]
        )
        (row,) = await self.store.differences(GUILD_A, self.c)
        self.assertEqual(row.sort, rs.NOT_SORTED)

    async def test_a_sort_that_could_not_be_made_never_replaces_one_that_was(self) -> None:
        await self.store.save_differences(
            GUILD_A, self.c, [rs.SortedDifference(self.clash(), rs.MISUNDERSTOOD, "Mixed up.", 7)]
        )
        await self.store.save_differences(
            GUILD_A, self.c, [rs.SortedDifference(self.clash(), rs.NOT_SORTED, "", 9)]
        )
        (row,) = await self.store.differences(GUILD_A, self.c)
        self.assertEqual(
            (row.sort, row.reason, row.session_started_at), ("misunderstood", "Mixed up.", 7)
        )
        await self.store.save_differences(
            GUILD_A, self.c, [rs.SortedDifference(self.clash(), rs.FALSE, "Not so.", 9)]
        )
        (row,) = await self.store.differences(GUILD_A, self.c)
        self.assertEqual((row.sort, row.reason), ("false", "Not so."))

    async def test_a_connection_that_is_gone_is_skipped_and_one_dropped_goes_with_it(self) -> None:
        gone = Difference(self.party, "party", "f" * 32, None, DENIED, False, False)
        saved = await self.store.save_differences(
            GUILD_A,
            self.c,
            [rs.SortedDifference(gone, rs.FALSE), rs.SortedDifference(self.clash(), rs.FALSE)],
        )
        self.assertEqual(saved, 1)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("DELETE FROM memory_relations WHERE id = %s", (self.truth,))
        self.assertEqual(await self.store.differences(GUILD_A, self.c), [])  # cascades

    async def test_differences_no_longer_found_are_dropped_and_the_rest_stay(self) -> None:
        await self.store.save_differences(
            GUILD_A,
            self.c,
            [
                rs.SortedDifference(self.clash(), rs.FALSE),
                rs.SortedDifference(self.denied(), rs.FALSE),
            ],
        )
        # both share (party, held): the second replaced the first
        self.assertEqual(len(await self.store.differences(GUILD_A, self.c)), 1)
        self.assertEqual(
            await self.store.drop_differences_except(GUILD_A, self.c, {(self.party, self.held)}), 0
        )
        self.assertEqual(await self.store.drop_differences_except(GUILD_A, self.c, set()), 1)
        self.assertEqual(await self.store.differences(GUILD_A, self.c), [])

    async def test_another_campaign_or_server_never_sees_them(self) -> None:
        await self.store.save_differences(
            GUILD_A, self.c, [rs.SortedDifference(self.clash(), rs.FALSE)]
        )
        self.assertEqual(await self.store.differences(GUILD_A, "nobody"), [])
        self.assertEqual(await self.store.differences(GUILD_B, self.c), [])

    async def test_known_game_times_come_first(self) -> None:
        await self.store.save_differences(
            GUILD_A, self.c, [rs.SortedDifference(self.denied(), rs.FALSE)]
        )
        other = await self.link("enemy_of", back=True)
        known = Difference(self.party, "party", other, None, DENIED, False, False)
        await self.store.save_differences(GUILD_A, self.c, [rs.SortedDifference(known, rs.FALSE)])
        order = [r.times_unknown for r in await self.store.differences(GUILD_A, self.c)]
        self.assertEqual(order, [False, True])


class TheColumnLimit(ConfirmedMemoryTest):
    async def test_the_reason_limit_in_the_schema_is_the_one_the_code_keeps(self) -> None:
        from dmbot.memory.contradiction_grades import REASON_MAX

        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint"
                " WHERE conrelid = 'memory_differences'::regclass AND contype = 'c'"
            )
            defs = " ".join(str(r["d"]) for r in await cur.fetchall())
        self.assertIn(f"<= {REASON_MAX}", defs)


class Answering(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = rs.ReportStore(self.db, clock=lambda: NOW)
        self.key = rs.difference_key("p" * 32, "r" * 32)

    async def test_an_answer_is_kept_replaced_and_undone(self) -> None:
        self.assertTrue(
            await self.store.answer(GUILD_A, self.c, self.key, rs.DIFFERENCE, rs.INTENDED, 77)
        )
        (a,) = (await self.store.answers(GUILD_A, self.c)).values()
        self.assertEqual(
            (a.answer, a.answered_by, a.answered_at, a.remind_from), ("intended", 77, NOW, None)
        )
        await self.store.answer(
            GUILD_A, self.c, self.key, rs.DIFFERENCE, rs.REMIND, 78, remind_from=400
        )
        (a,) = (await self.store.answers(GUILD_A, self.c)).values()
        self.assertEqual((a.answer, a.answered_by, a.remind_from), ("remind", 78, 400))
        self.assertTrue(await self.store.undo(GUILD_A, self.c, self.key))
        self.assertEqual(await self.store.answers(GUILD_A, self.c), {})
        self.assertFalse(await self.store.undo(GUILD_A, self.c, self.key))  # nothing to undo

    async def test_only_the_four_answers_and_remind_needs_its_session(self) -> None:
        for answer, remind_from in (("maybe", None), (rs.REMIND, None), (rs.INTENDED, 5)):
            self.assertFalse(
                await self.store.answer(
                    GUILD_A, self.c, self.key, rs.DIFFERENCE, answer, 1, remind_from=remind_from
                ),
                answer,
            )
        self.assertFalse(
            await self.store.answer(GUILD_A, self.c, self.key, "other", rs.INTENDED, 1)
        )
        self.assertEqual(await self.store.answers(GUILD_A, self.c), {})

    async def test_answers_are_one_campaign_at_a_time(self) -> None:
        await self.store.answer(GUILD_A, self.c, self.key, rs.CONTRADICTION, rs.NOT_A_PROBLEM, 1)
        self.assertEqual(await self.store.answers(GUILD_B, self.c), {})
        self.assertEqual(await self.store.answers(GUILD_A, "nobody"), {})

    def test_an_items_key_is_made_from_its_own_ids_and_differs_by_kind(self) -> None:
        a = rs.contradiction_key("p", "contradiction", "x", "y")
        self.assertEqual(a, rs.contradiction_key("p", "contradiction", "x", "y"))
        self.assertNotEqual(a, rs.contradiction_key("p", "contradiction", "x", "z"))
        self.assertNotEqual(rs.difference_key("p", "x"), rs.difference_key("p", "y"))
        self.assertRegex(a, r"^[0-9a-f]{32}$")


class Making(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = NOW
        self.store = rs.ReportStore(self.db, clock=lambda: self.now)

    async def test_a_run_has_a_stage_counts_and_times_and_ends_ready(self) -> None:
        self.assertIsNone(await self.store.run(GUILD_A, self.c, 100))
        token = await self.store.begin(GUILD_A, self.c, 100)
        assert token is not None
        self.now += 5
        await self.store.progress(GUILD_A, self.c, 100, token, rs.GRADING, 3, 10)
        run = await self.store.run(GUILD_A, self.c, 100)
        assert run is not None
        self.assertEqual((run.state, run.stage, run.done, run.total), ("making", "grading", 3, 10))
        self.assertEqual((run.started_at, run.updated_at), (NOW, NOW + 5))
        await self.store.finish(GUILD_A, self.c, 100, token)
        run = await self.store.run(GUILD_A, self.c, 100)
        assert run is not None
        self.assertEqual((run.state, run.reason), ("ready", ""))

    async def test_a_second_press_does_not_start_a_run_until_the_first_has_stalled(self) -> None:
        self.assertIsNotNone(await self.store.begin(GUILD_A, self.c, 100))
        self.assertIsNone(await self.store.begin(GUILD_A, self.c, 100))
        self.now += rs.STALLED_S + 1  # the worker died: the run may be taken over
        self.assertIsNotNone(await self.store.begin(GUILD_A, self.c, 100))

    async def test_a_run_that_was_taken_over_cannot_change_the_one_that_took_over(self) -> None:
        old = await self.store.begin(GUILD_A, self.c, 100)
        self.now += rs.STALLED_S + 1
        new = await self.store.begin(GUILD_A, self.c, 100)
        assert old is not None and new is not None and old != new
        await self.store.progress(GUILD_A, self.c, 100, old, rs.SORTING, 9, 9)
        await self.store.finish(GUILD_A, self.c, 100, old, failed="late")
        run = await self.store.run(GUILD_A, self.c, 100)
        assert run is not None
        self.assertEqual((run.state, run.stage, run.done), ("making", "reading", 0))
        await self.store.finish(GUILD_A, self.c, 100, new)
        run = await self.store.run(GUILD_A, self.c, 100)
        assert run is not None
        self.assertEqual(run.state, "ready")

    async def test_a_failed_run_says_why_in_a_code_and_may_begin_again(self) -> None:
        token = await self.store.begin(GUILD_A, self.c, 100)
        assert token is not None
        await self.store.finish(GUILD_A, self.c, 100, token, failed="out_of_funds")
        run = await self.store.run(GUILD_A, self.c, 100)
        assert run is not None
        self.assertEqual((run.state, run.reason), ("failed", "out_of_funds"))
        self.assertIsNotNone(await self.store.begin(GUILD_A, self.c, 100))

    async def test_progress_of_a_run_that_is_over_changes_nothing(self) -> None:
        token = await self.store.begin(GUILD_A, self.c, 100)
        assert token is not None
        await self.store.finish(GUILD_A, self.c, 100, token)
        await self.store.progress(GUILD_A, self.c, 100, token, rs.SORTING, 1, 1)
        run = await self.store.run(GUILD_A, self.c, 100)
        assert run is not None
        self.assertEqual((run.state, run.stage), ("ready", "reading"))

    async def test_a_stage_that_is_not_one_of_the_three_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            await self.store.progress(GUILD_A, self.c, 100, 1, "typing", 0, 0)


GOOD = {
    "item_key": "a" * 32, "item_kind": "difference", "answer": "intended",
    "answered_by": "77", "answered_at": 5, "remind_from": None,
}  # fmt: skip


class TheBackup(ConfirmedMemoryTest):
    async def test_answers_are_dumped_checked_and_loaded_back(self) -> None:
        store = rs.ReportStore(self.db, clock=lambda: NOW)
        await store.answer(GUILD_A, self.c, "b" * 32, rs.CONTRADICTION, rs.REMIND, 9, remind_from=3)
        await store.answer(GUILD_A, self.c, "a" * 32, rs.DIFFERENCE, rs.INTENDED, 77)
        section = ReportAnswersSection()
        async with self.db.guild(GUILD_A) as conn:
            dumped = await section.dump(conn, GUILD_A, self.c)
            await section.clear(conn, GUILD_A, self.c)
        self.assertEqual([r["item_key"] for r in dumped], ["a" * 32, "b" * 32])
        self.assertEqual(await store.answers(GUILD_A, self.c), {})
        async with self.db.guild(GUILD_A) as conn:
            await section.load(conn, GUILD_A, self.c, dumped)
        got = await store.answers(GUILD_A, self.c)
        self.assertEqual(got["b" * 32].remind_from, 3)
        self.assertEqual((got["a" * 32].answer, got["a" * 32].answered_by), ("intended", 77))


class TheBackupOfAFixedReminder(ConfirmedMemoryTest):
    async def test_a_fixed_answer_keeps_the_reminder_it_replaced_through_a_backup(self) -> None:
        section = ReportAnswersSection()
        row = {**GOOD, "answer": "fixed", "remind_was": 5}
        async with self.db.guild(GUILD_A) as conn:
            await section.load(conn, GUILD_A, self.c, [row])
            (dumped,) = await section.dump(conn, GUILD_A, self.c)
            await section.clear(conn, GUILD_A, self.c)
            await section.load(conn, GUILD_A, self.c, [dumped])
            (again,) = await section.dump(conn, GUILD_A, self.c)
        self.assertEqual((dumped["answer"], dumped["remind_was"]), ("fixed", 5))
        self.assertEqual(again, dumped)


class CheckingTheBackup(unittest.TestCase):
    def check(self, *rows: dict[str, Any]) -> Any:
        return ReportAnswersSection().check(list(rows))

    def test_a_good_row_passes_and_none_is_fine(self) -> None:
        self.assertEqual(len(self.check(GOOD)), 1)
        self.assertEqual(self.check(), [])

    def test_a_file_from_before_remind_was_reads_as_none(self) -> None:
        (row,) = self.check(dict(GOOD))
        self.assertIsNone(row["remind_was"])

    def test_a_damaged_remind_was_is_refused(self) -> None:
        for bad in (
            {**GOOD, "remind_was": 5},  # only a fixed answer has one
            {**GOOD, "answer": "fixed", "remind_was": 0},
            {**GOOD, "answer": "fixed", "remind_was": -3},
            {**GOOD, "answer": "fixed", "remind_was": True},
            {**GOOD, "answer": "fixed", "remind_was": "5"},
        ):
            with self.assertRaises(CampaignError, msg=str(bad)):
                self.check(bad)
        self.assertEqual(len(self.check({**GOOD, "answer": "fixed", "remind_was": 5})), 1)

    def test_a_damaged_row_is_refused(self) -> None:
        for bad in (
            {**GOOD, "item_key": "A" * 32},
            {**GOOD, "item_key": "a" * 31},
            {**GOOD, "item_kind": "other"},
            {**GOOD, "answer": "maybe"},
            {**GOOD, "answered_by": 77},
            {**GOOD, "answered_by": "0"},
            {**GOOD, "answered_at": -1},
            {**GOOD, "answered_at": True},
            {**GOOD, "answer": "remind"},  # remind needs the session it was asked in
            {**GOOD, "remind_from": 4},  # and only remind has one
            {**GOOD, "extra": 1},
        ):
            with self.assertRaises(CampaignError, msg=str(bad)):
                self.check(bad)
        with self.assertRaises(CampaignError):
            self.check(GOOD, GOOD)  # the same item twice
        with self.assertRaises(CampaignError):
            ReportAnswersSection().check("nope")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
