"""When the DM's word and a map disagree (#4670, MapBot slice 4c; docs/design/mapbot_layers.md
§9): the 20% and 45° limits, a straight line flagged only when the stated distance is shorter, north
only when read or set, and the map an answer would use. Invented names; the last class needs
Postgres."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.maps import disagree, measure
from dmbot.maps import measure_words as words
from dmbot.memory import world_notes as wn
from dmbot.memory.models import MemoryRuleError
from tests.test_map_measure import a_map, pins
from tests.test_map_pins import PinsBase
from tests.test_map_store import GUILD_A


class TheLimits(unittest.TestCase):
    def test_the_sixteen_points(self) -> None:
        self.assertEqual(disagree.angle_of("north"), 0.0)
        for said in ("north-east", "north east", "Northeast", "NORTH-EAST"):
            self.assertEqual(disagree.angle_of(said), 45.0, said)
        self.assertEqual(disagree.angle_of("east-north-east"), 67.5)
        self.assertEqual(disagree.angle_of("north-north-west"), 337.5)
        self.assertIsNone(disagree.angle_of("up"))
        self.assertIsNone(disagree.angle_of("northward bound"))

    def test_only_lengths_are_compared(self) -> None:
        self.assertEqual(disagree.in_miles(20, "miles"), 20)
        self.assertEqual(disagree.in_miles(5280, "feet"), 1.0)
        self.assertIsNone(disagree.in_miles(2, "days"))  # (a time is never a conflict)
        self.assertIsNone(disagree.in_miles(3, "hours"))

    def test_a_straight_line_flags_only_a_shorter_stated_distance(self) -> None:
        self.assertTrue(disagree.distance_off(20, 35, straight=True))
        self.assertFalse(disagree.distance_off(29, 35, straight=True))  # (within 20%)
        self.assertFalse(disagree.distance_off(28, 35, straight=True))  # (exactly 20%: silence)
        self.assertFalse(disagree.distance_off(60, 35, straight=True))  # (a road is longer)

    def test_a_traced_path_flags_either_way(self) -> None:
        self.assertTrue(disagree.distance_off(20, 35, straight=False))
        self.assertTrue(disagree.distance_off(50, 35, straight=False))
        self.assertFalse(disagree.distance_off(40, 35, straight=False))

    def test_45_degrees_or_more_is_flagged(self) -> None:
        # (the design's example: the map says east-north-east, 67.5°)
        self.assertFalse(disagree.direction_off(45.0, 67.5))  # north-east: fine
        self.assertFalse(disagree.direction_off(90.0, 67.5))  # east: fine
        self.assertTrue(disagree.direction_off(0.0, 67.5))  # north: flagged
        self.assertTrue(disagree.direction_off(0.0, 45.0))  # exactly 45°: flagged
        self.assertFalse(disagree.direction_off(350.0, 20.0))  # (across north: 30° apart)
        self.assertEqual(disagree.apart(350.0, 20.0), 30.0)


class TheMaps(unittest.TestCase):
    def test_a_stated_distance_far_shorter_than_the_map(self) -> None:
        # (the map makes a to b 40 miles in a straight line)
        found = disagree.check_distance([a_map()], pins(), "a", "b", 20)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.map.id, "m1")
        self.assertAlmostEqual(found.measured_miles, 40.0)
        self.assertEqual(found.stated_miles, 20)
        self.assertIsNone(disagree.check_distance([a_map()], pins(), "a", "b", 35))
        self.assertIsNone(disagree.check_distance([a_map()], pins(), "a", "b", 90))

    def test_no_scale_or_no_shared_map_is_silence(self) -> None:
        self.assertIsNone(disagree.check_distance([a_map(per_px=None)], pins(), "a", "b", 1))
        apart = [measure.Pin("m1", "a", 0.5, 0.1), measure.Pin("m2", "b", 0.5, 0.5)]
        two = [a_map(), a_map(id="m2")]
        self.assertIsNone(disagree.check_distance(two, apart, "a", "b", 1))

    def test_a_feet_map_is_compared_in_miles(self) -> None:
        town = a_map(kind="settlement", per_px=10.0, unit="feet")  # (4,000 feet: about 0.76 mile)
        self.assertIsNotNone(disagree.check_distance([town], pins(), "a", "b", 0.5))
        self.assertIsNone(disagree.check_distance([town], pins(), "a", "b", 0.7))

    def test_the_map_an_answer_would_use(self) -> None:
        # (the town map is more detailed: it answers, and it agrees with the stated distance)
        town = a_map(id="t", kind="settlement", per_px=0.05)  # (20 miles on this one)
        both = pins() + pins("t")
        self.assertIsNone(disagree.check_distance([a_map(), town], both, "a", "b", 19))

    def test_a_direction_far_off(self) -> None:
        # (a is due north of b on the map)
        found = disagree.check_direction([a_map()], pins(), "a", "b", 90.0)  # "a is east of b"
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.map_way, "north")
        self.assertIsNone(disagree.check_direction([a_map()], pins(), "a", "b", 22.5))

    def test_a_north_assumed_is_never_compared(self) -> None:
        self.assertIsNone(
            disagree.check_direction([a_map(north_known=False)], pins(), "a", "b", 180.0)
        )


class TheWords(unittest.TestCase):
    def test_the_card_line_and_the_note_line(self) -> None:
        # (the ux-critic: side by side, "about" and "in a straight line", never "wrong")
        self.assertEqual(
            words.map_measures("35 miles", "Ten-Towns"),
            "On the Ten-Towns map it's about 35 miles in a straight line.",
        )
        self.assertEqual(
            words.road_note("20 miles", False, "35 miles", "Ten-Towns"),
            "your lore says 20 miles; on the Ten-Towns map, about 35 miles in a straight line",
        )
        said = words.road_note("20 miles", True, "35 miles", "Ten-Towns")
        self.assertTrue(said.startswith("said in a session: 20 miles"), said)  # (never "you said")
        for line in (said, words.map_measures("35 miles", "")):
            self.assertNotIn("wrong", line.casefold())
            self.assertNotIn("pin", line.casefold())

    def test_the_maps_figure_never_says_zero(self) -> None:
        found = disagree.Distance(a_map(), 0.1, 0.4)
        self.assertEqual(disagree.amount(found), "2110 feet")  # (0.4 mile, in feet)
        self.assertEqual(disagree.amount(disagree.Distance(a_map(), 20, 35.2)), "35 miles")


class Roads(PinsBase):  # (Postgres: the World notes line and the card's check, worked out on read)
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bryn = await self.place("Bryn Shander")
        await self.record()  # (Targos and Bryn Shander pinned: 60 miles apart on the map)
        made = await self.memory.add_relation(
            GUILD_A, self.one.id, self.targos, "route_to", self.bryn, source="dm",
            confidence=1.0, status="confirmed",
        )  # fmt: skip
        self.road = made.value[0].id

    async def length(self, words_: str, *, by: str = "extractor") -> None:
        await self.memory.set_route_length(
            GUILD_A, self.one.id, self.road, words_, on="connection", source=by
        )

    async def notes(self, roads: bool = True) -> list[wn.Note]:
        found = await self.memory.world_notes(GUILD_A, self.one.id, with_roads=roads)
        return [n for n in found if n.kind == wn.ROAD]

    async def test_a_read_length_far_shorter_is_a_world_note(self) -> None:
        await self.length("20 miles")
        (note,) = await self.notes()
        # (a road is stored with its two places in either order: the line names both)
        self.assertIn(note.name, ("Targos to Bryn Shander", "Bryn Shander to Targos"))
        self.assertEqual((note.value, note.measured), ("20 miles", "60 miles"))
        self.assertEqual(note.relation_id, self.road)
        self.assertEqual(await self.notes(roads=False), [])  # (only while the switch is on)

    async def test_within_the_limit_or_longer_is_silence(self) -> None:
        for said in ("50 miles", "300 miles"):  # (a road is longer than a straight line)
            await self.length(said)
            self.assertEqual(await self.notes(), [], said)

    async def test_a_travel_time_is_never_compared(self) -> None:
        for said in ("two days by cart", "3 hours"):
            await self.length(said)
            self.assertEqual(await self.notes(), [], said)
            self.assertIsNone(await self.maps.road_off(GUILD_A, self.one.id, self.road))

    async def test_keep_makes_it_the_dms_and_the_note_goes(self) -> None:
        await self.length("20 miles")
        (note,) = await self.notes()
        kept = await self.memory.keep_world_notes(GUILD_A, self.one.id, [note.id], source="dm")
        self.assertEqual(kept.value, 1)
        self.assertEqual(await self.notes(), [])  # (a kept length is never flagged again)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT length_words, length_from FROM memory_relations WHERE id = %s", (self.road,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual((row["length_words"], row["length_from"]), ("20 miles", "dm"))

    async def test_the_dms_own_length_gets_the_card_line_but_no_note(self) -> None:
        await self.length("20 miles", by="dm")
        self.assertEqual(await self.notes(), [])
        off = await self.maps.road_off(GUILD_A, self.one.id, self.road)
        assert off is not None
        self.assertEqual((off.stated, disagree.amount(off.off)), ("20 miles", "60 miles"))

    async def test_a_road_note_has_no_not_right(self) -> None:
        await self.length("20 miles")
        (note,) = await self.notes()
        with self.assertRaises(MemoryRuleError):
            await self.memory.turn_down_world_note(GUILD_A, self.one.id, note.id, source="dm")

    async def test_the_questions_count_matches_the_screen(self) -> None:
        from dmbot.questions.counts import Sources, _world

        await self.length("20 miles")
        on = await _world(Sources(self.db, self.memory, map_roads=True), GUILD_A, self.one.id)
        off = await _world(Sources(self.db, self.memory), GUILD_A, self.one.id)
        self.assertEqual(on.n - off.n, 1)

    async def test_a_note_the_map_now_agrees_with_is_gone(self) -> None:
        await self.length("20 miles")
        (note,) = await self.notes()
        async with self.db.guild(GUILD_A) as conn:  # (the DM set the map's scale: 20 miles across)
            await conn.execute("UPDATE campaign_maps SET scale_value = 0.03")
        with self.assertRaises(MemoryRuleError):
            await self.memory.keep_world_notes(GUILD_A, self.one.id, [note.id], source="dm")

    async def test_a_road_still_waiting_is_never_a_note(self) -> None:
        await self.length("20 miles")
        async with self.db.guild(GUILD_A) as conn:  # (asked about on its own, never twice)
            await conn.execute(
                "UPDATE memory_relations SET status = 'proposed' WHERE id = %s", (self.road,)
            )
        self.assertEqual(await self.notes(), [])

    async def test_a_map_the_dm_said_no_to_is_never_used(self) -> None:
        await self.length("20 miles")
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE campaign_maps SET use_ok = FALSE")
        self.assertEqual(await self.notes(), [])


def road_note(session_at: int | None = None) -> wn.Note:
    return wn.Note(
        "road:r", wn.ROAD, "a", "Targos to Bryn Shander", "20 miles", session_at=session_at,
        relation_id="r", map_title="Ten-Towns", measured="35 miles",
    )  # fmt: skip


class TheScreen(unittest.IsolatedAsyncioTestCase):  # (World notes in Discord: a road's line)
    def test_the_line_and_the_menu_choice(self) -> None:
        from dmbot.ui import world_notes as screen

        self.assertEqual(
            screen.full_line(road_note()),
            "**Targos to Bryn Shander** · your lore says 20 miles; on the Ten-Towns map, about"
            " 35 miles in a straight line",
        )
        self.assertIn("said in a session: 20 miles", screen.full_line(road_note(5)))
        got = screen.option(road_note())
        self.assertEqual(
            (got.label, got.description),
            ("Targos to Bryn Shander: 20 miles", "Road length · from your lore"),
        )
        self.assertEqual(
            screen.done_text(road_note(), kept=True),
            "✅ Kept: **Targos to Bryn Shander** · 20 miles.",
        )
        self.assertEqual(screen.keep_all_label(2, wn.ROAD), "✅ Keep all 2 roads")

    async def test_a_road_note_offers_keep_and_change_but_no_not_right(self) -> None:
        from dmbot.ui import world_notes as screen

        labels = [getattr(i, "label", None) for i in screen.OneNote("c", road_note()).children]
        self.assertEqual(labels, [screen.KEEP, screen.CHANGE, screen.BACK])


class TheCard(unittest.IsolatedAsyncioTestCase):  # (the road card's line after the DM's length)
    def found(self) -> Any:
        return SimpleNamespace(
            campaign=SimpleNamespace(guild_id=1, id="c"), relation=SimpleNamespace(id="r")
        )

    def press(self, *, on: bool, off: Any, site: str = "https://example.com") -> Any:
        async def road_off(guild_id: int, campaign_id: str, relation_id: str) -> Any:
            if off is Exception:
                raise RuntimeError("boom")
            return off

        client = SimpleNamespace(
            settings=SimpleNamespace(map_measure_on=on, site_url=site),
            map_store=SimpleNamespace(road_off=road_off),
        )
        return SimpleNamespace(client=client)

    async def test_the_line_and_its_link(self) -> None:
        from dmbot.maps.store import RoadOff
        from dmbot.ui.connection_edit import map_line

        off = disagree.Distance(a_map(title="Ten-Towns"), 20, 35.2)
        road = RoadOff("r", "a", "b", "20 miles", None, 0, off)
        got = await map_line(self.press(on=True, off=road), self.found())
        self.assertEqual(
            got,
            "\nOn the Ten-Towns map it's about 35 miles in a straight line."
            "\n🗺️ Open the map: <https://example.com/account/maps>",
        )

    async def test_nothing_when_off_within_the_limit_or_failing(self) -> None:
        from dmbot.maps.store import RoadOff
        from dmbot.ui.connection_edit import map_line

        off = disagree.Distance(a_map(), 20, 35.2)
        road = RoadOff("r", "a", "b", "20 miles", None, 0, off)
        self.assertEqual(await map_line(self.press(on=False, off=road), self.found()), "")
        self.assertEqual(await map_line(self.press(on=True, off=None), self.found()), "")
        self.assertEqual(await map_line(self.press(on=True, off=Exception), self.found()), "")
