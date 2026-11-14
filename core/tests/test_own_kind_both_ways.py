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
    # every migration but this one runs at set-up (later ones too, as the store writes their
    # columns: test_waiting_route_means_migration does the same)
    NAME = ""

    async def asyncSetUp(self) -> None:
        if not TEST_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_DATABASE_URL is needed")
            self.skipTest("set DMBOT_TEST_DATABASE_URL to test the migration")
        self.schema = f"t_{uuid.uuid4().hex[:16]}"
        self.db = await Database.open(
            TEST_URL, schema=self.schema, max_size=2,
            migrations=tuple(m for m in MIGRATIONS if m[0] != self.NAME),
        )  # fmt: skip
        self.c = (await CampaignStore(self.db).create(GUILD_A, "Frostmaiden", DM)).id
        self.memory = MemoryStore(self.db)
        self.batches: dict[str, int | None] = {}

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)

    async def person(self, name: str) -> str:
        made = await self.memory.add_entity(
            GUILD_A, self.c, type="character", name=name, source="dm", status="confirmed"
        )
        return made.value.id

    async def tie(self, predicate: str, a: str, b: str, campaign: str = "") -> str:
        made = await self.memory.add_relation(
            GUILD_A, campaign or self.c, a, predicate, b, source="dm", status="confirmed",
            confidence=1.0,
        )  # fmt: skip
        self.batches[made.value[0].id] = made.batch
        return made.value[0].id

    async def as_before(self, *ids: str) -> None:
        """These rows as they were made before #4388: no mark, on the row and in the change log."""
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_relations SET both_ways = NULL WHERE id = ANY(%s)", (list(ids),)
            )
            await conn.execute(
                "UPDATE memory_changes SET after = jsonb_set(after, '{both_ways}', 'null')"
                " WHERE table_name = 'memory_relations' AND row_id = ANY(%s)"
                " AND after IS NOT NULL",
                (list(ids),),
            )

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
        sworn = await self.tie("sworn_to", cerric, wit)
        # another campaign whose own kind of that name reads one way: its rows stay as they are
        other = (await CampaignStore(self.db).create(GUILD_A, "Elsewhere", DM)).id
        await self.memory.add_predicate(
            GUILD_A, other, PredicateTerm(OATH.key, OATH.label, OATH.description,
                                          OATH.subject_types, OATH.object_types, core=False),
            examples=["Cerric is oath-bound to Wit."], reason="the table uses it", source="dm",
        )  # fmt: skip
        a, b = [
            (await self.memory.add_entity(
                GUILD_A, other, type="character", name=n, source="dm", status="confirmed"
            )).value.id
            for n in ("Anna", "Bo")
        ]  # fmt: skip
        elsewhere = await self.tie("oath_kin_of", a, b, campaign=other)
        await self.as_before(old, elsewhere, sworn)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(  # (the DM's one-way choice, and the campaign's own old
                # `sworn_to`, from before the core took the name, marked two way in its table)
                "UPDATE memory_relations SET both_ways = false WHERE id = %s",
                (set_one_way,),
            )
            await conn.execute(
                "INSERT INTO memory_predicates (guild_id, campaign_id, key, label, description,"
                " examples, reason, subject_types, object_types, is_symmetric, conflicts_with,"
                " status, replaced_by, created_at) VALUES (%s, %s, 'sworn_to', 'is sworn to',"
                " 'An old own kind.', '{}', 'old', '{character}', '{character}', true, '{}',"
                " 'deprecated', 'sworn_to', 1)",
                (GUILD_A, self.c),
            )
        await self.db.migrate(MIGRATIONS)
        marks = await self.marks()
        self.assertEqual(
            {k: marks[k] for k in (old, set_one_way, rivals, sworn)},
            {old: True, set_one_way: False, rivals: None, sworn: None},
        )  # (the DM's one-way row, a core kind's, and a name the core has taken: left alone)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT both_ways FROM memory_relations WHERE id = %s", (elsewhere,)
            )
            found = await cur.fetchone()
        assert found is not None
        self.assertIsNone(found["both_ways"])
        batch = self.batches[old]
        assert batch is not None
        await self.memory.undo(GUILD_A, self.c, batch)  # (its change log was marked too)
        self.assertNotIn(old, await self.marks())


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
