"""Common knowledge (#4537, docs/design/party_knowledge.md §4, slice 1): the "everyone" view.

A row it holds is known to every character, player character or NPC, now or later, unless they have
a row of their own; the gate counts it only while the statement's own flags show it (a suggestion
once confirmed; the DM's hide masks it without deleting it). Who knows this never takes it off, the
#4524 guard counts it as public, the party's fast path counts it, and migration 0513 gives a row to
each name stored from a website upload read as World general knowledge. Postgres. Invented names."""

from __future__ import annotations

import unittest
import uuid
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.db import Database, drop_schema
from dmbot.memory import gate, public_guard
from dmbot.memory.gate import COMMON, ENTRY, Statements, Viewer
from dmbot.memory.models import CONFIRMED, DM, PROPOSED
from dmbot.memory.store import MemoryStore
from dmbot.schema import MIGRATIONS
from tests.pg import REQUIRE_DB, TEST_URL
from tests.test_memory_store import DM as DM_USER
from tests.test_memory_store import GUILD_A, GUILD_B, MemoryTest

PLAYER = 4242


class TheRule(unittest.TestCase):
    """The gate's rule, without a database."""

    def test_everyone_reaches_every_character_and_their_own_row_wins(self) -> None:
        data = Statements(
            knowers={(ENTRY, "town"): {COMMON: 1.0}, (ENTRY, "rumour"): {COMMON: 1.0, "ilsa": 0.0}},
            player_characters=frozenset({"ilsa", "bram"}),
        )
        party = gate.visible(Viewer.the_party(), data)
        self.assertTrue(party.knows(ENTRY, "town"))
        npc = gate.visible(Viewer.character("innkeeper"), data)  # (an NPC too, and anyone later)
        self.assertTrue(npc.knows(ENTRY, "town"))
        self.assertEqual(
            gate.visible(Viewer.character("ilsa"), data).how_sure(ENTRY, "rumour"), 0.0
        )
        self.assertEqual(gate.knowers_of(data)(ENTRY, "town"), {"ilsa", "bram"})


class CommonKnowledge(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.hero = await self.add(
            "Ilsa", type="character", role="player_character", played_by=PLAYER, status=CONFIRMED
        )
        self.npc = await self.add("Mira", type="character", status=CONFIRMED)
        self.town = await self.add("Bryn Shander", type="place", status=CONFIRMED)

    async def sees(self, viewer: Viewer, entity_id: str) -> bool:
        return (await self.memory.visible_to(GUILD_A, self.c, viewer)).knows(ENTRY, entity_id)

    async def test_a_common_name_is_known_to_everyone_and_the_dms_hide_masks_it(self) -> None:
        written = await self.memory.add_common_knowledge(
            GUILD_A, self.c, "entry", [self.town], source=DM
        )
        self.assertEqual(written.value, 1)
        self.assertTrue(await self.sees(Viewer.character(self.npc), self.town))
        self.assertTrue(await self.sees(Viewer.the_party(), self.town))
        async with self.memory._read(GUILD_A, self.c) as scope:
            self.assertIn(self.town, await gate.party_entries(scope) or ())
        await self.memory.set_hidden_from_players(
            GUILD_A, self.c, self.town, True, source=DM, dm_chose_secret=True
        )
        self.assertFalse(await self.sees(Viewer.character(self.npc), self.town))
        await self.memory.set_hidden_from_players(
            GUILD_A, self.c, self.town, False, source=DM, dm_chose_secret=True
        )
        self.assertTrue(await self.sees(Viewer.character(self.npc), self.town))  # (row kept)

    async def test_a_suggestion_counts_once_the_dm_confirms_it(self) -> None:
        rumour = await self.add("Ten-Towns", type="place", status=PROPOSED)
        await self.memory.add_common_knowledge(GUILD_A, self.c, "entry", [rumour], source=DM)
        self.assertFalse(await self.sees(Viewer.character(self.npc), rumour))
        await self.memory.confirm_entity(GUILD_A, self.c, rumour, "place", source=DM)
        self.assertTrue(await self.sees(Viewer.character(self.npc), rumour))

    async def test_only_adds_and_the_dms_own_list_replaces_it(self) -> None:
        await self.memory.add_common_knowledge(
            GUILD_A, self.c, "entry", [self.town], 0.75, source=DM
        )
        again = await self.memory.add_common_knowledge(
            GUILD_A, self.c, "entry", [self.town], 0.6, source=DM
        )
        self.assertEqual(again.value, 0)  # (never made less sure)
        # (the DM's list shows every player character for common knowledge, never "only the DM")
        listed = await self.memory.statement_knowers(GUILD_A, self.c, "entry", [self.town])
        self.assertEqual(listed, {self.town: [self.hero]})
        # (a write that is not the DM's own choice keeps it: public stays public, #4524)
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, "entry", self.town, [self.hero], source=DM, dm_chose_secret=False
        )
        self.assertTrue(await self.sees(Viewer.character(self.npc), self.town))
        # (the DM's own Who knows this says exactly who knows it: common knowledge goes)
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, "entry", self.town, [self.hero], source=DM, dm_chose_secret=True
        )
        self.assertFalse(await self.sees(Viewer.character(self.npc), self.town))
        self.assertTrue(await self.sees(Viewer.character(self.hero), self.town))

    async def test_the_guard_goes_by_what_the_players_are_shown(self) -> None:
        await self.memory.add_common_knowledge(GUILD_A, self.c, "entry", [self.town], source=DM)
        await self.memory.set_hidden_from_players(
            GUILD_A, self.c, self.town, True, source=DM, dm_chose_secret=True
        )
        async with self.memory._write(GUILD_A, self.c, DM) as w:  # (hidden: the gate shows nobody)
            self.assertFalse(await public_guard.known_to_players(w, "memory_entities", self.town))

    async def test_a_backup_carries_common_knowledge(self) -> None:
        await self.memory.add_common_knowledge(GUILD_A, self.c, "entry", [self.town], source=DM)
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM_USER)
        names = {e.name: e.id for e in await self.memory.entities(GUILD_B, restored.id)}
        seen = await self.memory.visible_to(GUILD_B, restored.id, Viewer.character(names["Mira"]))
        self.assertTrue(seen.knows(ENTRY, names["Bryn Shander"]))


GUILD, DM_ID = 4537, 99
NAME = "0513_common_knowledge"


class TheMove(unittest.IsolatedAsyncioTestCase):
    """0513 on tables that hold rows (CLAUDE.md, #704): only a name from a website upload read as
    World general knowledge gets a row; a spoilers upload's name does not."""

    async def asyncSetUp(self) -> None:
        if not TEST_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_DATABASE_URL is needed")
            self.skipTest("set DMBOT_TEST_DATABASE_URL to test the migration")
        self.schema = f"t_{uuid.uuid4().hex[:16]}"
        self.db = await Database.open(
            TEST_URL, schema=self.schema, max_size=2,
            migrations=tuple(m for m in MIGRATIONS if m[0] != NAME),
        )  # fmt: skip
        self.memory = MemoryStore(self.db)
        self.c = (await CampaignStore(self.db).create(GUILD, "Frostmaiden", DM_ID)).id

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)

    async def add(self, name: str, **kw: Any) -> str:
        kw.setdefault("type", "place")
        kw.setdefault("status", CONFIRMED)
        written = await self.memory.add_entity(GUILD, self.c, name=name, source="dm", **kw)
        return str(written.value.id)

    async def upload(self, key: str, choice: str) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO lore_uploads (guild_id, campaign_id, id, user_id, source, choice,"
                " rights_by, rights_at, created_at, updated_at, state) VALUES (%s, %s, %s, %s,"
                " 'paste', %s, %s, 1, 1, 1, 'done')",
                (GUILD, self.c, key, DM_ID, choice, DM_ID),
            )

    async def test_a_world_uploads_names_become_common_knowledge(self) -> None:
        town = await self.add("Bryn Shander")
        secret = await self.add("Xardorok's forge")
        npc = await self.add("Mira", type="character")
        world, spoilers = uuid.uuid4().hex, uuid.uuid4().hex
        await self.upload(world, "world")
        await self.upload(spoilers, "spoilers")
        await self.memory.record_name_sources(GUILD, self.c, [town], upload_key=world, label="a")
        await self.memory.record_name_sources(
            GUILD, self.c, [secret], upload_key=spoilers, label="b"
        )
        await self.db.migrate(MIGRATIONS)
        seen = await self.memory.visible_to(GUILD, self.c, Viewer.character(npc))
        self.assertTrue(seen.knows(ENTRY, town))
        self.assertFalse(seen.knows(ENTRY, secret))
        async with self.db.guild(GUILD) as conn:  # (a second run adds nothing)
            await conn.execute("DELETE FROM schema_migrations WHERE name = %s", (NAME,))
        await self.db.migrate(MIGRATIONS)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_knowers k JOIN memory_perspectives p"
                " ON p.guild_id = k.guild_id AND p.campaign_id = k.campaign_id"
                " AND p.id = k.perspective_id WHERE p.kind = 'everyone'"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 1)
