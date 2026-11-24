"""A campaign's named cultures (#4257 slice 3b; Supervisor's ruling on #4257): "Illuskan" beside the
lineages, as the DM's word or as DMbot's read for World notes; a turned-down name is never read
again and takes its rows with it; "Something else…" on a culture makes a named culture; migration
0496 turns 3a's own-words culture rows into named cultures, behind forced row-level security
(CLAUDE.md, #704). Invented names. Needs Postgres."""

from __future__ import annotations

import unittest
import uuid
from unittest import mock

from dmbot.campaigns import CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.memory import gate
from dmbot.memory import map_facts as mf
from dmbot.memory.map_fact_store import Read
from dmbot.memory.models import CONFIRMED, new_id
from dmbot.memory.models import DM as BY_DM
from dmbot.memory.store import MemoryStore
from dmbot.schema import MIGRATIONS
from tests.pg import REQUIRE_DB, TEST_URL
from tests.test_memory_store import DM, GUILD_A, MemoryTest


class NamedCultures(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.town = await self.add("Luskan", type="place", status=CONFIRMED)
        self.ruin = await self.add("Ascore", type="place", status=CONFIRMED)

    async def facts(self, place: str, viewer: gate.Viewer | None = None) -> list[mf.Fact]:
        got = await self.memory.map_facts(GUILD_A, self.c, [place], viewer or gate.Viewer.the_dm())
        return sorted(got.get(place, []), key=lambda f: (f.layer, f.words, f.value))

    async def read(self, *found: Read) -> dict[str, int]:
        got = await self.memory.add_read_map_facts(
            GUILD_A, self.c, list(found), source="background"
        )
        return dict(got.value)

    async def test_the_dm_types_a_culture_and_it_sits_beside_a_lineage(self) -> None:
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.town, mf.CULTURE, "human", how="some", source=BY_DM
        )
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.town, mf.CULTURE, mf.OTHER, how="strong", words=" Illuskan ",
            source=BY_DM,
        )  # fmt: skip
        got = [(f.words or f.value, f.how, f.status) for f in await self.facts(self.town)]
        self.assertEqual(got, [("human", "some", CONFIRMED), ("Illuskan", "strong", CONFIRMED)])
        listed = await self.memory.map_cultures(GUILD_A, self.c)
        self.assertEqual([(name, status) for _, name, status in listed], [("Illuskan", CONFIRMED)])
        # (typing the same name again, any case, is the same culture: its shade changes)
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.town, mf.CULTURE, mf.OTHER, how="traces", words="illuskan",
            source=BY_DM,
        )  # fmt: skip
        named = [f for f in await self.facts(self.town) if mf.is_named(mf.CULTURE, f.value)]
        self.assertEqual([(f.words, f.how) for f in named], [("Illuskan", "traces")])
        self.assertEqual(len(await self.memory.map_cultures(GUILD_A, self.c)), 1)

    async def test_two_named_cultures_sit_side_by_side(self) -> None:
        for name in ("Illuskan", "Reghed"):
            await self.memory.set_map_fact(
                GUILD_A, self.c, self.town, mf.CULTURE, mf.OTHER, how="strong", words=name,
                source=BY_DM,
            )  # fmt: skip
        self.assertEqual([f.words for f in await self.facts(self.town)], ["Illuskan", "Reghed"])

    async def test_a_read_name_waits_and_keeping_the_row_keeps_the_culture(self) -> None:
        counts = await self.read(Read(self.town, mf.CULTURE, "", "strong", culture="Illuskan"))
        self.assertEqual(counts["written"], 1)
        [fact] = await self.facts(self.town)
        self.assertEqual((fact.words, fact.status), ("Illuskan", mf.READ))
        [(_, _, status)] = await self.memory.map_cultures(GUILD_A, self.c)
        self.assertEqual(status, mf.READ)
        await self.memory.keep_map_facts(
            GUILD_A, self.c, self.town, source=BY_DM, layers=(mf.CULTURE,)
        )
        [(_, _, status)] = await self.memory.map_cultures(GUILD_A, self.c)
        self.assertEqual(status, CONFIRMED)

    async def test_turning_a_read_name_down_rejects_its_rows_and_it_is_never_read_again(
        self,
    ) -> None:
        await self.read(
            Read(self.town, mf.CULTURE, "", "strong", culture="Illuskan"),
            Read(self.ruin, mf.CULTURE, "", "once", culture="Illuskan"),
        )
        notes = await self.memory.world_notes(GUILD_A, self.c)
        self.assertEqual(len(notes), 2)
        done = await self.memory.turn_down_world_note(GUILD_A, self.c, notes[0].id, source=BY_DM)
        self.assertTrue(done.value)  # (the name went, at every place: the screen says so)
        self.assertEqual(await self.facts(self.town), [])
        self.assertEqual(await self.facts(self.ruin), [])
        self.assertEqual(await self.memory.map_cultures(GUILD_A, self.c), [])
        again = await self.read(Read(self.town, mf.CULTURE, "", "strong", culture="illuskan"))
        self.assertEqual((again.get("written", 0), again["rejected_before"]), (0, 1))
        # (only the DM's own word brings it back)
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.town, mf.CULTURE, mf.OTHER, how="strong", words="Illuskan",
            source=BY_DM,
        )  # fmt: skip
        self.assertEqual([f.words for f in await self.facts(self.town)], ["Illuskan"])

    async def test_keep_all_in_world_notes_keeps_the_name_too(self) -> None:
        await self.read(Read(self.town, mf.CULTURE, "", "strong", culture="Reghed"))
        notes = await self.memory.world_notes(GUILD_A, self.c)
        await self.memory.keep_world_notes(GUILD_A, self.c, [n.id for n in notes], source=BY_DM)
        [(_, _, status)] = await self.memory.map_cultures(GUILD_A, self.c)
        self.assertEqual(status, CONFIRMED)

    async def test_a_name_with_no_row_is_never_made(self) -> None:
        counts = await self.read(Read(self.town, mf.CULTURE, "", "", culture="Illuskan"))
        self.assertEqual(counts["not_allowed"], 1)  # (a culture always says how strong)
        self.assertEqual(await self.memory.map_cultures(GUILD_A, self.c), [])

    async def test_picking_a_read_name_makes_it_the_dms(self) -> None:
        await self.read(Read(self.ruin, mf.CULTURE, "", "once", culture="Netherese"))
        [(cid, _, _)] = await self.memory.map_cultures(GUILD_A, self.c)
        await self.memory.set_map_fact(
            GUILD_A, self.c, self.town, mf.CULTURE, cid, how="traces", source=BY_DM
        )
        [(_, _, status)] = await self.memory.map_cultures(GUILD_A, self.c)
        self.assertEqual(status, CONFIRMED)

    async def test_players_see_a_named_culture_only_once_kept_and_shown(self) -> None:
        await self.read(Read(self.town, mf.CULTURE, "", "strong", culture="Illuskan"))
        await self.memory.show_map_facts(GUILD_A, self.c, self.town, True, source=BY_DM)
        player = gate.Viewer.player(())

        async def seen() -> list[str]:
            got = await self.memory.map_facts(
                GUILD_A, self.c, [self.town], player, shown={self.town}
            )
            return [f.words for f in got.get(self.town, [])]

        self.assertEqual(await seen(), [])  # (DMbot's read is the DM's until kept)
        await self.memory.keep_map_facts(GUILD_A, self.c, self.town, source=BY_DM)
        self.assertEqual(await seen(), ["Illuskan"])
        await self.memory.show_map_facts(GUILD_A, self.c, self.town, False, source=BY_DM)
        self.assertEqual(await seen(), [])

    async def test_a_culture_is_one_undo(self) -> None:
        made = await self.memory.set_map_fact(
            GUILD_A, self.c, self.town, mf.CULTURE, mf.OTHER, how="strong", words="Netherese",
            source=BY_DM,
        )  # fmt: skip
        assert made.batch is not None
        await self.memory.undo(GUILD_A, self.c, made.batch)
        self.assertEqual(await self.facts(self.town), [])
        self.assertEqual(await self.memory.map_cultures(GUILD_A, self.c), [])


class TheMigration(unittest.IsolatedAsyncioTestCase):
    """0496 on a table holding 3a's own-words culture rows, in two campaigns."""

    NAME = "0496_named_cultures"

    async def asyncSetUp(self) -> None:
        if not TEST_URL:
            if REQUIRE_DB:
                self.fail("DMBOT_TEST_DATABASE_URL is needed")
            self.skipTest("set DMBOT_TEST_DATABASE_URL to test the migration")
        self.schema = f"t_{uuid.uuid4().hex[:16]}"
        self.db = await Database.open(
            TEST_URL, schema=self.schema, max_size=2,
            # (0496 and every later one: a later migration may build on 0496's column, #4258)
            migrations=tuple(m for m in MIGRATIONS if m[0] < self.NAME),
        )  # fmt: skip
        self.memory = MemoryStore(self.db)
        # (a place kept looks for a map's loose pins, #4670; that table comes after 0496)
        no_pins = mock.patch("dmbot.memory.store._rematch_pins", mock.AsyncMock())
        no_pins.start()
        self.addCleanup(no_pins.stop)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)

    async def place(self, campaign: str, name: str) -> str:
        # (written as the schema stood before 0496: the store's own insert names later columns,
        # such as 0614's `described`)
        eid = new_id()
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_entities (guild_id, campaign_id, id, type, name, description,"
                " status, source, created_at) VALUES (%s, %s, %s, 'place', %s, '', 'confirmed',"
                " 'dm', 1)",
                (GUILD_A, campaign, eid, name),
            )
        return eid

    async def test_own_words_cultures_become_named_cultures(self) -> None:
        store = CampaignStore(self.db)
        one = (await store.create(GUILD_A, "Frostmaiden", DM)).id
        two = (await store.create(GUILD_A, "Elsewhere", DM)).id
        a, b = await self.place(one, "Luskan"), await self.place(one, "Ascore")
        c = await self.place(two, "Bree")
        rows = [
            (one, a, "culture", "other", "Illuskan", "strong"),
            (one, b, "culture", "other", " illuskan", "once"),  # (the same name: one culture)
            (one, b, "culture", "dwarf", "", "some"),  # (a lineage: left alone)
            (one, a, "population", "other", "duergar", "most"),  # (who lives there: left alone)
            (two, c, "culture", "other", "Illuskan", "strong"),  # (its own campaign's)
            (two, c, "culture", "other", "Reghed", "once"),  # (taken away: turned down below)
        ]
        async with self.db.guild(GUILD_A) as conn:
            for at, (campaign, place, layer, value, words, how) in enumerate(rows, 1):
                await conn.execute(  # (the first one written gives the name its spelling)
                    "INSERT INTO memory_map_facts (guild_id, campaign_id, id, entity_id, layer,"
                    " value, words, how, status, source, created_at) VALUES (%s, %s, %s, %s, %s,"
                    " %s, %s, %s, %s, 'dm', %s)",
                    (
                        GUILD_A,
                        campaign,
                        new_id(),
                        place,
                        layer,
                        value,
                        words,
                        how,
                        "rejected" if words == "Reghed" else "confirmed",
                        at,
                    ),
                )
        await self.db.migrate(MIGRATIONS)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT campaign_id, name, name_key, status, source FROM memory_cultures"
                " ORDER BY campaign_id, name_key"
            )
            cultures = [tuple(r.values()) for r in await cur.fetchall()]
            cur = await conn.execute(
                "SELECT f.campaign_id, f.layer, f.value, f.words, c.name FROM memory_map_facts f"
                " LEFT JOIN memory_cultures c ON c.guild_id = f.guild_id"
                " AND c.campaign_id = f.campaign_id AND c.id = f.culture_id"
                " ORDER BY f.campaign_id, f.layer, f.value, c.name, f.words"
            )
            facts = [tuple(r.values()) for r in await cur.fetchall()]
        self.assertEqual(
            sorted(cultures),
            sorted([(one, "Illuskan", "illuskan", "confirmed", "dm"),
                    (two, "Illuskan", "illuskan", "confirmed", "dm"),
                    (two, "Reghed", "reghed", "rejected", "dm")]),
        )  # fmt: skip
        self.assertEqual(
            sorted(facts, key=str),
            sorted([(one, "culture", "", "", "Illuskan"), (one, "culture", "", "", "Illuskan"),
                    (one, "culture", "dwarf", "", None),
                    (one, "population", "other", "duergar", None),
                    (two, "culture", "", "", "Illuskan"), (two, "culture", "", "", "Reghed")],
                   key=str),
        )  # fmt: skip
        async with self.db.guild(GUILD_A) as conn:  # (the migration's own policies are gone)
            cur = await conn.execute(
                "SELECT count(*) AS n FROM pg_policies WHERE policyname = 'migrate_backfill'"
            )
            found = await cur.fetchone()
        assert found is not None
        self.assertEqual(found["n"], 0)
