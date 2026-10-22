"""Names and connections the story holds back from the players until the table has said them
(#2404, slice 2). A name is "revealed" when the players may know it (not hidden from them); a
connection is revealed when The party believes it. Invented names throughout."""

from __future__ import annotations

import unittest
import uuid
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.memory import beliefs
from dmbot.memory.models import DM, MemoryRuleError, NewName, new_id
from dmbot.schema import MIGRATIONS
from tests.pg import REQUIRE_DB, TEST_URL
from tests.test_memory_store import DM as DM_USER
from tests.test_memory_store import GUILD_A
from tests.test_web_relationships import Web

BEFORE = "0187_unrevealed_connections"


class Held(Web):
    async def seen(self, who: Any = None, **kw: Any) -> dict[str, dict[str, Any]]:
        got = await self.graph(who, center=self.mira, hops=1, **kw)
        return {x["id"]: x for e in got["edges"] for x in e["links"]}

    async def held_link(self) -> str:
        made = await self.memory.add_relation(
            self.g, self.one.id, self.mira, "owns", self.keep, source=DM, confidence=1.0,
            status="confirmed", unrevealed=True,
        )  # fmt: skip
        return made.value[0].id


class TheConnection(Held):
    async def test_a_held_back_connection_is_not_shown_to_players_until_revealed(self) -> None:
        held = await self.held_link()
        self.assertNotIn(held, await self.seen(self.player))
        self.assertNotIn(held, await self.seen(view="players"))
        full = await self.seen(view="full")
        self.assertIn(held, full)
        self.assertFalse(full[held]["revealed"])  # the DM sees it dimmed
        self.assertTrue(full[self.links["mira-keep"]]["revealed"])
        await self.memory.update_relation(self.g, self.one.id, held, source=DM, unrevealed=False)
        shown = await self.seen(self.player)
        self.assertIn(held, shown)
        self.assertTrue(shown[held]["revealed"])

    async def test_undoing_a_reveal_hides_it_again(self) -> None:
        held = await self.held_link()
        shown = await self.memory.update_relation(
            self.g, self.one.id, held, source=DM, unrevealed=False
        )
        self.assertIn(held, await self.seen(self.player))
        await self.memory.undo(self.g, self.one.id, shown.batch, source=DM)  # type: ignore[arg-type]
        self.assertNotIn(held, await self.seen(self.player))

    async def test_a_connection_waits_for_both_of_its_names(self) -> None:
        other = await self.add("Aldous Pell", "npc", hidden_from_players=True)
        made = await self.memory.add_relation(
            self.g, self.one.id, self.mira, "knows", other, source=DM, confidence=1.0,
            status="confirmed", unrevealed=True,
        )  # fmt: skip
        with self.assertRaises(MemoryRuleError):
            await self.memory.update_relation(
                self.g, self.one.id, made.value[0].id, source=DM, unrevealed=False
            )
        await self.memory.set_hidden_from_players(self.g, self.one.id, other, False, source=DM)
        await self.memory.update_relation(
            self.g, self.one.id, made.value[0].id, source=DM, unrevealed=False
        )

    async def test_only_the_dm_holds_one_back_or_shows_it(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_relation(
                self.g, self.one.id, self.mira, "owns", self.keep, source="scan",
                confidence=0.5, unrevealed=True,
            )  # fmt: skip
        held = await self.held_link()
        with self.assertRaises(MemoryRuleError):
            await self.memory.update_relation(
                self.g, self.one.id, held, source="scan", unrevealed=False
            )

    async def test_a_secret_one_is_never_shown_by_a_reveal(self) -> None:
        made = await self.memory.add_relation(
            self.g, self.one.id, self.mira, "owns", self.keep, source=DM, confidence=1.0,
            status="confirmed", unrevealed=True, secret=True,
        )  # fmt: skip
        link = made.value[0].id
        await self.memory.update_relation(self.g, self.one.id, link, source=DM, unrevealed=False)
        self.assertNotIn(link, await self.seen(self.player))  # still 🤫

    async def test_the_wording_of_a_held_back_connection_stays_held_back(self) -> None:
        held = await self.held_link()
        got = await self.memory.change_relation_words(
            self.g, self.one.id, held, "enemy_of", "", source=DM
        )
        new = got.value.id
        self.assertNotIn(new, await self.seen(self.player))


class TheNames(Held):
    async def test_a_list_of_names_starts_unrevealed(self) -> None:
        added = await self.memory.add_names(
            self.g, self.one.id, [NewName("Orla Finch", "npc", "confirmed")], source=DM
        )
        (entity_id,) = added.value
        assert entity_id is not None
        entity = await self.memory.entity(self.g, self.one.id, entity_id)
        assert entity is not None
        self.assertTrue(entity.hidden_from_players)

    async def test_revealing_a_name_shows_it_in_the_web_and_undo_hides_it(self) -> None:
        fresh = await self.add("Orla Finch", "npc", hidden_from_players=True)
        await self.link("knows", self.mira, fresh, "orla")
        names = lambda got: {n["id"]: n for n in got["nodes"]}  # noqa: E731
        theirs = names(await self.graph(self.player, center=self.mira, hops=1))
        self.assertNotIn(fresh, theirs)
        mine = names(await self.graph(center=self.mira, hops=1, view="full"))
        self.assertFalse(mine[fresh]["revealed"])
        shown = await self.memory.set_hidden_from_players(
            self.g, self.one.id, fresh, False, source=DM
        )
        theirs = names(await self.graph(self.player, center=self.mira, hops=1))
        self.assertTrue(theirs[fresh]["revealed"])
        await self.memory.undo(self.g, self.one.id, shown.batch, source=DM)  # type: ignore[arg-type]
        theirs = names(await self.graph(self.player, center=self.mira, hops=1))
        self.assertNotIn(fresh, theirs)


class TheMigration(unittest.IsolatedAsyncioTestCase):
    """0187 on a table that already holds connections: each reads as revealed, as before."""

    async def asyncSetUp(self) -> None:
        if not TEST_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_DATABASE_URL is needed")
            self.skipTest("set DMBOT_TEST_DATABASE_URL to test the migration")
        self.schema = f"t_{uuid.uuid4().hex[:16]}"
        self.before = tuple(m for m in MIGRATIONS if m[0] != BEFORE)
        self.db = await Database.open(
            TEST_URL, schema=self.schema, max_size=2, migrations=self.before
        )

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)

    async def test_existing_connections_read_as_revealed(self) -> None:
        campaign = await CampaignStore(self.db).create(GUILD_A, "Frostmaiden", DM_USER)
        a, b, link = new_id(), new_id(), new_id()
        async with self.db.guild(GUILD_A) as conn:
            for key, name in ((a, "Aaa"), (b, "Bbb")):
                await conn.execute(
                    "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                    " description, status, source, created_at)"
                    " VALUES (%s, %s, %s, 'npc', %s, '', 'confirmed', 'dm', 1)",
                    (GUILD_A, campaign.id, key, name),
                )
            await conn.execute(
                "INSERT INTO memory_relations (guild_id, campaign_id, id, subject_id,"
                " predicate, object_id, detail, confidence, status, source, mention_ids,"
                " secret, created_at, definition_version)"
                " VALUES (%s, %s, %s, %s, 'knows', %s, '', 1.0, 'confirmed', 'dm', '{}', false,"
                " 1, 1)",
                (GUILD_A, campaign.id, link, a, b),
            )
        await self.db.close()
        self.db = await Database.open(
            TEST_URL, schema=self.schema, max_size=2, migrations=tuple(MIGRATIONS)
        )
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT unrevealed FROM memory_relations WHERE id = %s", (link,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertFalse(row["unrevealed"])
        self.assertTrue(callable(beliefs.party_knows))


if __name__ == "__main__":
    unittest.main()
