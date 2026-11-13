"""Only a character is linked to the rules (#4241): a kind change or a merge into anything else
takes the links away (Undo gives them back), a restore leaves out one a backup still holds, and
migration 0449 removes the ones stored before. Invented names throughout."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.db import Database, drop_schema
from dmbot.memory.backup import MemorySection
from dmbot.memory.ontology import TypeTerm
from dmbot.memory.store import MemoryStore
from dmbot.schema import MIGRATIONS
from tests.pg import REQUIRE_DB, TEST_URL
from tests.test_memory_store import DM, GUILD_A, GUILD_B, MemoryTest

NAME = "0449_rule_links_on_characters"


class KindChanges(MemoryTest):
    async def test_a_character_made_a_place_loses_its_links_and_its_backup_loads(self) -> None:
        npc = await self.add("Brewner")
        await self.memory.set_rule_link(GUILD_A, self.c, npc, "class", "Fighter", source="dm")
        changed = await self.memory.set_entity_type(GUILD_A, self.c, npc, "place", source="dm")
        self.assertEqual(await self.memory.rule_links(GUILD_A, self.c, [npc]), [])
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        assert changed.batch is not None
        await self.memory.undo(GUILD_A, self.c, changed.batch, source="dm")
        (back,) = await self.memory.rule_links(GUILD_A, self.c, [npc])
        self.assertEqual(back.name, "Fighter")

    async def test_a_merge_into_a_place_leaves_the_links_behind(self) -> None:
        npc = await self.add("Brewner")
        place = await self.add("The Brewery", type="place")
        await self.memory.set_rule_link(GUILD_A, self.c, npc, "class", "Fighter", source="dm")
        await self.memory.merge(GUILD_A, self.c, place, npc, source="dm", dm_said_same=True)
        self.assertEqual(await self.memory.rule_links(GUILD_A, self.c, [place]), [])
        (stayed,) = await self.memory.rule_links(GUILD_A, self.c, [npc])  # behind, unseen
        self.assertEqual(stayed.name, "Fighter")
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)

    async def test_a_backup_holding_one_on_a_place_loads_without_it(self) -> None:
        npc = await self.add("Brewner")
        await self.memory.set_rule_link(GUILD_A, self.c, npc, "class", "Fighter", source="dm")
        data = decode_backup(encode_backup(await self.campaigns.export(GUILD_A, self.c)))
        assert isinstance(data, dict)
        for row in data["sections"]["memory"]:
            if row["table"] == "entity" and row["id"] == npc:
                row.update(type="place", role=None)  # (as an older build left it)
        with self.assertLogs("dmbot.memory.backup", logging.INFO) as logs:
            restored = await self.campaigns.import_backup(GUILD_B, data, DM)
        self.assertIn("left out 1 rules links", " ".join(logs.output))
        self.assertEqual(await self.memory.rule_links(GUILD_B, restored.id), [])

    async def test_a_link_to_an_entry_the_file_lacks_is_still_damage(self) -> None:
        npc = await self.add("Brewner")
        await self.memory.set_rule_link(GUILD_A, self.c, npc, "class", "Fighter", source="dm")
        data = decode_backup(encode_backup(await self.campaigns.export(GUILD_A, self.c)))
        assert isinstance(data, dict)
        for row in data["sections"]["memory"]:
            if row["table"] == "rule_link":
                row["entity_id"] = "f" * 32
        with self.assertRaisesRegex(Exception, "damaged"):
            await self.campaigns.import_backup(GUILD_B, data, DM)


class TheMigration(MemoryTest):
    """0449 run on tables holding rows, held back until they are written (CLAUDE.md, #704)."""

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
        self.now = 1_700_000_000
        self.campaigns = CampaignStore(self.db, clock=lambda: self.now)
        self.campaigns.register_section(MemorySection())
        self.memory = MemoryStore(self.db, clock=lambda: self.now)
        self.c = (await self.campaigns.create(GUILD_A, "Frozen Wastes", DM)).id

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)

    async def kind(self, campaign: str, key: str, parent: str) -> None:
        await self.memory.add_type(
            GUILD_A,
            campaign,
            TypeTerm(key, parent, key, f"A {key}.", core=False),
            examples=["x"],
            reason="ours",
            source="entitybot",
        )

    async def linked(self, name: str, *, campaign: str | None = None, **kw: Any) -> str:
        who = await self.add(name, campaign=campaign, **kw)
        await self.memory.set_rule_link(
            GUILD_A, campaign or self.c, who, "class", "Fighter", source="dm"
        )
        return who

    async def retype(self, entity_id: str, type_key: str) -> None:
        """A kind changed under a link, as an older build could leave it."""
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_entities SET type = %s, role = NULL WHERE id = %s",
                (type_key, entity_id),
            )

    async def test_only_the_links_on_non_characters_go(self) -> None:
        # this campaign: hireling under character, porter under hireling (two levels down)
        await self.kind(self.c, "hireling", "character")
        await self.kind(self.c, "porter", "hireling")
        npc = await self.linked("Brewner")
        hired = await self.linked("Tom", type="hireling")
        porter = await self.linked("Pip", type="porter")
        gone = await self.linked("The Brewery")
        await self.retype(gone, "place")
        # another campaign: the same key "hireling" is a place there, so its link goes
        other = (await self.campaigns.create(GUILD_A, "Elsewhere", DM)).id
        await self.kind(other, "hireling", "place")
        theirs = await self.linked("Old Mill", campaign=other)
        await self.retype(theirs, "hireling")
        # a third campaign with no kinds of its own: a character keeps its link
        plain = (await self.campaigns.create(GUILD_A, "Plain", DM)).id
        kept = await self.linked("Grik", campaign=plain)
        await self.db.migrate(MIGRATIONS)
        left = {link.entity_id for link in await self.memory.rule_links(GUILD_A, self.c)}
        self.assertEqual(left, {npc, hired, porter})
        self.assertEqual(await self.memory.rule_links(GUILD_A, other), [])
        mine = await self.memory.rule_links(GUILD_A, plain)
        self.assertEqual([x.entity_id for x in mine], [kept])
