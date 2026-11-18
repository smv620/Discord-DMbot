"""A route taken leg by leg (#4260, MapBot layers slice 10d; Supervisor's ruling on #4260; the
ux-critic's words): the fewest legs in a stable order, a closed leg kept and said, a total only
when it says nothing untrue, and a player's legs only when the party knows every one. Invented
names. The last class needs Postgres."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from dmbot.maps import legs, measure_phrase
from dmbot.maps import legs_words as words
from dmbot.memory import gate, route_length
from tests.test_map_pins import PinsBase
from tests.test_map_store import GUILD_A

N = {"t": "Targos", "b": "Bryn Shander", "e": "Easthaven", "m": "Termalaine", "x": "Lonelywood"}


def roads(*rows: tuple[Any, ...]) -> list[legs.Road]:
    return [legs.Road(*row) for row in rows]


class TheRoute(unittest.TestCase):
    def test_the_fewest_legs_in_a_stable_order_and_the_other_way(self) -> None:
        found = legs.find(
            roads(("r1", "t", "m"), ("r2", "m", "e"), ("r3", "t", "b"), ("r4", "b", "e")),
            "t", "e", N,
        )  # fmt: skip
        assert found is not None
        self.assertEqual(found.stops, ("t", "b", "e"))  # (Bryn Shander before Termalaine by name)
        self.assertEqual(found.other_via, "m")
        again = legs.find(
            roads(("r4", "b", "e"), ("r3", "t", "b"), ("r2", "m", "e"), ("r1", "t", "m")),
            "t", "e", N,
        )  # fmt: skip
        self.assertEqual(again, found)  # (the order the roads come in never matters)

    def test_fewer_legs_win_over_names(self) -> None:
        found = legs.find(
            roads(("r1", "t", "b"), ("r2", "b", "m"), ("r3", "m", "e"), ("r4", "t", "x"),
                  ("r5", "x", "e")),
            "t", "e", N,
        )  # fmt: skip
        assert found is not None
        self.assertEqual(found.stops, ("t", "x", "e"))
        self.assertIsNone(found.other_via)

    def test_a_direct_road_too_long_a_chain_or_none_is_no_route(self) -> None:
        self.assertIsNone(legs.find(roads(("r1", "t", "e")), "t", "e", N))  # (10a's and 4b's)
        chain = roads(
            ("r1", "t", "b"), ("r2", "b", "m"), ("r3", "m", "x"), ("r4", "x", "p"), ("r5", "p", "e")
        )
        self.assertIsNone(legs.find(chain, "t", "e", {**N, "p": "Caer-Dineval"}))  # (5 legs)
        self.assertIsNone(legs.find(roads(("r1", "t", "b")), "t", "e", N))
        self.assertIsNone(legs.find(roads(("r1", "t", "z"), ("r2", "z", "e")), "t", "e", N))

    def test_too_many_paths_is_no_answer(self) -> None:
        names = {f"p{i}": f"Place {i:04d}" for i in range(80)} | {"t": "Targos", "e": "Easthaven"}
        grid = [("t", f"p{i}") for i in range(80)] + [
            (f"p{i}", f"p{j}") for i in range(80) for j in range(i + 1, 80)
        ]
        rows = [(f"r{n}", a, b) for n, (a, b) in enumerate(grid)] + [("rz", "p79", "e")]
        found = legs.find(roads(*rows), "t", "e", names)
        assert found is not None
        self.assertEqual(found.stops, ("t", "p79", "e"))  # (with room to search, it's found)
        with patch.object(legs, "MAX_PATHS", 50):
            self.assertIsNone(legs.find(roads(*rows), "t", "e", names))  # (never a wrong one)

    def test_a_closed_leg_stays_on_the_route(self) -> None:
        found = legs.find(roads(("r1", "t", "b", "", True), ("r2", "b", "e")), "t", "e", N)
        assert found is not None
        self.assertTrue(found.legs[0].road.closed)


class TheTotal(unittest.TestCase):
    def total(self, *lengths: str) -> legs.Total | str:
        chain = [("t", "b"), ("b", "m"), ("m", "e")][: len(lengths)]
        rows = [
            (f"r{i}", a, b, w) for i, ((a, b), w) in enumerate(zip(chain, lengths, strict=True))
        ]
        found = legs.find(roads(*rows), "t", chain[-1][1], N)
        assert found is not None
        return legs.total(found)

    def test_one_unit_adds_up(self) -> None:
        self.assertEqual(self.total("20 miles", "two leagues"), legs.Total(26, "miles"))
        self.assertEqual(self.total("a day", "half a day", "2 days"), legs.Total(3.5, "days"))
        self.assertEqual(self.total("two days by cart", "a day by wagon"), legs.Total(3, "days"))

    def test_why_not(self) -> None:
        self.assertEqual(self.total("20 miles", ""), legs.NO_LENGTH)
        self.assertEqual(self.total("20 miles", "a few days"), legs.NOT_A_NUMBER)
        self.assertEqual(self.total("a few days", "a few days"), legs.NOT_A_NUMBER)
        self.assertEqual(self.total("20 to 30 miles", "25 miles"), legs.A_RANGE)
        self.assertEqual(self.total("20 miles", "two days"), legs.MIXED_UNITS)
        self.assertEqual(self.total("two days by cart", "three days on foot"), legs.MIXED_WAYS)
        self.assertEqual(self.total("two days by cart", "three days"), legs.MIXED_WAYS)
        self.assertTrue(route_length.is_range("about two or three days"))
        self.assertFalse(route_length.is_range("about two days"))


class TheWords(unittest.TestCase):
    def say(self, *rows: tuple[Any, ...]) -> str:
        found = legs.find(roads(*rows), "t", "e", N)
        assert found is not None
        return words.route(found, N, legs.total(found))

    def test_a_total_first_and_a_closed_road_said_twice(self) -> None:
        self.assertEqual(
            self.say(("r1", "t", "b", "20 miles", True), ("r2", "b", "e", "25 miles")),
            "About 45 miles by road from Targos to Easthaven, through Bryn Shander: Targos to"
            " Bryn Shander, 20 miles (closed now); Bryn Shander to Easthaven, 25 miles. One road"
            " on the way is closed now.",
        )

    def test_no_total_says_why_and_the_other_way(self) -> None:
        self.assertEqual(
            self.say(
                ("r1", "t", "b", ""), ("r2", "b", "e", "5 miles"), ("r3", "t", "m", "2 miles"),
                ("r4", "m", "e", "3 miles"),
            ),
            "By road from Targos to Easthaven, through Bryn Shander: Targos to Bryn Shander, no"
            " length set; Bryn Shander to Easthaven, 5 miles. No total, since a leg has no length."
            " Another way goes through Termalaine.",
        )  # fmt: skip

    def test_two_stops_and_halves(self) -> None:
        self.assertEqual(
            self.say(
                ("r1", "t", "b", "1 day"), ("r2", "b", "m", "1.5 days"), ("r3", "m", "e", "1 day")
            ),
            "About 3½ days by road from Targos to Easthaven, through Bryn Shander and Termalaine:"
            " Targos to Bryn Shander, 1 day; Bryn Shander to Termalaine, 1.5 days; Termalaine to"
            " Easthaven, 1 day.",
        )


class TheAnswer(PinsBase):  # (Postgres: Targos and Bryn Shander 60 miles apart on the map)
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bryn = await self.place("Bryn Shander")
        self.east = await self.place("Easthaven")
        await self.record()

    async def road(self, a: str, b: str, length: str | None, *, status: str = "confirmed") -> str:
        made = await self.memory.add_relation(
            GUILD_A, self.one.id, a, "route_to", b, source="dm", confidence=1.0, status=status
        )
        if length is not None:
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute(
                    "UPDATE memory_relations SET length_words = %s, length_from = 'dm'"
                    " WHERE id = %s",
                    (length, made.value[0].id),
                )
        return str(made.value[0].id)

    async def ask(self, question: str, *, dm: bool = True) -> Any:
        asked = measure_phrase.read(question)
        assert asked is not None
        return await self.maps.measure(GUILD_A, self.one.id, asked, dm=dm)

    async def party(self) -> None:
        await self.memory.add_entity(
            GUILD_A, self.one.id, name="Cerric", type="character", role="player_character",
            played_by=4242, status="confirmed", source="dm",
        )  # fmt: skip

    async def test_how_far_by_the_legs_for_the_dm(self) -> None:
        await self.road(self.targos, self.bryn, "20 miles")
        await self.road(self.bryn, self.east, "25 miles")
        self.assertEqual(
            await self.ask("how far is Easthaven from Targos?"),
            "About 45 miles by road from Easthaven to Targos, through Bryn Shander: Easthaven to"
            " Bryn Shander, 25 miles; Bryn Shander to Targos, 20 miles.",
        )

    async def test_how_long_from_a_total_in_miles(self) -> None:
        await self.road(self.targos, self.bryn, "20 miles")
        await self.road(self.bryn, self.east, "28 miles")
        said = await self.ask("how long will it take to get from Targos to Easthaven?")
        self.assertTrue(said.startswith("About 2 days on foot at a Normal pace"), said)
        self.assertIn("About 48 miles by road from Targos to Easthaven, through Bryn Shander", said)
        self.assertTrue(said.endswith("Your call."), said)

    async def test_how_long_with_no_total_is_the_legs(self) -> None:
        await self.road(self.targos, self.bryn, "two days by cart")
        await self.road(self.bryn, self.east, "a day on foot")
        said = await self.ask("how long will it take to get from Targos to Easthaven?")
        self.assertTrue(said.startswith("By road from Targos to Easthaven"), said)
        self.assertTrue(said.endswith("different ways of travelling."), said)

    async def test_an_unkept_or_ended_road_is_never_a_leg(self) -> None:
        await self.road(self.targos, self.bryn, "20 miles", status="proposed")
        await self.road(self.bryn, self.east, "25 miles")
        self.assertFalse(
            str(await self.ask("how far is Easthaven from Targos?")).startswith("About 45")
        )

    async def test_a_player_gets_the_legs_only_when_the_party_knows_them_all(self) -> None:
        await self.party()
        before = await self.ask("how far is Easthaven from Targos?", dm=False)
        await self.road(self.targos, self.bryn, "20 miles")
        await self.road(self.bryn, self.east, "25 miles")
        said = await self.ask("how far is Easthaven from Targos?", dm=False)
        self.assertTrue(str(said).startswith("About 45 miles by road"), said)
        await self.memory.set_hidden_from_players(
            GUILD_A, self.one.id, self.bryn, True, source="dm", dm_chose_secret=True
        )
        # (a stop the party doesn't know: today's answer, as it was, never a hint of a route)
        self.assertEqual(await self.ask("how far is Easthaven from Targos?", dm=False), before)

    async def test_a_length_dmbot_read_is_no_length_for_a_player(self) -> None:
        await self.party()
        await self.road(self.targos, self.bryn, "20 miles")
        read = await self.road(self.bryn, self.east, "25 miles")
        async with self.db.guild(GUILD_A) as conn:  # (DMbot's read, not yet kept by the DM)
            await conn.execute(
                "UPDATE memory_relations SET length_from = 'read' WHERE id = %s", (read,)
            )
        said = await self.ask("how far is Easthaven from Targos?", dm=False)
        self.assertIn("Easthaven to Bryn Shander, no length set", said)
        self.assertNotIn("25 miles", said)
        self.assertTrue(
            str(await self.ask("how far is Easthaven from Targos?")).startswith("About 45 miles")
        )  # (the DM's answer counts it, as 10a's does)

    async def test_a_road_the_party_doesnt_know_is_never_a_players_leg(self) -> None:
        await self.party()
        before = await self.ask("how far is Easthaven from Targos?", dm=False)
        await self.road(self.targos, self.bryn, "20 miles")
        secret = await self.road(self.bryn, self.east, "25 miles")
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE memory_relations SET secret = TRUE WHERE id = %s", (secret,))
        self.assertEqual(await self.ask("how far is Easthaven from Targos?", dm=False), before)
        said = await self.ask("how far is Easthaven from Targos?")  # (the DM's: every kept road)
        self.assertTrue(str(said).startswith("About 45 miles"), said)

    async def test_a_players_stops_are_named_as_the_lists_name_them(self) -> None:
        await self.party()
        await self.road(self.targos, self.bryn, "20 miles")
        await self.road(self.bryn, self.east, "25 miles")
        real = gate.names_for

        async def lists_say(scope: Any, viewer: Any, ids: Any, **kw: Any) -> Any:
            said = await real(scope, viewer, ids, **kw)
            return {**said, self.bryn: "the walled town"}  # (as if known by another name)

        with patch.object(gate, "names_for", lists_say):
            said = await self.ask("how far is Easthaven from Targos?", dm=False)
        self.assertIn("through the walled town", said)
        self.assertNotIn("Bryn Shander", said)

        async def unnamed(scope: Any, viewer: Any, ids: Any, **kw: Any) -> Any:
            return {**(await real(scope, viewer, ids, **kw)), self.bryn: None}

        with patch.object(gate, "names_for", unnamed):
            said = await self.ask("how far is Easthaven from Targos?", dm=False)
        self.assertFalse(str(said).startswith("About 45"), said)  # (no name for them: no route)

    async def test_the_ends_are_named_as_asked_and_a_closed_leg_is_said(self) -> None:
        road = await self.road(self.targos, self.bryn, "20 miles")
        await self.road(self.bryn, self.east, "25 miles")
        await self.memory.set_route_condition(
            GUILD_A, self.one.id, "closed", relation_id=road, source="dm"
        )
        said = await self.ask("how far is easthaven from the Targos?")
        self.assertIn("Bryn Shander to the Targos, 20 miles (closed now)", said)
        self.assertIn("One road on the way is closed now.", said)
        trip = await self.ask("how long will it take to get from Targos to Easthaven?")
        self.assertIn("(closed now)", trip)

    async def test_no_offer_for_a_route_with_no_total_in_miles(self) -> None:
        await self.road(self.targos, self.bryn, "two days")
        await self.road(self.bryn, self.east, "a day")
        line = "Targos is a week from Easthaven."
        self.assertEqual(await self.maps.said_trips(GUILD_A, self.one.id, line), [])

    async def test_a_player_with_no_party_gets_todays_answer(self) -> None:
        before = await self.ask("how far is Easthaven from Targos?", dm=False)
        await self.road(self.targos, self.bryn, "20 miles")
        await self.road(self.bryn, self.east, "25 miles")
        self.assertEqual(await self.ask("how far is Easthaven from Targos?", dm=False), before)
