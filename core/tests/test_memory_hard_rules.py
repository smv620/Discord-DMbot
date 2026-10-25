"""Hard rules and warnings (#2663): a structural rule refuses the write even when two writers race,
a warning still writes and flags, and nothing is compared across campaigns. Needs the test
database; invented names throughout."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from psycopg import errors as pg_errors

from dmbot.memory import checks
from dmbot.memory.checks import FLAG_KINDS, HELD_TWICE
from dmbot.memory.models import MemoryRuleError
from tests.test_memory_store import DM, GUILD_A, ConfirmedMemoryTest


class TheList(unittest.TestCase):
    def test_the_rules_are_written_down_as_structural_or_warning(self) -> None:
        doc = checks.__doc__ or ""
        for heading in (
            "Structural: never broken",
            "A warning: flagged for the DM, never refused",
            "Safe when two writes race",
            "`FLAG_KINDS`",
        ):
            self.assertIn(heading, doc)
        # a flag kind is a warning, and nothing is both: no warning kind is a structural rule's
        self.assertEqual(len(FLAG_KINDS), len(set(FLAG_KINDS)))
        self.assertIn(HELD_TWICE, FLAG_KINDS)  # the owner's example of a warning

    def test_the_story_memory_page_says_the_same(self) -> None:
        page = Path(__file__).parents[2] / "docs" / "STORY_MEMORY.md"
        if not page.exists():  # (a core-only checkout has no docs folder)
            self.skipTest("no docs folder here")
        self.assertIn("**Hard rules and warnings**", page.read_text())


class StructuralRaces(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.event = await self.add("The ambush", type="event")
        self.quest = await self.add("The Missing Heir", type="quest")

    async def led(self, a: str, b: str) -> None:
        await self.memory.led_to(GUILD_A, self.c, a, b, source="dm")

    async def test_two_links_that_would_close_a_loop_together_let_only_one_in(self) -> None:
        # (Whether the two overlap is up to timing, so this passes by luck without the lock; the
        # lock-wait test below is the deterministic proof that they cannot.)
        results = await asyncio.gather(
            self.led(self.event, self.quest),
            self.led(self.quest, self.event),
            return_exceptions=True,
        )
        refused = [r for r in results if isinstance(r, MemoryRuleError)]
        written = [r for r in results if not isinstance(r, Exception)]
        self.assertEqual((len(written), len(refused)), (1, 1), results)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_relations WHERE predicate = 'led_to'"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 1)  # never both: that would be a loop

    async def test_a_write_waits_for_the_campaigns_lock_so_a_check_cannot_be_overtaken(
        self,
    ) -> None:
        """The race test above can pass by luck of timing; this one cannot. While another writer
        holds the campaign's row lock, a new write waits (it does not read and write meanwhile)."""
        mira, rook = await self.add("Mira"), await self.add("Rook")
        async with self.db.guild(GUILD_A) as holder:
            await holder.execute(
                "SELECT 1 FROM campaigns WHERE guild_id = %s AND id = %s FOR NO KEY UPDATE",
                (GUILD_A, self.c),
            )
            write = asyncio.ensure_future(self.relate(mira, "knows", rook))
            await asyncio.sleep(0.5)
            self.assertFalse(write.done())  # waiting on the lock, not done
            # and it waits at its first statement, before it reads anything to check
            cur = await holder.execute(
                "SELECT query FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
                " AND datname = current_database() AND pid <> pg_backend_pid()"
            )
            waiting = [r["query"] for r in await cur.fetchall()]
            self.assertTrue(any("SELECT memory_version" in q for q in waiting), waiting)
        await asyncio.wait_for(write, 10)  # the holder let go: it goes through


class WarningsStillWrite(ConfirmedMemoryTest):
    async def test_a_second_holder_of_a_one_of_a_kind_item_is_written_and_flagged(self) -> None:
        item = await self.add("Heartseeker", type="item")
        mira, snot = await self.add("Mira"), await self.add("Snot")
        await self.relate(mira, "carries", item)
        second = await self.relate(snot, "carries", item)
        self.assertEqual([f.kind for f in second.value[1]], [HELD_TWICE])
        carries = [
            r for r in await self.memory.relations(GUILD_A, self.c) if r.predicate == "carries"
        ]
        self.assertEqual(len(carries), 2)  # both stand: only the DM decides which is true

    async def test_a_same_named_item_in_another_campaign_raises_nothing(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Sunken Coast", DM)).id
        mine = await self.add("Heartseeker", type="item")
        theirs = await self.add("Heartseeker", type="item", campaign=other)
        for campaign, item in ((self.c, mine), (other, theirs)):  # both one-of-a-kind
            await self.memory.set_unique(GUILD_A, campaign, item, True, source="dm")
        mira, snot = await self.add("Mira"), await self.add("Snot", campaign=other)
        # one holder in each campaign, then a second holder in this one: only this one is flagged
        await self.memory.add_relation(
            GUILD_A, other, snot, "carries", theirs, source="dm", confidence=1.0
        )
        first = await self.relate(mira, "carries", mine)
        self.assertEqual(first.value[1], [])  # the other campaign's holder is nobody here
        rook = await self.add("Rook")
        second = await self.relate(rook, "carries", mine)
        self.assertEqual([f.kind for f in second.value[1]], [HELD_TWICE])
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT campaign_id, count(*) AS n FROM memory_flags GROUP BY campaign_id"
            )
            counts = {r["campaign_id"]: r["n"] for r in await cur.fetchall()}
        self.assertEqual(counts, {self.c: 1})  # nothing at all in the other campaign


class TheDatabaseKeepsACampaignApart(ConfirmedMemoryTest):
    async def test_a_belief_cannot_point_at_a_connection_in_another_campaign(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Sunken Coast", DM)).id
        a, b = await self.add("Mira"), await self.add("Rook")
        made = await self.relate(a, "knows", b)
        relation = made.value[0].id
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT id FROM memory_perspectives WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = 'truth'",
                (GUILD_A, other),
            )
            row = await cur.fetchone()
            assert row is not None
            with self.assertRaises(pg_errors.ForeignKeyViolation):
                await conn.execute(
                    "INSERT INTO memory_beliefs (guild_id, campaign_id, id, perspective_id,"
                    " relation_id, confidence, status, seq, recorded_at)"
                    " VALUES (%s, %s, %s, %s, %s, 0.5, 'theory', 1, 1)",
                    (GUILD_A, other, "a" * 32, row["id"], relation),  # the relation is in the other
                )


if __name__ == "__main__":
    unittest.main()
