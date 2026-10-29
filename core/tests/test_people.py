"""The table's people as entries (#1188 part 1): a player gets a person entry and a `plays`
link to their character, kept in step with `played_by`, undone with the same change, and
made for characters that were played before the change by migration 0058."""

from __future__ import annotations

from dmbot.campaigns import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.memory.models import MemoryRuleError
from dmbot.schema import MIGRATIONS
from tests.pg import TEST_URL
from tests.test_memory_store import DM, GUILD_A, MemoryTest

SRC = "dm"
PLAYER = 111111111111111234
OTHER = 222222222222225678


class People(MemoryTest):
    async def people(self) -> list[tuple[str, int | None]]:
        found = await self.memory.entities(GUILD_A, self.c)
        return [(e.name, e.played_by) for e in found if e.type == "person"]

    async def plays(self) -> list[tuple[str, str]]:
        rels = await self.memory.relations(GUILD_A, self.c)
        return [(r.subject_id, r.object_id) for r in rels if r.predicate == "plays"]

    async def test_a_player_character_makes_its_person_and_the_link(self) -> None:
        pc = await self.add("Testa", type="player_character", played_by=PLAYER)
        self.assertEqual(await self.people(), [("Player 1234", PLAYER)])
        persons = [e for e in await self.memory.entities(GUILD_A, self.c) if e.type == "person"]
        self.assertEqual(await self.plays(), [(persons[0].id, pc)])

    async def test_one_person_for_two_characters(self) -> None:
        a = await self.add("Testa", type="player_character", played_by=PLAYER)
        b = await self.add("Tosta", type="player_character", played_by=PLAYER)
        self.assertEqual(len(await self.people()), 1)
        self.assertEqual({o for _, o in await self.plays()}, {a, b})

    async def test_changing_the_kind_ends_the_link(self) -> None:
        pc = await self.add("Testa", type="player_character", played_by=PLAYER)
        await self.memory.set_entity_type(GUILD_A, self.c, pc, "character", source=SRC, role="npc")
        self.assertEqual(await self.plays(), [])

    async def test_undo_takes_back_the_person_and_the_link_too(self) -> None:
        before = await self.snapshot()
        written = await self.memory.add_entity(
            GUILD_A, self.c, type="player_character", name="Testa", source=SRC, played_by=PLAYER
        )
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.c, written.batch)
        self.assertEqual(await self.snapshot(), before)

    async def test_merging_keeps_one_link(self) -> None:
        keep = await self.add("Testa", type="player_character", played_by=PLAYER)
        gone = await self.add("Tessa", type="player_character", played_by=PLAYER)
        await self.memory.merge(GUILD_A, self.c, keep, gone, source=SRC, dm_said_same=True)
        self.assertEqual([o for _, o in await self.plays()], [keep])

    async def test_the_extractor_cannot_say_who_plays_whom(self) -> None:
        pc = await self.add("Testa", type="player_character", played_by=PLAYER)
        npc = await self.add("Belleros")
        with self.assertRaises(MemoryRuleError):
            await self.relate(npc, "plays", pc)

    async def test_dm_is_never_a_name(self) -> None:
        for word in ("DM", "GM", "Dungeon Master", "game master"):
            with self.assertRaises(MemoryRuleError):
                await self.add(word)
        belleros = await self.add("Belleros")
        with self.assertRaises(MemoryRuleError):
            await self.memory.add_alias(
                GUILD_A, self.c, belleros, "DM", kind="nickname", source=SRC
            )

    async def test_campaigns_do_not_share_people(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Other", DM)).id
        await self.add("Testa", type="player_character", played_by=PLAYER)
        await self.add("Tosta", type="player_character", played_by=PLAYER, campaign=other)
        self.assertEqual(len(await self.people()), 1)
        found = await self.memory.entities(GUILD_A, other)
        self.assertEqual(len([e for e in found if e.type == "person"]), 1)


class PeopleInTheLookup(MemoryTest):
    """The Cleaner's copy of a campaign holds that campaign's people and nobody else's (#1547)."""

    async def test_a_person_of_another_campaign_is_never_a_candidate(self) -> None:
        other = (await self.campaigns.create(GUILD_A, "Other", DM)).id
        await self.add("Testa", type="player_character", played_by=PLAYER, status="confirmed")
        await self.add(
            "Tosta", type="player_character", played_by=OTHER, status="confirmed", campaign=other
        )

        async def person(campaign: str) -> str:
            found = await self.memory.entities(GUILD_A, campaign)
            return next(e.id for e in found if e.type == "person")

        await self.memory.add_alias(
            GUILD_A, self.c, await person(self.c), "Mikael", kind="nickname", source=SRC,
            status="confirmed",
        )  # fmt: skip
        await self.memory.add_alias(
            GUILD_A, other, await person(other), "Brannoch", kind="nickname", source=SRC,
            status="confirmed",
        )  # fmt: skip
        data = await self.memory.lookup_data(GUILD_A, self.c)
        self.assertEqual([a.text for a in data.people], ["Mikael"])
        from dmbot.memory.lookup import CampaignLookup

        lookup = CampaignLookup.build(data)
        self.assertIn("mikael", lookup.people_by_key)
        self.assertNotIn("brannoch", lookup.people_by_key)
        self.assertEqual(
            [a.text for a in (await self.memory.lookup_data(GUILD_A, other)).people], ["Brannoch"]
        )


class Edges(MemoryTest):
    async def test_rejecting_a_character_ends_its_link_and_the_person_needs_a_look(self) -> None:
        pc = await self.add("Testa", type="player_character", played_by=PLAYER)
        await self.memory.set_entity_status(GUILD_A, self.c, pc, "rejected", source=SRC)
        rels = await self.memory.relations(GUILD_A, self.c)
        self.assertEqual([r for r in rels if r.predicate == "plays"], [])
        self.assertEqual((await self.memory.check_people(GUILD_A, self.c, {DM})).value, 1)

    async def test_a_person_is_never_merged_with_a_character(self) -> None:
        pc = await self.add("Testa", type="player_character", played_by=PLAYER)
        person = next(
            e.id for e in await self.memory.entities(GUILD_A, self.c) if e.type == "person"
        )
        with self.assertRaises(MemoryRuleError):
            await self.memory.merge(GUILD_A, self.c, pc, person, source=SRC, dm_said_same=True)
        with self.assertRaises(MemoryRuleError):
            await self.memory.set_entity_type(GUILD_A, self.c, person, "place", source=SRC)


class Checking(MemoryTest):
    async def test_check_people_marks_only_those_who_play_nothing_and_are_not_dm(self) -> None:
        pc = await self.add("Testa", type="player_character", played_by=PLAYER)
        # A player whose character was set to an NPC afterwards plays nothing now.
        gone = await self.add("Quin", type="player_character", played_by=OTHER)
        await self.memory.set_entity_type(
            GUILD_A, self.c, gone, "character", source=SRC, role="npc"
        )
        marked = await self.memory.check_people(GUILD_A, self.c, {DM})
        self.assertEqual(marked.value, 1)
        persons = {
            e.played_by: e
            for e in await self.memory.entities(GUILD_A, self.c)
            if e.type == "person"
        }
        self.assertTrue(persons[OTHER].needs_look)
        self.assertFalse(persons[PLAYER].needs_look)
        self.assertEqual((await self.memory.check_people(GUILD_A, self.c, {DM})).value, 0)
        self.assertEqual((await self.memory.check_people(GUILD_A, self.c, {OTHER})).value, 0)
        self.assertTrue(pc)

    async def test_a_dm_is_not_marked(self) -> None:
        pc = await self.add("Testa", type="player_character", played_by=PLAYER)
        await self.memory.set_entity_type(GUILD_A, self.c, pc, "character", source=SRC, role="npc")
        self.assertEqual((await self.memory.check_people(GUILD_A, self.c, {PLAYER})).value, 0)

    async def test_nobody_adds_a_person_by_hand(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.add("Mia", type="person")


class Backfill0058(MemoryTest):
    """Migrations run with no server set: 0058's backfill must open every table it reads
    and writes, or it makes no person at all."""

    async def test_characters_played_before_get_their_person_and_link(self) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0058"]
        await self.db.close()  # start again from a schema as it was before 0058
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        c = (await CampaignStore(self.db, clock=lambda: 1).create(GUILD_A, "Frozen Wastes", DM)).id
        pcs = {"a" * 32: ("Testa", PLAYER), "b" * 32: ("Tosta", PLAYER), "c" * 32: ("Quin", OTHER)}
        async with self.db.guild(GUILD_A) as conn:
            for eid, (name, player) in pcs.items():
                await conn.execute(
                    "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                    " description, status, source, created_at, played_by, role, needs_look)"
                    " VALUES (%s, %s, %s, 'character', %s, '', 'confirmed', 'dm', 1, %s,"
                    " 'player_character', false)",
                    (GUILD_A, c, eid, name, player),
                )
            await conn.execute(
                "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name,"
                " description, status, source, created_at, role, needs_look)"
                " VALUES (%s, %s, %s, 'character', 'Belleros', '', 'confirmed', 'dm', 1,"
                " 'npc', false)",
                (GUILD_A, c, "d" * 32),
            )
        await self.db.migrate()
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT name, played_by FROM memory_entities WHERE type = 'person' ORDER BY name"
            )
            people = [(r["name"], r["played_by"]) for r in await cur.fetchall()]
            cur = await conn.execute(
                "SELECT p.played_by AS who, r.object_id FROM memory_relations r"
                " JOIN memory_entities p ON p.id = r.subject_id WHERE r.predicate = 'plays'"
            )
            links = sorted((r["who"], r["object_id"]) for r in await cur.fetchall())
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_aliases WHERE text LIKE 'Player %'"
            )
            aliases = (await cur.fetchone() or {})["n"]
        self.assertEqual(people, [("Player 1234", PLAYER), ("Player 5678", OTHER)])
        self.assertEqual(links, [(PLAYER, "a" * 32), (PLAYER, "b" * 32), (OTHER, "c" * 32)])
        self.assertEqual(aliases, 2)
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM pg_policies WHERE policyname = 'migrate_backfill'"
                " AND schemaname = current_schema()"
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)  # no opening left
