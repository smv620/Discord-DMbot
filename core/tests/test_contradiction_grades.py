"""The stored grade of each contradiction candidate (#2660): major, minor or not graded, with its
reason; told once; one campaign at a time; gone with the connections it names. Invented names."""

from __future__ import annotations

import unittest

from dmbot.memory import contradiction_grades as cg
from dmbot.memory.contradictions import Candidate, Contradictions
from tests.test_memory_store import GUILD_A, GUILD_B, ConfirmedMemoryTest


class Storing(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = cg.GradeStore(self.db, clock=lambda: 1_700_000_000)
        self.bram = await self.add("Bram")
        self.mira = await self.add("Mira")
        await self.relate(self.bram, "ally_of", self.mira, status="confirmed")
        await self.relate(self.bram, "enemy_of", self.mira, status="confirmed")
        found = await Contradictions(self.memory).find(GUILD_A, self.c)
        self.assertTrue(found)
        self.candidate = found[0]

    async def rows_in(self, table: str = "memory_contradictions") -> int:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(f"SELECT count(*) AS n FROM {table}")
            row = await cur.fetchone()
        assert row is not None
        return int(row["n"])

    def key(self, c: Candidate) -> tuple[str, str, str, str]:
        return (c.perspective_id, c.kind, c.first_relation_id, c.second_relation_id)

    async def test_a_grade_is_kept_with_its_reason(self) -> None:
        saved = await self.store.save(
            GUILD_A,
            self.c,
            [cg.Graded(self.candidate, cg.MAJOR, "Allies and enemies at once.", 500)],
        )
        self.assertEqual(saved, 1)
        (row,) = await self.store.rows(GUILD_A, self.c)
        self.assertEqual((row.grade, row.reason), ("major", "Allies and enemies at once."))
        self.assertEqual(
            (row.session_started_at, row.said_at, row.found_at), (500, None, 1_700_000_000)
        )
        self.assertEqual(
            self.key(self.candidate),
            (row.perspective_id, row.kind, row.first_relation_id, row.second_relation_id),
        )

    async def test_an_unknown_grade_is_not_graded_never_minor(self) -> None:
        await self.store.save(GUILD_A, self.c, [cg.Graded(self.candidate, "kinda")])
        (row,) = await self.store.rows(GUILD_A, self.c)
        self.assertEqual(row.grade, cg.NOT_GRADED)

    async def test_a_candidate_seen_again_takes_the_new_grade_and_keeps_when_it_was_told(
        self,
    ) -> None:
        await self.store.save(GUILD_A, self.c, [cg.Graded(self.candidate, cg.MAJOR, "first")])
        await self.store.mark_said(GUILD_A, self.c, [self.key(self.candidate)])
        await self.store.save(GUILD_A, self.c, [cg.Graded(self.candidate, cg.MINOR, "second")])
        (row,) = await self.store.rows(GUILD_A, self.c)
        self.assertEqual((row.grade, row.reason), ("minor", "second"))
        self.assertIsNotNone(row.said_at)  # told once, not again
        self.assertEqual(await self.rows_in(), 1)

    async def test_a_failed_grade_never_replaces_a_real_one(self) -> None:
        await self.store.save(GUILD_A, self.c, [cg.Graded(self.candidate, cg.MAJOR, "real", 500)])
        await self.store.save(
            GUILD_A, self.c, [cg.Graded(self.candidate, cg.NOT_GRADED, "cut", 900)]
        )
        (row,) = await self.store.rows(GUILD_A, self.c)
        self.assertEqual((row.grade, row.reason, row.session_started_at), ("major", "real", 500))
        # a real grade after that does replace it
        await self.store.save(GUILD_A, self.c, [cg.Graded(self.candidate, cg.MINOR, "newer", 900)])
        (row,) = await self.store.rows(GUILD_A, self.c)
        self.assertEqual((row.grade, row.reason), ("minor", "newer"))

    async def test_told_once(self) -> None:
        await self.store.save(GUILD_A, self.c, [cg.Graded(self.candidate, cg.MAJOR)])
        self.assertEqual(len(await self.store.rows(GUILD_A, self.c, unsaid=True)), 1)
        self.assertEqual(await self.store.mark_said(GUILD_A, self.c, [self.key(self.candidate)]), 1)
        self.assertEqual(await self.store.mark_said(GUILD_A, self.c, [self.key(self.candidate)]), 0)
        self.assertEqual(await self.store.rows(GUILD_A, self.c, unsaid=True), [])
        self.assertEqual(len(await self.store.rows(GUILD_A, self.c)), 1)

    async def test_rows_can_be_asked_for_by_grade(self) -> None:
        await self.store.save(GUILD_A, self.c, [cg.Graded(self.candidate, cg.MINOR, "small")])
        self.assertEqual(await self.store.rows(GUILD_A, self.c, grade=cg.MAJOR), [])
        self.assertEqual(len(await self.store.rows(GUILD_A, self.c, grade=cg.MINOR)), 1)

    async def test_a_reason_is_one_line_and_cut_only_at_the_column_limit(self) -> None:
        self.assertEqual(cg.one_line("  two\n lines\t here "), "two lines here")
        long = ("word " * 600).strip()
        cut = cg.one_line(long)
        self.assertLessEqual(len(cut), cg.REASON_MAX)
        self.assertTrue(cut.endswith("word"))
        await self.store.save(GUILD_A, self.c, [cg.Graded(self.candidate, cg.MINOR, long)])
        (row,) = await self.store.rows(GUILD_A, self.c)
        self.assertEqual(row.reason, cut)

    async def test_a_candidate_whose_connection_is_gone_is_skipped(self) -> None:
        gone = Candidate(self.candidate.perspective_id, "contradiction", "0" * 32, "1" * 32, True)
        saved = await self.store.save(
            GUILD_A, self.c, [cg.Graded(gone, cg.MAJOR), cg.Graded(self.candidate, cg.MINOR)]
        )
        self.assertEqual((saved, await self.rows_in()), (1, 1))

    async def test_a_grade_goes_with_either_connection(self) -> None:
        await self.store.save(GUILD_A, self.c, [cg.Graded(self.candidate, cg.MAJOR)])
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "DELETE FROM memory_relations WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (GUILD_A, self.c, self.candidate.first_relation_id),
            )
        self.assertEqual(await self.rows_in(), 0)

    async def test_another_campaign_and_another_server_see_nothing(self) -> None:
        await self.store.save(GUILD_A, self.c, [cg.Graded(self.candidate, cg.MAJOR)])
        elsewhere = (await self.campaigns.create(GUILD_A, "Elsewhere", 7)).id
        self.assertEqual(await self.store.rows(GUILD_A, elsewhere), [])
        self.assertEqual(await self.store.rows(GUILD_B, self.c), [])
        # and it cannot be written into another campaign's name
        saved = await self.store.save(GUILD_A, elsewhere, [cg.Graded(self.candidate, cg.MAJOR)])
        self.assertEqual(saved, 0)
        self.assertEqual(
            await self.store.mark_said(GUILD_A, elsewhere, [self.key(self.candidate)]), 0
        )

    async def test_the_reason_limit_is_the_columns_check(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint"
                " WHERE conrelid = 'memory_contradictions'::regclass AND contype = 'c'"
                " AND pg_get_constraintdef(oid) LIKE '%%reason%%'"
            )
            (row,) = await cur.fetchall()
        self.assertIn(f"<= {cg.REASON_MAX})", row["d"])


if __name__ == "__main__":
    unittest.main()
