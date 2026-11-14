"""Migration 0468 (#4173 slice 6d part 2): the waiting connections' key gains a route's means. Run
on a table that holds rows, with the migration held back until they are written (CLAUDE.md, #704):
every row stays, a route's means is its detail, every other kind's is '', and a road and a river
between one pair can then both wait. Invented and published place names only."""

from __future__ import annotations

import unittest
import uuid

from dmbot.campaigns import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.memory.backstory_links import BackstoryLinks, NewLink
from dmbot.memory.models import CONFIRMED
from dmbot.memory.store import MemoryStore
from dmbot.schema import MIGRATIONS
from tests.pg import REQUIRE_DB, TEST_URL

GUILD, DM = 4468, 99
NAME = "0468_waiting_route_means"


class WaitingRouteMeans(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        if not TEST_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_DATABASE_URL is needed")
            self.skipTest("set DMBOT_TEST_DATABASE_URL to test the migration")
        self.schema = f"t_{uuid.uuid4().hex[:16]}"
        self.db = await Database.open(
            TEST_URL,
            schema=self.schema,
            max_size=2,
            migrations=tuple(m for m in MIGRATIONS if m[0] != NAME),
        )
        self.memory = MemoryStore(self.db)
        self.c = (await CampaignStore(self.db).create(GUILD, "Frostmaiden", DM)).id

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)

    async def place(self, name: str) -> str:
        written = await self.memory.add_entity(
            GUILD, self.c, name=name, type="place", status=CONFIRMED, source="dm"
        )
        return str(written.value.id)

    async def rows(self) -> list[tuple[str, str, str]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT predicate, detail, means FROM backstory_links ORDER BY predicate"
            )
            return [(r["predicate"], r["detail"], r["means"]) for r in await cur.fetchall()]

    async def test_the_rows_stay_and_a_second_means_can_then_wait(self) -> None:
        targos, bryn = await self.place("Targos"), await self.place("Bryn Shander")
        async with self.db.guild(GUILD) as conn:  # (rows written before the migration)
            for predicate, detail in (("route_to", "road"), ("next_to", "on the shore of")):
                await conn.execute(
                    "INSERT INTO backstory_links (guild_id, campaign_id, subject_id, predicate,"
                    " object_id, quote, detail, created_at) VALUES (%s, %s, %s, %s, %s, 'q',"
                    " %s, 1)",
                    (GUILD, self.c, targos, predicate, bryn, detail),
                )
        await self.db.migrate(MIGRATIONS)
        self.assertEqual(
            await self.rows(),
            [("next_to", "on the shore of", ""), ("route_to", "road", "road")],
        )
        links = BackstoryLinks(self.db)
        added = await links.add(
            GUILD, self.c,
            [NewLink(targos, "route_to", bryn, "q", detail="river"),
             NewLink(targos, "route_to", bryn, "q", detail="road"),  # (already waiting)
             NewLink(targos, "next_to", bryn, "q", detail="borders")],  # (keys as before)
        )  # fmt: skip
        self.assertEqual(added, 1)
        self.assertEqual(len(await self.rows()), 3)
