"""Migration 0287 (#2995): today's flags and knowers become `memory_knowers` rows. Run on tables
that hold rows, with the migration held back until they are written (CLAUDE.md, #704). Invented
names throughout."""

from __future__ import annotations

import unittest
import uuid
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.memory import gate
from dmbot.memory.gate import ALIAS, CONNECTION, ENTRY, PROPERTY, Viewer
from dmbot.memory.models import CONFIRMED
from dmbot.memory.store import MemoryStore
from dmbot.schema import MIGRATIONS
from tests.pg import REQUIRE_DB, TEST_URL

GUILD, DM = 4343, 99
MOVE = "0287_knowers_move"
MEMBERS = (1, 2)


class TheMove(unittest.IsolatedAsyncioTestCase):
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
            migrations=tuple(m for m in MIGRATIONS if m[0] != MOVE),
        )
        self.memory = MemoryStore(self.db)
        self.c = (await CampaignStore(self.db).create(GUILD, "Frostmaiden", DM)).id

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)

    async def add(self, name: str, **kw: Any) -> str:
        kw.setdefault("type", "npc")
        kw.setdefault("status", CONFIRMED)
        written = await self.memory.add_entity(GUILD, self.c, name=name, source="dm", **kw)
        return str(written.value.id)

    async def link(self, a: str, b: str, **kw: Any) -> str:
        made = await self.memory.add_relation(
            GUILD, self.c, a, "knows", b, source="dm", confidence=1.0, status=CONFIRMED, **kw
        )
        return str(made.value[0].id)

    async def test_every_old_flag_lands_where_the_rule_says(self) -> None:
        tavin = await self.add(
            "Tavin", type="character", role="player_character", played_by=MEMBERS[0]
        )
        mara = await self.add(
            "Mara", type="character", role="player_character", played_by=MEMBERS[1]
        )
        voss = await self.add("Voss", type="character", role="npc")
        council = await self.add("Lantern Council", type="faction")
        rook = await self.add("Rook", type="character", role="npc")
        await self.memory.set_hidden_from_players(GUILD, self.c, rook, True, source="dm")
        seen = await self.link(voss, council)  # The party knows it
        secret = await self.link(rook, council, secret=True)
        known_by_tavin = await self.link(voss, rook, secret=True)
        await self.memory.set_connection_knowers(
            GUILD, self.c, known_by_tavin, [tavin], source="dm"
        )
        shade = await self.memory.add_alias(
            GUILD, self.c, voss, "Shade", kind="title", secret=True, source="dm"
        )
        await self.memory.set_alias_knowers(GUILD, self.c, shade.value.id, [mara], source="dm")
        plain = await self.memory.add_alias(
            GUILD, self.c, voss, "Old Voss", kind="nickname", source="dm", status=CONFIRMED
        )
        await self.memory.set_condition(GUILD, self.c, voss, "dead", source="dm")
        hidden_state = await self.memory.set_condition(GUILD, self.c, rook, "dead", source="dm")

        await self.db.migrate(MIGRATIONS)  # the move arrives while the rows are there

        party = Viewer.the_party()
        table = await self.memory.visible_to(GUILD, self.c, party)
        self.assertTrue(table.knows(ENTRY, voss))  # revealed: every player character, at 1.0
        self.assertTrue(table.knows(ENTRY, council))
        self.assertEqual(table.how_sure(CONNECTION, seen), 1.0)
        self.assertTrue(table.knows(ALIAS, plain.value.id))
        self.assertFalse(table.knows(ENTRY, rook))  # hidden: nobody
        self.assertFalse(table.knows(CONNECTION, secret))  # secret: nobody
        self.assertFalse(table.knows(CONNECTION, known_by_tavin))  # (not every character)
        self.assertFalse(table.knows(ALIAS, shade.value.id))
        self.assertTrue(
            (await self.memory.visible_to(GUILD, self.c, Viewer.character(tavin))).knows(
                CONNECTION, known_by_tavin
            )
        )  # today's knowers carry over
        self.assertFalse(
            (await self.memory.visible_to(GUILD, self.c, Viewer.character(mara))).knows(
                CONNECTION, known_by_tavin
            )
        )
        self.assertTrue(
            (await self.memory.visible_to(GUILD, self.c, Viewer.character(mara))).knows(
                ALIAS, shade.value.id
            )
        )
        own = await self.memory.visible_to(GUILD, self.c, Viewer.character(rook))
        self.assertTrue(own.knows(ENTRY, rook))  # a character knows that it exists itself
        self.assertFalse(own.knows(ENTRY, voss) and False)
        self.assertEqual(own.how_sure(ENTRY, rook), 1.0)
        everyone = await self.memory.visible_to(GUILD, self.c, Viewer.player([tavin]))
        state_ids = [s.id for s in await self._states(voss)]
        self.assertTrue(all(everyone.knows(PROPERTY, i) for i in state_ids))
        hidden_ids = [s.id for s in await self._states(rook)]
        self.assertFalse(any(table.knows(PROPERTY, i) for i in hidden_ids))
        self.assertIsNotNone(hidden_state)
        self.assertIsNotNone(gate)

    async def _states(self, entity_id: str) -> list[Any]:
        found = await self.memory.states(GUILD, self.c, [entity_id])
        return [found[entity_id]] if entity_id in found else []

    async def test_a_limited_clue_goes_to_the_characters_who_know_it(self) -> None:
        mara = await self.add(
            "Mara", type="character", role="player_character", played_by=MEMBERS[1]
        )
        await self.add("Tavin", type="character", role="player_character", played_by=MEMBERS[0])
        clue = await self.add("A torn map", type="clue")
        await self.link(mara, clue)  # "knows"
        await self.memory.set_audience_limited(GUILD, self.c, clue, True, source="dm")
        await self.db.migrate(MIGRATIONS)
        table = await self.memory.visible_to(GUILD, self.c, Viewer.the_party())
        self.assertFalse(table.knows(ENTRY, clue))  # not the whole party
        got = await self.memory.visible_to(GUILD, self.c, Viewer.character(mara))
        self.assertTrue(got.knows(ENTRY, clue))

    async def test_another_campaign_is_untouched_by_the_rows_of_this_one(self) -> None:
        await self.add("Voss", type="character", role="npc")
        other = (await CampaignStore(self.db).create(GUILD, "Elsewhere", DM)).id
        await self.db.migrate(MIGRATIONS)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_knowers WHERE campaign_id = %s", (other,)
            )
            row = await cur.fetchone()
        self.assertEqual(row["n"], 0)
