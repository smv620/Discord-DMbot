"""Two places with a way between them (#4173 slice 6d part 1, Supervisor's yes to the routes
proposal): the means a `route_to` keeps and how it reads from either place, two means between the
same two places as two connections, one travelled leg per pair, means and session, a state only on
a route, and the database and backups saying the same. Invented and published place names only."""

from __future__ import annotations

import unittest
from dataclasses import replace

from dmbot.campaigns import CampaignError
from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory import link_words
from dmbot.memory import route_words as rw
from dmbot.memory.checks import duplicate_of
from dmbot.memory.models import CONFIRMED, Relation
from dmbot.memory.models import DM as BY_DM
from tests.test_memory_store import DM, GUILD_A, GUILD_B, MemoryTest


class TheWords(unittest.TestCase):
    def test_the_words_said_fold_to_one_means(self) -> None:
        for said, kept in (
            ("Highway", rw.ROAD), ("by  road", rw.ROAD), ("teleportation circle", rw.PORTAL),
            ("airship", rw.AIR), ("water trade route", rw.SEA), ("over the pass", rw.WILD),
            ("downriver", rw.RIVER), ("by mule train", ""), ("", ""),
        ):  # fmt: skip
            with self.subTest(said=said):
                self.assertEqual(rw.means_of(said), kept)

    def test_a_means_off_the_list_keeps_the_texts_words(self) -> None:
        self.assertEqual(rw.canonical(" by  mule train "), "by mule train")
        self.assertEqual(rw.canonical("Cart Track"), rw.ROAD)

    def test_how_it_reads(self) -> None:
        self.assertEqual(rw.forward(rw.ROAD), "is connected by road to")
        self.assertEqual(rw.pair(rw.PORTAL), "are linked by a portal")
        self.assertEqual(rw.pair("by mule train"), "are connected (“by mule train”)")
        self.assertEqual(rw.pair(""), "are connected")
        self.assertEqual(rw.journey(rw.WILD), "across the wild")
        # from the second place: the same both ways, "is reached … from" when made one way
        self.assertEqual(rw.backward(rw.RIVER), "is connected by river to")
        self.assertEqual(rw.backward(rw.PORTAL, one_way=True), "is reached through a portal from")

    def test_the_card_words(self) -> None:
        self.assertEqual(link_words.link_words("route_to", rw.SEA), "is connected by sea to")
        self.assertEqual(link_words.pair_words("route_to", rw.AIR), "are connected by air")
        self.assertTrue(link_words.reads_both_ways("route_to", rw.ROAD))
        self.assertEqual(
            link_words.back_words("route_to", rw.ROAD, both_ways=False),
            "is reached by road from",
        )


def route(rid: str, detail: str = rw.ROAD, state: str = "", at: int | None = None) -> Relation:
    return Relation(
        rid, "a" * 32, "route_to", "b" * 32, detail, 1.0, CONFIRMED, "dm", (), at, None, None,
        None, False, 0, route_state=state,
    )  # fmt: skip


class TheSame(unittest.TestCase):
    def test_two_means_are_two_connections(self) -> None:
        self.assertIsNone(duplicate_of(route("1" * 32, rw.RIVER), [route("2" * 32)]))
        self.assertIsNotNone(duplicate_of(route("1" * 32), [route("2" * 32)]))

    def test_one_travelled_leg_per_pair_means_and_session(self) -> None:
        leg = route("2" * 32, state=rw.TRAVELLED, at=100)
        self.assertIsNotNone(duplicate_of(route("1" * 32, state=rw.TRAVELLED, at=100), [leg]))
        self.assertIsNone(duplicate_of(route("1" * 32, state=rw.TRAVELLED, at=200), [leg]))

    def test_a_known_road_and_a_journey_on_it_are_two(self) -> None:
        self.assertIsNone(
            duplicate_of(route("1" * 32, state=rw.TRAVELLED, at=100), [route("2" * 32)])
        )
        planned = replace(route("2" * 32), route_state=rw.PLANNED)
        self.assertIsNone(duplicate_of(route("1" * 32), [planned]))


class Stored(MemoryTest):
    async def towns(self) -> tuple[str, str]:
        targos = await self.add("Targos", type="place", status=CONFIRMED)
        bryn = await self.add("Bryn Shander", type="place", status=CONFIRMED)
        return targos, bryn

    async def connect(self, a: str, b: str, detail: str, **kw: object) -> Relation:
        written = await self.memory.add_relation(
            GUILD_A, self.c, a, "route_to", b, source=BY_DM, confidence=1.0, status=CONFIRMED,
            detail=detail, **kw,  # type: ignore[arg-type]
        )  # fmt: skip
        return written.value[0]

    async def routes(self) -> list[Relation]:
        return [
            r for r in await self.memory.relations(GUILD_A, self.c) if r.predicate == "route_to"
        ]

    async def test_a_road_and_a_river_are_two_and_a_highway_is_the_road(self) -> None:
        targos, bryn = await self.towns()
        await self.connect(targos, bryn, "road")
        await self.connect(targos, bryn, "river")
        again = await self.connect(bryn, targos, "highway")  # (either way round: two-way)
        self.assertEqual(again.detail, rw.ROAD)
        self.assertEqual(sorted(r.detail for r in await self.routes()), [rw.RIVER, rw.ROAD])

    async def test_journeys_one_per_session(self) -> None:
        targos, bryn = await self.towns()
        for at in (100, 100, 200):
            await self.connect(targos, bryn, "road", route_state=rw.TRAVELLED, from_session_at=at)
        legs = [r for r in await self.routes() if r.route_state == rw.TRAVELLED]
        self.assertEqual(sorted(r.from_session_at or 0 for r in legs), [100, 200])

    async def test_only_a_route_has_a_state(self) -> None:
        targos, bryn = await self.towns()
        with self.assertRaises(ValueError):
            await self.memory.add_relation(
                GUILD_A, self.c, targos, "next_to", bryn, source=BY_DM, confidence=1.0,
                route_state=rw.PLANNED,
            )  # fmt: skip
        with self.assertRaises(ValueError):
            await self.connect(targos, bryn, "road", route_state="walked")

    async def test_the_database_keeps_a_state_on_routes_only(self) -> None:
        targos, bryn = await self.towns()
        await self.connect(targos, bryn, "road", route_state=rw.PLANNED)
        with self.assertRaises(Exception) as caught:
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute(
                    "UPDATE memory_relations SET predicate = 'next_to' WHERE route_state <> ''"
                )
        self.assertIn("route_state", str(caught.exception))

    async def test_a_backup_carries_the_state_and_refuses_it_elsewhere(self) -> None:
        targos, bryn = await self.towns()
        await self.connect(targos, bryn, "road", route_state=rw.TRAVELLED, from_session_at=100)
        data = await self.campaigns.export(GUILD_A, self.c)
        restored = await self.campaigns.import_backup(
            GUILD_B, decode_backup(encode_backup(data)), DM
        )
        copy = await self.snapshot(GUILD_B, restored.id)
        self.assertEqual([r["route_state"] for r in copy["memory_relations"]], [rw.TRAVELLED])
        damaged = [
            {**r, "predicate": "next_to"} if r.get("route_state") else r
            for r in data["sections"]["memory"]
        ]
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(
                GUILD_B, {**data, "sections": {**data["sections"], "memory": damaged}}, DM
            )
