"""A campaign's own two-way connection kind: every row says so (#4388). New rows get the mark from
`add_relation_in`; migration 0485 gives it to the rows made before, on real rows behind forced
row-level security (CLAUDE.md, #704). A row the DM set, and a core kind's, are left alone. Invented
names."""

from __future__ import annotations

import unittest
import uuid

from dmbot.campaigns import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.memory.ontology import PredicateTerm
from dmbot.memory.store import MemoryStore
from dmbot.schema import MIGRATIONS
from tests.pg import REQUIRE_DB, TEST_URL
from tests.test_memory_store import DM, GUILD_A

OATH = PredicateTerm(
    "oath_kin_of", "is oath-bound to", "Two sworn to each other.", ("character",), ("character",),
    symmetric=True, core=False,
)  # fmt: skip


class _Campaign(unittest.IsolatedAsyncioTestCase):  # (no tests of its own)
    NAME = ""  # migrations before this one run at set-up

    async def asyncSetUp(self) -> None:
        if not TEST_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_DATABASE_URL is needed")
            self.skipTest("set DMBOT_TEST_DATABASE_URL to test the migration")
        self.schema = f"t_{uuid.uuid4().hex[:16]}"
        self.db = await Database.open(
            TEST_URL, schema=self.schema, max_size=2,
            migrations=tuple(m for m in MIGRATIONS if m[0] < self.NAME),
        )  # fmt: skip
        self.c = (await CampaignStore(self.db).create(GUILD_A, "Frostmaiden", DM)).id
        self.memory = MemoryStore(self.db)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)

    async def person(self, name: str) -> str:
        made = await self.memory.add_entity(
            GUILD_A, self.c, type="character", name=name, source="dm", status="confirmed"
        )
        return made.value.id

    async def tie(self, predicate: str, a: str, b: str) -> str:
        made = await self.memory.add_relation(
            GUILD_A, self.c, a, predicate, b, source="dm", status="confirmed", confidence=1.0
        )
        return made.value[0].id

    async def marks(self) -> dict[str, bool | None]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT id, both_ways FROM memory_relations WHERE campaign_id = %s", (self.c,)
            )
            return {r["id"]: r["both_ways"] for r in await cur.fetchall()}


class TheMigration(_Campaign):
    NAME = "0485_own_kind_both_ways"

    async def test_an_older_row_of_a_two_way_own_kind_is_marked_two_way(self) -> None:
        await self.memory.add_predicate(
            GUILD_A, self.c, OATH, examples=["Cerric is oath-bound to Wit."],
            reason="the table uses it", source="dm",
        )  # fmt: skip
        cerric, wit, mira = [await self.person(n) for n in ("Cerric", "Wit", "Mira")]
        old = await self.tie("oath_kin_of", cerric, wit)
        set_one_way = await self.tie("oath_kin_of", cerric, mira)
        rivals = await self.tie("rival_of", wit, mira)
        async with self.db.guild(GUILD_A) as conn:  # (as rows made before this change were)
            await conn.execute(
                "UPDATE memory_relations SET both_ways = CASE WHEN id = %s THEN false END"
                " WHERE campaign_id = %s",
                (set_one_way, self.c),
            )
        await self.db.migrate(MIGRATIONS)
        self.assertEqual(
            await self.marks(), {old: True, set_one_way: False, rivals: None}
        )  # (the DM's one-way row and a core kind's are left alone)


class NewRows(_Campaign):
    NAME = "9999_after_all"  # (every migration: today's schema)

    async def test_a_new_row_of_a_two_way_own_kind_carries_the_mark(self) -> None:
        await self.memory.add_predicate(
            GUILD_A, self.c, OATH, examples=["Cerric is oath-bound to Wit."],
            reason="the table uses it", source="dm",
        )  # fmt: skip
        cerric, wit = [await self.person(n) for n in ("Cerric", "Wit")]
        made = await self.tie("oath_kin_of", cerric, wit)
        rivals = await self.tie("rival_of", cerric, wit)
        self.assertEqual(await self.marks(), {made: True, rivals: None})
