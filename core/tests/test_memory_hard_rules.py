"""Hard rules and warnings (#2663): a structural rule refuses the write even when two writers race,
a warning still writes and flags, and nothing is compared across campaigns. Needs the test
database; invented names throughout."""

from __future__ import annotations

import asyncio
import re
import unittest
from pathlib import Path

from psycopg import errors as pg_errors

from dmbot.memory import checks
from dmbot.memory.checks import FLAG_KINDS, HELD_TWICE
from dmbot.memory.models import MemoryRuleError
from tests.test_memory_store import DM, GUILD_A, ConfirmedMemoryTest


class TheList(unittest.TestCase):
    def test_every_warning_kind_is_named_in_the_list_and_each_rule_says_which_it_is(self) -> None:
        doc = checks.__doc__ or ""
        self.assertIn("Structural: never broken", doc)
        self.assertIn("A warning: flagged for the DM, never refused", doc)
        self.assertIn("Safe when two writes race", doc)
        # a new flag kind is a new warning: it is listed in FLAG_KINDS, which the docstring points
        # at, and the story-memory page says in words what a warning is
        self.assertIn("`FLAG_KINDS`", doc)
        page = (Path(__file__).parents[2] / "docs" / "STORY_MEMORY.md").read_text()
        self.assertRegex(page, re.compile(r"\*\*Hard rules and warnings\*\*"))
        self.assertEqual(len(FLAG_KINDS), len(set(FLAG_KINDS)))


class StructuralRaces(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.clue = await self.add("The torn map", type="clue")
        self.event = await self.add("The ambush", type="event")
        self.quest = await self.add("The Missing Heir", type="quest")

    async def led(self, a: str, b: str) -> None:
        await self.memory.led_to(GUILD_A, self.c, a, b, source="dm")

    async def test_two_links_that_would_close_a_loop_together_let_only_one_in(self) -> None:
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
        links = range(row["n"])
        self.assertEqual(len(links), 1)  # never both: that would be a loop

    async def test_the_same_fact_said_twice_at_once_is_stored_once(self) -> None:
        mira, keep = await self.add("Mira"), await self.add("The Keep", type="place")
        await asyncio.gather(*(self.relate(mira, "knows", keep) for _ in range(4)))
        knows = [r for r in await self.memory.relations(GUILD_A, self.c) if r.predicate == "knows"]
        self.assertEqual(len(knows), 1)

    async def test_a_write_waits_for_the_campaigns_lock_so_a_check_cannot_be_overtaken(
        self,
    ) -> None:
        """The race tests above can pass by luck of timing; this one cannot. While another writer
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
                "SELECT query FROM pg_stat_activity"
                " WHERE wait_event_type = 'Lock' AND datname = current_database()"
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
        mira = await self.add("Mira")
        snot = await self.add("Snot", campaign=other)
        first = await self.relate(mira, "carries", mine)
        second = await self.memory.add_relation(
            GUILD_A, other, snot, "carries", theirs, source="dm", confidence=1.0
        )
        self.assertEqual(first.value[1], [])
        self.assertEqual(second.value[1], [])  # one holder in each campaign: nothing to flag
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM memory_flags")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)


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
