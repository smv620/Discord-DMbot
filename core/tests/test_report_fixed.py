"""Recording an after-session item as "fixed" when the DM changes its connection (#2661): the
changes that count and the ones that do not, which items they reach, which answers give way, and
what a later check does about it. Invented names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import report_fixed as rf
from dmbot.memory import report_store as rs
from dmbot.memory._changes import BELIEFS
from dmbot.memory.contradiction_grades import Graded, GradeStore
from dmbot.memory.contradictions import Candidate
from dmbot.memory.differences import CLASHES, DENIED, Difference
from dmbot.memory.models import CONFIRMED, REJECTED
from dmbot.memory.store import _delete_relation
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest

NOW = 1_700_000_000


class ThePureRule(unittest.TestCase):
    def row(self, **kw: Any) -> dict[str, Any]:
        return {
            "id": "r", "status": "confirmed", "to_session_at": None, "predicate": "ally_of",
            "detail": "", "directional": False, "secret": False, "unrevealed": False,
            "subject_id": "a", "object_id": "b", **kw,
        }  # fmt: skip

    def test_what_counts_and_what_does_not(self) -> None:
        was = self.row()
        for label, after, want in (
            ("rejected", self.row(status="rejected"), True),
            ("ended", self.row(to_session_at=5), True),
            ("other words", self.row(predicate="enemy_of"), True),
            ("other detail", self.row(detail="niece"), True),
            ("made secret", self.row(secret=True), False),
            ("revealed", self.row(unrevealed=True), False),
            ("repointed by a merge", self.row(subject_id="z"), False),
            ("nothing", self.row(), False),
        ):
            with self.subTest(label):
                self.assertEqual(rf.changed_meaning("update", was, after), want)

    def test_a_removed_connection_counts_and_an_insert_is_not_a_change(self) -> None:
        self.assertTrue(rf.changed_meaning("delete", self.row(), None))
        self.assertFalse(rf.changed_meaning("insert", None, self.row()))


class Fixing(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = rs.ReportStore(self.db, clock=lambda: NOW)
        self.grades = GradeStore(self.db, clock=lambda: NOW)
        self.bram = await self.add("Bram")
        self.mira = await self.add("Mira")
        self.held = await self.link("ally_of")  # what the party holds
        self.truth = await self.link("enemy_of")  # what The truth says
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT id FROM memory_perspectives WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = 'party'",
                (GUILD_A, self.c),
            )
            row = await cur.fetchone()
        assert row is not None
        self.party = str(row["id"])
        await self.store.save_differences(
            GUILD_A, self.c, [rs.SortedDifference(self.clash(), rs.FALSE, "Not so.", 500)]
        )
        self.key = rs.difference_key(self.party, self.held)

    async def link(self, predicate: str, *, back: bool = False) -> str:
        a, b = (self.mira, self.bram) if back else (self.bram, self.mira)
        made = await self.relate(a, predicate, b, status=CONFIRMED)
        return str(made.value[0].id)

    def clash(self) -> Difference:
        return Difference(self.party, "party", self.held, self.truth, CLASHES, False, False)

    async def answers(self) -> dict[str, rs.Answer]:
        return await self.store.answers(GUILD_A, self.c)

    # ---- which changes ------------------------------------------------------------------

    async def test_the_dm_removing_the_truth_connection_marks_the_difference_fixed(self) -> None:
        await self.memory.update_relation(GUILD_A, self.c, self.truth, source="dm", status=REJECTED)
        (a,) = (await self.answers()).values()
        self.assertEqual((a.item_key, a.answer, a.item_kind), (self.key, "fixed", "difference"))
        self.assertEqual((a.answered_by, a.remind_from), (0, None))

    async def test_new_words_for_the_truth_connection_mark_it_fixed(self) -> None:
        await self.memory.change_relation_words(
            GUILD_A, self.c, self.truth, "ally_of", "", source="dm"
        )
        self.assertEqual([a.answer for a in (await self.answers()).values()], ["fixed"])

    async def test_ending_the_connection_marks_it_fixed(self) -> None:
        await self.memory.update_relation(GUILD_A, self.c, self.truth, source="dm", to_session_at=9)
        self.assertEqual([a.answer for a in (await self.answers()).values()], ["fixed"])

    async def test_a_secret_flag_marks_nothing(self) -> None:
        await self.memory.update_relation(GUILD_A, self.c, self.truth, source="dm", secret=True)
        self.assertEqual(await self.answers(), {})

    async def test_undoing_the_dms_change_puts_the_item_back(self) -> None:
        done = await self.memory.update_relation(
            GUILD_A, self.c, self.truth, source="dm", status=REJECTED
        )
        self.assertEqual([a.answer for a in (await self.answers()).values()], ["fixed"])
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch, source="dm")
        self.assertEqual(await self.answers(), {})

    async def test_undoing_a_fix_gives_back_the_reminder_and_its_date(self) -> None:
        await self.store.answer(
            GUILD_A, self.c, self.key, rs.DIFFERENCE, rs.REMIND, 7, remind_from=400
        )
        done = await self.memory.update_relation(
            GUILD_A, self.c, self.truth, source="dm", status=REJECTED
        )
        self.assertEqual([a.answer for a in (await self.answers()).values()], ["fixed"])
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch, source="dm")
        (a,) = (await self.answers()).values()
        self.assertEqual((a.answer, a.remind_from), ("remind", 400))

    async def test_a_discord_answer_on_an_item_fixed_over_a_reminder_goes_through(self) -> None:
        await self.store.answer(
            GUILD_A, self.c, self.key, rs.DIFFERENCE, rs.REMIND, 7, remind_from=400
        )
        await self.memory.update_relation(GUILD_A, self.c, self.truth, source="dm", status=REJECTED)
        await self.store.answer(GUILD_A, self.c, self.key, rs.DIFFERENCE, rs.INTENDED, 7)
        (a,) = (await self.answers()).values()
        self.assertEqual((a.answer, a.remind_from), ("intended", None))
        async with self.db.guild(GUILD_A) as conn:  # and nothing of the old reminder is left
            cur = await conn.execute("SELECT remind_was FROM report_answers")
            self.assertEqual([r["remind_was"] for r in await cur.fetchall()], [None])

    async def test_undoing_a_pin_or_a_secret_flag_keeps_the_fix(self) -> None:
        await self.memory.update_relation(GUILD_A, self.c, self.truth, source="dm", status=REJECTED)
        secret = await self.memory.update_relation(
            GUILD_A, self.c, self.truth, source="dm", secret=True
        )
        assert secret.batch is not None
        await self.memory.undo(GUILD_A, self.c, secret.batch, source="dm")
        self.assertEqual([a.answer for a in (await self.answers()).values()], ["fixed"])

    async def test_undoing_a_partys_belief_does_not_un_fix_anything(self) -> None:
        denied = Difference(self.party, "party", self.held, None, DENIED, False, False)
        await self.store.save_differences(GUILD_A, self.c, [rs.SortedDifference(denied, rs.FALSE)])
        perspectives = await self.memory.perspectives(GUILD_A, self.c)
        truth = next(p.id for p in perspectives if p.kind == "truth")
        await self.memory.believe(
            GUILD_A, self.c, truth, self.held, 1.0, "revealed_fact", source="dm"
        )
        async with self.memory._write(GUILD_A, self.c, "dm") as w:  # the truth's belief goes aside
            for belief in await w.select(BELIEFS, " AND perspective_id = %s", [truth]):
                await w.delete(BELIEFS, belief["id"])
        self.assertEqual([a.answer for a in (await self.answers()).values()], ["fixed"])
        party = await self.memory.believe(
            GUILD_A, self.c, self.party, self.held, 1.0, "revealed_fact", source="dm"
        )
        assert party.batch is not None
        await self.memory.undo(GUILD_A, self.c, party.batch, source="dm")
        self.assertEqual([a.answer for a in (await self.answers()).values()], ["fixed"])

    async def test_undoing_a_later_belief_keeps_a_fix_the_truth_still_holds(self) -> None:
        denied = Difference(self.party, "party", self.held, None, DENIED, False, False)
        await self.store.save_differences(GUILD_A, self.c, [rs.SortedDifference(denied, rs.FALSE)])
        truth = next(
            p.id for p in await self.memory.perspectives(GUILD_A, self.c) if p.kind == "truth"
        )
        await self.memory.believe(
            GUILD_A, self.c, truth, self.held, 1.0, "revealed_fact", source="dm"
        )
        pinned = await self.memory.pin(GUILD_A, self.c, self.held, True, source="dm")
        assert pinned.batch is not None
        await self.memory.undo(GUILD_A, self.c, pinned.batch, source="dm")
        self.assertEqual([a.answer for a in (await self.answers()).values()], ["fixed"])

    async def test_undo_leaves_another_answer_alone(self) -> None:
        done = await self.memory.update_relation(
            GUILD_A, self.c, self.truth, source="dm", status=REJECTED
        )
        await self.store.answer(GUILD_A, self.c, self.key, rs.DIFFERENCE, rs.INTENDED, 7)
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch, source="dm")
        self.assertEqual([a.answer for a in (await self.answers()).values()], ["intended"])

    async def test_deleting_a_connection_marks_the_item_it_took_with_it(self) -> None:
        async with self.memory._write(GUILD_A, self.c, "dm") as w:
            await _delete_relation(w, self.truth)  # what a merge does with a twin
        self.assertEqual(
            await self.store.differences(GUILD_A, self.c), []
        )  # the item went with the connection
        (a,) = (await self.answers()).values()
        self.assertEqual((a.item_key, a.answer), (self.key, "fixed"))

    async def test_a_change_to_another_connection_marks_nothing(self) -> None:
        other = await self.link("knows", back=True)
        await self.memory.update_relation(GUILD_A, self.c, other, source="dm", status=REJECTED)
        self.assertEqual(await self.answers(), {})

    # ---- which answers give way -----------------------------------------------------------

    async def test_intended_stays_and_remind_gives_way(self) -> None:
        await self.store.answer(GUILD_A, self.c, self.key, rs.DIFFERENCE, rs.INTENDED, 7)
        await self.memory.update_relation(GUILD_A, self.c, self.truth, source="dm", status=REJECTED)
        (a,) = (await self.answers()).values()
        self.assertEqual((a.answer, a.answered_by), ("intended", 7))
        await self.store.answer(
            GUILD_A, self.c, self.key, rs.DIFFERENCE, rs.REMIND, 7, remind_from=400
        )
        await self.memory.update_relation(GUILD_A, self.c, self.held, source="dm", status=REJECTED)
        (a,) = (await self.answers()).values()
        self.assertEqual((a.answer, a.remind_from), ("fixed", None))

    # ---- a denied difference -------------------------------------------------------------

    async def test_the_truth_taking_up_the_partys_connection_fixes_a_denied_difference(
        self,
    ) -> None:
        denied = Difference(self.party, "party", self.held, None, DENIED, False, False)
        await self.store.save_differences(GUILD_A, self.c, [rs.SortedDifference(denied, rs.FALSE)])
        perspectives = await self.memory.perspectives(GUILD_A, self.c)
        truth = next(p.id for p in perspectives if p.kind == "truth")
        other = await self.link("knows", back=True)  # another connection: not the fix
        await self.memory.believe(GUILD_A, self.c, truth, other, 1.0, "revealed_fact", source="dm")
        self.assertEqual(await self.answers(), {})
        await self.memory.believe(
            GUILD_A, self.c, truth, self.held, 1.0, "revealed_fact", source="dm"
        )
        (a,) = (await self.answers()).values()
        self.assertEqual((a.item_key, a.answer), (self.key, "fixed"))

    # ---- a contradiction -------------------------------------------------------------------

    async def test_a_contradiction_naming_the_connection_is_fixed_too(self) -> None:
        first, second = sorted((self.held, self.truth))
        cand = Candidate(self.party, "contradiction", first, second, False, False)
        await self.grades.save(GUILD_A, self.c, [Graded(cand, "minor", "A clash.", 500)])
        await self.memory.update_relation(GUILD_A, self.c, self.held, source="dm", status=REJECTED)
        keys = set((await self.answers()).keys())
        self.assertIn(rs.contradiction_key(self.party, "contradiction", first, second), keys)

    # ---- what a later check does ---------------------------------------------------------

    async def test_an_item_found_again_loses_its_fixed_answer_other_answers_stay(self) -> None:
        await self.memory.update_relation(GUILD_A, self.c, self.truth, source="dm", status=REJECTED)
        self.assertEqual([a.answer for a in (await self.answers()).values()], ["fixed"])
        await self.store.save_differences(
            GUILD_A, self.c, [rs.SortedDifference(self.clash(), rs.FALSE)]
        )
        self.assertEqual(await self.answers(), {})  # the change did not fix it after all
        await self.store.answer(GUILD_A, self.c, self.key, rs.DIFFERENCE, rs.INTENDED, 7)
        await self.store.save_differences(
            GUILD_A, self.c, [rs.SortedDifference(self.clash(), rs.FALSE)]
        )
        self.assertEqual([a.answer for a in (await self.answers()).values()], ["intended"])

    async def test_an_item_no_longer_found_takes_its_fixed_answer_with_it(self) -> None:
        await self.memory.update_relation(GUILD_A, self.c, self.truth, source="dm", status=REJECTED)
        await self.store.drop_differences_except(GUILD_A, self.c, set())
        self.assertEqual(await self.answers(), {})
        self.assertEqual(await self.store.differences(GUILD_A, self.c), [])

    async def test_a_fixed_answer_of_another_kind_or_an_unfixed_one_is_not_swept(self) -> None:
        other_key = rs.contradiction_key(self.party, "contradiction", "a" * 32, "b" * 32)
        await self.store.answer(GUILD_A, self.c, other_key, rs.CONTRADICTION, rs.FIXED, 0)
        await self.store.answer(GUILD_A, self.c, self.key, rs.DIFFERENCE, rs.INTENDED, 7)
        await self.store.drop_differences_except(GUILD_A, self.c, set())
        self.assertEqual(set((await self.answers()).keys()), {other_key, self.key})

    async def test_nothing_crosses_campaigns(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Elsewhere", 7)).id
        await self.memory.update_relation(GUILD_A, self.c, self.truth, source="dm", status=REJECTED)
        self.assertEqual(await self.store.answers(GUILD_A, other), {})


if __name__ == "__main__":
    unittest.main()
