"""A road's, pass's or bridge's condition read from the lore, and its words on the card (#4258,
MapBot layers slice 4a; Supervisor's ruling on #4258): the set phrases only right after a route's
own name or in "the road between A and B is …"; never weather, never a town ("Bryn Shander is
flooded"), "is out" only when the clause ends; a road two known roads could be says nothing.
Invented names. The upload tests need Postgres."""

from __future__ import annotations

import unittest

from dmbot.memory import gate
from dmbot.memory import map_facts as mf
from dmbot.memory import route_condition as rc
from dmbot.memory.map_fact_store import Condition
from dmbot.ui import route_condition as ui
from dmbot.ui.name_background import store_place_facts
from tests.test_memory_store import GUILD_A, MemoryTest


class Phrases(unittest.TestCase):
    def test_each_value_right_after_the_name(self) -> None:
        for text, value in (
            ("Icewind Pass is snowed in until spring.", rc.CLOSED),
            ("Icewind Pass is impassable.", rc.CLOSED),
            ("Icewind Pass is treacherous in the dark.", rc.HARD),
            ("Icewind Pass has collapsed.", rc.GONE),
            ("Icewind Pass was washed away.", rc.GONE),
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    rc.read("Icewind Pass", text, is_route=True), rc.Said(value, False)
                )

    def test_open_is_only_ever_a_change(self) -> None:
        for text in ("Icewind Pass is open again.", "Icewind Pass reopened in spring."):
            with self.subTest(text=text):
                self.assertEqual(
                    rc.read("Icewind Pass", text, is_route=True), rc.Said(rc.OPEN, True)
                )

    def test_nothing_else_says_anything(self) -> None:
        for text in (
            "A blizzard on Icewind Pass.",  # (weather is never a condition)
            "Icewind Pass is cold and windy.",
            "Icewind Pass is not closed.",
            "The road to Icewind Pass is closed.",  # (not right after the name)
            "Icewind Pass is out of sight from here.",  # ("is out" must end its clause)
            "Icewind Pass is overgrownish.",
            "Icewind Pass is no more than a muddy track.",  # (the reviewer's finding)
            "Icewind Pass reopened its tolls.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(rc.read("Icewind Pass", text, is_route=True))

    def test_is_out_ends_its_clause(self) -> None:
        self.assertEqual(
            rc.read("the Redwaters bridge", "The Redwaters bridge is out.", is_route=True),
            rc.Said(rc.CLOSED, False),
        )
        self.assertEqual(
            rc.read(
                "the Redwaters bridge", "The Redwaters bridge is out, so go round.", is_route=True
            ),
            rc.Said(rc.CLOSED, False),
        )

    def test_never_a_place_that_is_no_route(self) -> None:
        self.assertIsNone(rc.read("Bryn Shander", "Bryn Shander is flooded.", is_route=False))

    def test_the_road_between_two_places(self) -> None:
        got = rc.read_between(
            "The road between Bryn Shander and Targos is closed. The trail from Termalaine to the "
            "Lonelywood was washed away. A blizzard on the road between Easthaven and Caer-Dineval."
            " The road from Bremen to Targos is long."
        )
        self.assertEqual(
            got,
            [
                rc.Between("bryn shander", "targos", rc.Said(rc.CLOSED, False)),
                rc.Between("termalaine", "lonelywood", rc.Said(rc.GONE, False)),
            ],
        )


class Words(unittest.TestCase):
    def fact(self, value: str, words: str = "", status: str = mf.CONFIRMED) -> mf.Fact:
        return mf.Fact("f", "p", mf.CONDITION, value, words, "", "", status, "dm", False)

    def test_the_card_line(self) -> None:
        now = self.fact(rc.CLOSED)
        self.assertEqual(
            ui.condition_line(Condition(now, None, None), {}, dm=False),
            "\n🚧 **Condition:** Closed",
        )
        was = Condition(now, 5_000, self.fact(rc.OPEN))
        self.assertEqual(
            ui.condition_line(was, {5_000: 5}, dm=False),
            "\n🚧 **Condition:** Closed since session 5 (was open)",
        )
        self.assertEqual(  # (before any session: what it was, with no "since")
            ui.condition_line(was, {}, dm=False), "\n🚧 **Condition:** Closed (was open)"
        )
        own = Condition(self.fact(mf.OTHER, "half washed out", mf.READ), None, None)
        self.assertEqual(
            ui.condition_line(own, {}, dm=True),
            "\n🚧 **Condition:** Half washed out (DMbot read this. Players see it once you press "
            "✅ Condition is right.)",
        )
        self.assertEqual(ui.condition_line(None, {}, dm=True), "")

    def test_the_notes(self) -> None:
        self.assertEqual(
            ui.noted("Icewind Pass", rc.CLOSED, "", None), "🚧 Noted: **Icewind Pass** is closed."
        )
        self.assertEqual(
            ui.noted("Icewind Pass", rc.CLOSED, "", "open"),
            "🚧 Noted: **Icewind Pass** was open; now it's closed.",
        )
        self.assertEqual(
            ui.noted("Icewind Pass", mf.OTHER, "bandits on the road", None),
            "🚧 Noted for **Icewind Pass**: bandits on the road.",
        )
        self.assertEqual(
            ui.noted("Icewind Pass", None, "", "open"),
            "🚧 Noted: no condition for **Icewind Pass**.",
        )

    def test_the_menu_fits_a_phone(self) -> None:
        for label, about in ui.CHOICES.values():
            self.assertLessEqual(len(label), 25)
            self.assertLessEqual(len(about), 50)


class FromTheLore(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.pass_ = await self.add("Icewind Pass", type="place", status="confirmed")
        await self.memory.set_place_form(GUILD_A, self.c, self.pass_, "route", source="dm")
        self.town = await self.add("Bryn Shander", type="place", status="confirmed")
        self.targos = await self.add("Targos", type="place", status="confirmed")
        self.road = (
            (
                await self.relate(
                    self.town, "route_to", self.targos, detail="road", status="confirmed"
                )
            )
            .value[0]
            .id
        )

    async def read(self, text: str) -> int:
        campaign = await self.campaigns.get(GUILD_A, self.c)
        assert campaign is not None
        return await store_place_facts(self.memory, campaign, text)

    async def conditions(self) -> dict[str, Condition]:
        return await self.memory.route_conditions(
            GUILD_A, self.c, gate.Viewer.the_dm(), place_ids=[self.pass_, self.town],
            relation_ids=[self.road],
        )  # fmt: skip

    async def test_a_route_and_a_road_as_dmbots_read(self) -> None:
        await self.read(
            "Icewind Pass is snowed in. The road between Bryn Shander and Targos is treacherous. "
            "Bryn Shander is flooded."
        )
        got = await self.conditions()
        self.assertEqual(
            {k: (c.now.value, c.now.status) for k, c in got.items()},
            {self.pass_: (rc.CLOSED, mf.READ), self.road: (rc.HARD, mf.READ)},
        )  # (and never the town)

    async def test_a_road_two_known_roads_could_be_says_nothing(self) -> None:
        await self.relate(self.targos, "route_to", self.town, detail="trail", status="confirmed")
        await self.read("The road between Bryn Shander and Targos is closed.")
        self.assertEqual(await self.conditions(), {})

    async def test_a_trip_is_no_road(self) -> None:
        bremen = await self.add("Bremen", type="place", status="confirmed")
        await self.relate(
            self.town, "route_to", bremen, detail="road", status="confirmed",
            route_state="travelled", from_session_at=5_000,
        )  # fmt: skip
        await self.read("The road from Bryn Shander to Bremen is blocked.")
        self.assertEqual(await self.conditions(), {})

    async def test_weather_says_nothing(self) -> None:
        await self.read(
            "A blizzard on Icewind Pass. Snow fell on the road between Bryn Shander and Targos."
        )
        self.assertEqual(await self.conditions(), {})
