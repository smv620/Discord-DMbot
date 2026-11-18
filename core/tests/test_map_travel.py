"""The travel-time suggestion (#4260, MapBot layers slice 10a; design §9; the ux-critic's words;
Supervisor's ruling (a) on the SRD 5.2 pace table). The question shapes, the sums (pace, the land's
cap, half days), the words, the DM-only rule, and the store's answer from a map, a stated length,
the land at either end and a closed road. Invented names. The last class needs Postgres."""

from __future__ import annotations

import time
import unittest
import uuid
from types import SimpleNamespace
from typing import Any

from dmbot.maps import measure_phrase, travel
from dmbot.maps import travel_words as words
from dmbot.sidebar.answer import ONE_FOR_DM
from dmbot.sidebar.map_answers import MapMeasuring
from tests.test_map_measure import Inner
from tests.test_map_pins import PinsBase
from tests.test_map_store import GUILD_A

DM, PLAYER = 7, 9


class TheQuestion(unittest.TestCase):
    def test_the_shapes_and_how_and_how_fast(self) -> None:
        got = measure_phrase.read("How long will it take to get from Targos to Bryn Shander?")
        self.assertEqual(got, measure_phrase.Asked(measure_phrase.LONG, "Targos", "Bryn Shander"))
        got = measure_phrase.read(
            "how many days from Targos to Bryn Shander on foot at a slow pace"
        )
        assert got is not None
        self.assertEqual((got.how, got.pace), (measure_phrase.FOOT, "slow"))
        got = measure_phrase.read("how long is the trip from Targos to Easthaven by boat?")
        assert got is not None
        self.assertEqual(got.how, measure_phrase.BOAT)

    def test_anything_else_goes_the_usual_way(self) -> None:
        for q in ("how long will it take to cast fireball?", "how long is a long rest?"):
            self.assertIsNone(measure_phrase.read(q), q)


class TheSums(unittest.TestCase):
    def test_the_pace_tables_numbers_are_the_srds(self) -> None:
        from dmbot.rules.index import srd

        hit = srd().lookup("travel pace table", "2024", "2014", kind="rule")
        assert hit is not None
        for pace, miles in travel.MILES_A_DAY.items():
            self.assertIn(f"{pace.title()}: ", hit.entry.text)
            self.assertIn(f"{miles} miles a day", hit.entry.text)

    def test_days_round_up_to_the_half(self) -> None:
        plan = travel.plan(40, [])
        self.assertEqual(
            [(d.pace, d.days) for d in plan.days],
            [(travel.FAST, 1.5), (travel.NORMAL, 2.0), (travel.SLOW, 2.5)],
        )

    def test_the_slowest_land_caps_the_pace(self) -> None:
        plan = travel.plan(40, ["grassland", "mountain"])
        self.assertEqual(([d.pace for d in plan.days], plan.capped_by), ([travel.SLOW], "mountain"))
        self.assertTrue(travel.plan(40, ["arctic"]).fast_needs_gear)


class TheWords(unittest.TestCase):
    def test_the_time_first_and_only_paces_that_differ(self) -> None:
        self.assertEqual(
            words.lead(travel.plan(40, ["arctic"]), 40),
            "About 2 days on foot at a Normal pace; 1½ days if Fast (needs skis on ice and"
            " snow); 2½ days if Slow.",
        )
        self.assertEqual(
            words.lead(travel.plan(40, ["mountain"]), 40),
            "About 2½ days on foot: only a Slow pace through mountains.",
        )
        self.assertEqual(
            words.lead(travel.plan(40, ["forest"]), 40),
            "About 2 days on foot at a Normal pace; 2½ days if Slow. No Fast pace through forest.",
        )
        self.assertEqual(  # (Slow covers 18 miles a day: 20 miles is days, never 10 hours)
            words.lead(travel.plan(20, ["mountain"]), 20),
            "About 1½ days on foot: only a Slow pace through mountains.",
        )
        self.assertEqual(
            words.lead(travel.plan(23, []), 23),
            "About 8 hours on foot at a Normal pace; 6 hours if Fast; 1½ days if Slow.",
        )
        self.assertEqual(  # (under a day: hours)
            words.lead(travel.plan(12, []), 12),
            "About 4 hours on foot at a Normal pace; 3 hours if Fast; 6 hours if Slow.",
        )

    def test_the_citation(self) -> None:
        self.assertEqual(
            words.cite(["SRD 5.2.1 p. 12", "SRD 5.2.1 p. 192"]), "SRD 5.2.1 pp. 12, 192."
        )


class TheDMOnly(unittest.IsolatedAsyncioTestCase):
    async def ask(self, asker: int, *, table: bool) -> tuple[Any, list[bool]]:
        dms: list[bool] = []

        async def measured(_c: Any, _asked: Any, dm: bool) -> Any:
            dms.append(dm)
            return "About 2 days on foot …"

        layer = MapMeasuring(Inner(), measured, on=lambda: True)
        campaign = SimpleNamespace(dm_user_ids=frozenset({DM}))
        got = await layer.answer(
            campaign, "how long will it take to get from Ab to Bc?", asker_id=asker, scene="",
            table=table,
        )  # fmt: skip
        return got, dms

    async def test_a_player_is_told_its_the_dms_before_any_map_is_read(self) -> None:
        got, dms = await self.ask(PLAYER, table=True)
        self.assertEqual((got.text, dms), (ONE_FOR_DM, []))

    async def test_the_dm_at_the_table_gets_it_privately(self) -> None:
        got, dms = await self.ask(DM, table=True)
        self.assertTrue(got.one_for_dm)
        self.assertFalse(got.in_game)
        self.assertEqual(dms, [])  # (re-asked privately, with every map)

    async def test_the_dm_in_private_gets_the_answer(self) -> None:
        got, dms = await self.ask(DM, table=False)
        self.assertTrue(got.text.startswith("About 2 days on foot"), got.text)
        self.assertEqual(dms, [True])


class TheAnswer(PinsBase):  # (Postgres: Targos and Bryn Shander 60 miles apart on the map)
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bryn = await self.place("Bryn Shander")
        await self.record()

    async def ask(self, question: str) -> Any:
        asked = measure_phrase.read(question)
        assert asked is not None
        return await self.maps.measure(GUILD_A, self.one.id, asked, dm=True)

    async def land(self, place: str, value: str) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_map_facts (guild_id, campaign_id, id, entity_id, layer, value,"
                " status, source, also_text, created_at) VALUES (%s, %s, %s, %s, 'terrain', %s,"
                " 'confirmed', 'dm', FALSE, %s)",
                (GUILD_A, self.one.id, uuid.uuid4().hex, place, value, int(time.time())),
            )

    async def road(self, length: str | None) -> None:
        await self.memory.add_relation(
            GUILD_A, self.one.id, self.targos, "route_to", self.bryn, source="dm",
            confidence=1.0, status="confirmed",
        )  # fmt: skip
        if length is None:  # (a road with no length at all)
            return
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_relations SET length_words = %s, length_from = 'dm'"
                " WHERE predicate = 'route_to'",
                (length,),
            )

    async def test_from_the_maps_straight_line(self) -> None:
        said = await self.ask("how long will it take to get from Targos to Bryn Shander?")
        self.assertTrue(said.startswith("About 2½ days on foot at a Normal pace"), said)
        self.assertIn("about 60 miles in a straight line from Targos to Bryn Shander", said)
        self.assertIn("No land is set there", said)
        self.assertIn("SRD 5.2.1 p. 12.", said)
        self.assertTrue(said.endswith(words.YOUR_CALL), said)

    async def test_the_land_caps_it_and_is_cited(self) -> None:
        await self.land(self.targos, "mountain")
        said = await self.ask("how long will it take to get from Targos to Bryn Shander?")
        self.assertTrue(said.startswith("About 3½ days on foot: only a Slow pace"), said)
        self.assertIn("SRD 5.2.1 pp. 12, 192.", said)

    async def test_a_stated_length_in_miles_is_the_road(self) -> None:
        await self.road("40 miles")
        said = await self.ask("how long will it take to get from Targos to Bryn Shander?")
        self.assertTrue(said.startswith("About 2 days on foot"), said)
        self.assertIn("That's 40 miles by road from Targos to Bryn Shander, as you set it.", said)

    async def closed(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT id FROM memory_relations WHERE predicate = 'route_to'")
            road = await cur.fetchone()
            assert road is not None
            await conn.execute(
                "INSERT INTO memory_map_facts (guild_id, campaign_id, id, relation_id, layer,"
                " value, status, source, also_text, created_at) VALUES (%s, %s, %s, %s,"
                " 'condition', 'closed', 'confirmed', 'dm', FALSE, %s)",
                (GUILD_A, self.one.id, uuid.uuid4().hex, str(road["id"]), int(time.time())),
            )

    async def test_a_closed_road_is_said_even_with_a_stated_time(self) -> None:
        await self.road("two days by cart")
        await self.closed()
        said = await self.ask("how long will it take to get from Targos to Bryn Shander?")
        self.assertTrue(said.endswith(words.closed()), said)

    async def test_a_road_with_no_length_is_not_called_no_road(self) -> None:
        await self.road(None)
        said = await self.ask("how long will it take to get from Targos to Bryn Shander?")
        self.assertIn("The road between them has no length in miles", said)
        self.assertNotIn("No road is marked", said)

    async def test_a_2014_campaign_cites_its_pace_and_the_newer_terrain_tagged(self) -> None:
        await self.land(self.targos, "mountain")
        asked = measure_phrase.read("how long will it take to get from Targos to Bryn Shander?")
        assert asked is not None
        said = await self.maps.measure(GUILD_A, self.one.id, asked, dm=True, rules=("2014", "none"))
        assert isinstance(said, str)
        self.assertIn("[Legacy 2014]", said)
        self.assertIn("only a Slow pace through mountains", said)
        self.assertIn("SRD 5.2.1 p. 192", said)  # (the cap is cited, never applied uncited)

    async def test_mounted_gets_the_time_on_foot_and_says_so(self) -> None:
        said = await self.ask(
            "how long will it take to ride from Targos to Bryn Shander on horseback?"
        )
        self.assertTrue(said.startswith("About 2½ days on foot"), said)
        self.assertIn("I can't work out mounted travel yet.", said)

    async def test_never_for_a_player(self) -> None:
        asked = measure_phrase.read("how long will it take to get from Targos to Bryn Shander?")
        assert asked is not None
        self.assertIsNone(await self.maps.measure(GUILD_A, self.one.id, asked, dm=False))

    async def test_a_stated_time_is_given_back_and_never_summed(self) -> None:
        await self.road("two days by cart")
        said = await self.ask("how long will it take to get from Targos to Bryn Shander?")
        self.assertEqual(said, "You set two days by cart from Targos to Bryn Shander.")

    async def test_a_boat_gets_no_time_on_foot(self) -> None:
        said = await self.ask("how long will it take to sail from Targos to Bryn Shander by boat?")
        self.assertTrue(said.startswith("I can't work out boat travel yet."), said)

    async def test_no_scale_says_whats_missing(self) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaign_maps SET scale_value = NULL, scale_unit = NULL, scale_from = NULL"
            )
        said = await self.ask("how long will it take to get from Targos to Bryn Shander?")
        self.assertIn("has no scale", said)  # (the "how far" miss words, and no time)
        self.assertNotIn("days", said)
        self.assertNotIn(words.YOUR_CALL, said)
