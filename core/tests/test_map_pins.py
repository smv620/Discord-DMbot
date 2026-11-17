"""Positions on a map (#4670, MapBot layers slice 4a; Supervisor's ruling on #4670): the close
read's pins, scale bar, grid and compass, read and checked; the scale worked out (never guessed);
pins tied only to an exact or kept other name of a kept place; a read adding only where nothing
is; one Undo; a map's removal taking its pins; a name kept later tied to its pin; a merge moving
pins; and the one-time re-read's mark. Invented names. The parse and the sums need no database."""

from __future__ import annotations

import json
import math
import unittest
from typing import Any

from dmbot import images
from dmbot.campaigns import CampaignStore
from dmbot.maps import positions
from dmbot.maps.store import MapStore
from dmbot.memory.models import name_key
from dmbot.memory.store import MemoryStore
from dmbot.pictures.store import PictureStore
from tests import test_map_store as base
from tests.pg import DatabaseTest
from tests.test_map_store import DM, GUILD_A


def close_answer(**more: Any) -> str:
    base: dict[str, Any] = {
        "level": "region",
        "places": [
            {"name": "Targos", "where": "west", "level": "settlement", "form": "land",
             "at": {"x": 0.2, "y": 0.5}},
            {"name": "Bryn Shander", "where": "east", "level": "settlement", "form": "land",
             "at": {"x": 0.8, "y": 0.5}},
            {"name": "Kelvin's Cairn", "where": "north", "level": "", "form": "land",
             "at": {"x": -1, "y": -1}},
        ],
        "links": [],
        "layout": "Two towns and a mountain.",
        "scale": {"distance": 10, "unit": "Miles", "x1": 0.1, "y1": 0.9, "x2": 0.2, "y2": 0.9},
        "grid": {"kind": "none", "across": 0, "distance": 0, "unit": ""},
        "north": -1,
    }  # fmt: skip
    base.update(more)
    return json.dumps(base)


class TheRead(unittest.TestCase):
    def test_pins_scale_and_no_compass(self) -> None:
        read = images.parse_close_map(close_answer())
        assert read is not None
        self.assertEqual([p.at for p in read.places], [(0.2, 0.5), (0.8, 0.5), None])
        assert read.scale is not None
        self.assertEqual((read.scale.distance, read.scale.unit), (10.0, "miles"))
        self.assertIsNone(read.north)
        self.assertIsNone(read.grid)

    def test_a_compass_and_a_grid(self) -> None:
        read = images.parse_close_map(
            close_answer(north=370, grid={"kind": "hex", "across": 20, "distance": 6, "unit": "mi"})
        )
        assert read is not None and read.grid is not None
        self.assertEqual(read.north, 10.0)
        self.assertEqual((read.grid.kind, read.grid.across, read.grid.unit), ("hex", 20.0, "miles"))

    def test_bad_values_are_no_values(self) -> None:
        for scale in (
            {"distance": 10, "unit": "leagues", "x1": 0.1, "y1": 0.9, "x2": 0.2, "y2": 0.9},
            {"distance": 0, "unit": "miles", "x1": 0.1, "y1": 0.9, "x2": 0.2, "y2": 0.9},
            {"distance": 10, "unit": "miles", "x1": 0.1, "y1": 0.9, "x2": 0.1, "y2": 0.9},
            {"distance": 10, "unit": "miles", "x1": 1.5, "y1": 0.9, "x2": 0.2, "y2": 0.9},
            {"distance": True, "unit": "miles", "x1": 0.1, "y1": 0.9, "x2": 0.2, "y2": 0.9},
        ):
            with self.subTest(scale=scale):
                read = images.parse_close_map(close_answer(scale=scale))
                assert read is not None
                self.assertIsNone(read.scale)
        read = images.parse_close_map(
            close_answer(
                places=[
                    {"name": "A", "where": "", "level": "", "form": "", "at": {"x": 2, "y": 0.5}}
                ]
            )
        )
        assert read is not None
        self.assertIsNone(read.places[0].at)

    def test_an_old_answer_without_them_still_reads(self) -> None:
        data = json.loads(close_answer())
        for key in ("scale", "grid", "north"):
            del data[key]
        for p in data["places"]:
            del p["at"]
        read = images.parse_close_map(json.dumps(data))
        assert read is not None
        self.assertEqual((read.scale, read.grid, read.north), (None, None, None))
        self.assertEqual({p.at for p in read.places}, {None})


class TheSums(unittest.TestCase):
    def read(self, **more: Any) -> images.Read:
        got = images.parse_close_map(close_answer(**more))
        assert got is not None
        return got

    def test_a_scale_bar_over_its_drawn_length(self) -> None:
        scale = positions.scale_from(self.read(), 1000, 800)
        assert scale is not None
        self.assertTrue(math.isclose(scale.per_px, 10 / 100))  # (0.1 of 1000 px wide)
        self.assertEqual(scale.unit, "miles")

    def test_a_grid_with_a_written_cell(self) -> None:
        read = self.read(
            scale={"distance": 0, "unit": "", "x1": 0, "y1": 0, "x2": 0, "y2": 0},
            grid={"kind": "hex", "across": 20, "distance": 6, "unit": "miles"},
        )
        scale = positions.scale_from(read, 1000, 800)
        assert scale is not None
        self.assertTrue(math.isclose(scale.per_px, 6 / 50))

    def test_no_scale_and_no_size_is_no_number(self) -> None:
        read = self.read(scale={"distance": 0, "unit": "", "x1": 0, "y1": 0, "x2": 0, "y2": 0})
        self.assertIsNone(positions.scale_from(read, 1000, 800))
        self.assertIsNone(positions.scale_from(self.read(), None, None))

    def test_names_are_kept_places_only_and_never_two(self) -> None:
        entities = [
            {"id": "a", "name": "Targos", "type": "settlement", "status": "confirmed"},
            {"id": "b", "name": "Bryn Shander", "type": "settlement", "status": "proposed"},
            {"id": "c", "name": "Lonelywood", "type": "settlement", "status": "confirmed"},
            {"id": "d", "name": "Lonelywood", "type": "settlement", "status": "confirmed"},
            {"id": "e", "name": "Cerric", "type": "character", "status": "confirmed"},
        ]
        aliases = [{"entity_id": "a", "text": "Targos Town", "status": "confirmed"}]
        names = positions.place_names(entities, aliases, lambda t: t == "settlement")
        self.assertEqual(names[name_key("Targos")], "a")
        self.assertEqual(names[name_key("Targos Town")], "a")
        self.assertNotIn(name_key("Bryn Shander"), names)  # (a waiting name: never a target)
        self.assertIsNone(names[name_key("Lonelywood")])  # (two: no pick)
        self.assertNotIn(name_key("Cerric"), names)


class Pins(DatabaseTest):
    # (`test_map_store.Maps`'s set-up and helpers, without running its tests again)
    picture = base.Maps.picture
    add = base.Maps.add

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.pictures = PictureStore(self.db, clock=lambda: 1000.0)
        self.memory = MemoryStore(self.db)
        self.maps = MapStore(self.db, self.memory, clock=lambda: 2000.0)
        self.one = await self.campaigns.create(GUILD_A, "One", DM)
        self.two = await self.campaigns.create(GUILD_A, "Two", DM)
        self.map_id = await self.add(
            await self.picture(self.one.id, 1), dm=True, width=1000, height=800
        )
        self.targos = await self.place("Targos")

    async def place(self, name: str, status: str = "confirmed") -> str:
        made = await self.memory.add_entity(
            GUILD_A, self.one.id, name=name, type="place", status=status, source="dm"
        )
        return made.value.id

    async def record(self, **more: Any) -> positions.Recorded:
        read = images.parse_close_map(close_answer(**more))
        assert read is not None
        got = await self.maps.record_positions(GUILD_A, self.one.id, self.map_id, read)
        assert isinstance(got, positions.Recorded)
        return got

    async def features(self) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT * FROM map_features ORDER BY label")
            return [dict(r) for r in await cur.fetchall()]

    async def map_row(self) -> dict[str, Any]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT * FROM campaign_maps WHERE id = %s", (self.map_id,))
            row = await cur.fetchone()
        assert row is not None
        return dict(row)

    async def test_pins_scale_and_the_mark_are_written(self) -> None:
        got = await self.record()
        self.assertEqual((got.pins, got.matched, got.scale, got.north), (2, 1, True, False))
        bryn, targos = await self.features()
        self.assertEqual((targos["place_id"], targos["points"]), (self.targos, [0.2, 0.5]))
        self.assertIsNone(bryn["place_id"])  # (no kept place by that name: unmatched)
        self.assertEqual((targos["status"], targos["source"]), ("read", "read"))
        row = await self.map_row()
        self.assertEqual((row["scale_unit"], row["scale_from"]), ("miles", "read"))
        self.assertEqual(row["north_from"], "assumed")
        self.assertEqual(row["pins_read_at"], 2000)

    async def test_a_read_adds_only_where_nothing_is(self) -> None:
        await self.record()
        async with self.db.guild(GUILD_A) as conn:  # (as if the DM had set the scale)
            await conn.execute(
                "UPDATE campaign_maps SET scale_value = 1, scale_from = 'dm' WHERE id = %s",
                (self.map_id,),
            )
        got = await self.record(north=90)
        self.assertEqual((got.pins, got.kept, got.scale, got.north), (0, 2, False, True))
        self.assertEqual(len(await self.features()), 2)
        self.assertEqual((await self.map_row())["scale_value"], 1)

    async def test_one_undo_takes_the_read_back(self) -> None:
        await self.record()
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT max(batch) AS b FROM memory_changes WHERE campaign_id = %s", (self.one.id,)
            )
            row = await cur.fetchone()
        assert row is not None
        await self.memory.undo(GUILD_A, self.one.id, int(row["b"]), source="dm")
        self.assertEqual(await self.features(), [])
        self.assertIsNone((await self.map_row())["scale_from"])

    async def test_removing_the_map_takes_its_pins_and_undo_brings_them_back(self) -> None:
        await self.record()
        gone = await self.maps.remove_map(GUILD_A, self.one.id, self.map_id)
        self.assertEqual(await self.features(), [])
        assert gone.batch is not None
        await self.memory.undo(GUILD_A, self.one.id, gone.batch, source="dm")
        self.assertEqual(len(await self.features()), 2)

    async def test_a_name_kept_later_is_tied_to_its_pin(self) -> None:
        bryn = await self.place("Bryn Shander", status="proposed")
        await self.record()
        self.assertIsNone((await self.features())[0]["place_id"])  # (waiting: never a target)
        await self.memory.set_entity_status(GUILD_A, self.one.id, bryn, "confirmed", source="dm")
        self.assertEqual((await self.features())[0]["place_id"], bryn)

    async def test_a_merge_moves_the_pin(self) -> None:
        await self.record()
        other = await self.place("Targos the Town")
        await self.memory.merge(
            GUILD_A, self.one.id, other, self.targos, source="dm", dm_said_same=True
        )
        targos = (await self.features())[1]
        self.assertEqual(targos["place_id"], other)

    async def test_the_one_time_reread_mark(self) -> None:
        async with self.db.guild(GUILD_A) as conn:  # (read closely before pins were read)
            await conn.execute(
                "UPDATE campaign_maps SET close_read_at = 5, close_read_tries = 0 WHERE id = %s",
                (self.map_id,),
            )
        waiting = await self.maps.pins_waiting(GUILD_A)
        self.assertEqual([m.id for _c, m in waiting], [self.map_id])
        self.assertTrue(await self.maps.claim_pins(GUILD_A, self.one.id, self.map_id))
        self.assertFalse(await self.maps.claim_pins(GUILD_A, self.one.id, self.map_id))
        self.assertEqual(await self.maps.pins_waiting(GUILD_A), [])
        await self.maps.release_pins(GUILD_A, self.one.id, self.map_id)  # (put off)
        self.assertEqual(len(await self.maps.pins_waiting(GUILD_A)), 1)

    async def test_another_campaign_sees_no_pin(self) -> None:
        await self.record()
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM map_features WHERE campaign_id = %s", (self.two.id,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)
