"""Migration 0169 (#2358): a suggestion written before pronouns were kept out is marked, and one
written after is not. Run on a table that holds a row, with no clock moved."""

from __future__ import annotations

import unittest
import uuid

from dmbot.campaigns import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.memory.models import PROPOSED
from dmbot.memory.store import MemoryStore, hidden_old_pronoun
from dmbot.schema import MIGRATIONS
from tests.pg import REQUIRE_DB, TEST_URL

GUILD, DM = 4242, 99
GATE = "0169_made_before_name_gate"


class TheGateMigration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        if not TEST_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_DATABASE_URL is needed")
            self.skipTest("set DMBOT_TEST_DATABASE_URL to test the migration")
        self.schema = f"t_{uuid.uuid4().hex[:16]}"
        self.before = tuple(m for m in MIGRATIONS if m[0] != GATE)
        self.db = await Database.open(
            TEST_URL, schema=self.schema, max_size=2, migrations=self.before
        )

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)

    async def test_old_rows_are_marked_and_new_ones_are_not(self) -> None:
        campaign = await CampaignStore(self.db).create(GUILD, "Frostmaiden", DM)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name, description,"
                " status, source, created_at) VALUES (%s, %s, %s, 'concept', 'We', '', 'proposed',"
                " 'extractor', 1)",
                (GUILD, campaign.id, "a" * 32),
            )
        await self.db.migrate(MIGRATIONS)  # the gate arrives while the row is there
        memory = MemoryStore(self.db)
        await memory.add_entity(
            GUILD, campaign.id, type="concept", name="They", source="extractor"
        )  # written after: it passed the gate (a cue let it through)
        found = await memory.entities(GUILD, campaign.id, statuses=[PROPOSED])
        self.assertEqual([e.name for e in found], ["They"])  # the old "We" is not offered
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT name, made_before_name_gate AS old FROM memory_entities"
                " WHERE guild_id = %s ORDER BY name",
                (GUILD,),
            )
            rows = {r["name"]: r["old"] for r in await cur.fetchall()}
        self.assertEqual(rows, {"They": False, "We": True})
        old = [e for e in await memory.entities(GUILD, campaign.id, statuses=[PROPOSED])]
        self.assertFalse(any(hidden_old_pronoun(e) for e in old))
