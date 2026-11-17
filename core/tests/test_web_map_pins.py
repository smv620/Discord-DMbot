"""A map's pins, scale, grid and north on the website (#4672, migration 0531): a read door for a DM
of the campaign and nobody else, that names each pin's target in words, leaves a turned-down pin
out, and shows no entry id. Invented names. Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.web import maps as web_maps
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB
from tests.test_web_api import THURSDAY
from tests.test_web_maps import MapsDoor


class Pins(MapsDoor):
    async def pin(
        self, label: str, x: float, y: float, *, place: str | None = None, status: str = "read",
        source: str = "read", map_id: str | None = None, n: int = 1,
    ) -> str:  # fmt: skip
        fid = f"{n:032x}"
        await self.sql(
            "INSERT INTO map_features (guild_id, campaign_id, id, map_id, shape, points, place_id,"
            " label, status, source, created_at) VALUES (%s, %s, %s, %s, 'pin', %s, %s, %s, %s, %s,"
            " %s)",
            self.g, self.one.id, fid, map_id or self.dm_map, [x, y], place, label, status, source,
            100 + n,
        )  # fmt: skip
        return fid

    async def features(self, map_id: str | None = None, who: Any = None) -> dict[str, Any]:
        return await web_maps.read_features(
            self.web, who or self.session, self.cref, map_id or self.dm_map
        )


class TheDoor(Pins):
    async def test_a_pin_is_a_label_a_point_and_its_place_in_words(self) -> None:
        place = await self.add("Targos", "place")
        await self.pin("Targos", 0.2, 0.5, place=place, n=1)
        await self.pin("Kelvin's Cairn", 0.7, 0.1, n=2)
        got = await self.features()
        by_label = {f["label"]: f for f in got["features"]}
        targos = by_label["Targos"]
        self.assertEqual((targos["shape"], targos["points"]), ("pin", [0.2, 0.5]))
        self.assertEqual((targos["targetKind"], targos["target"]), ("place", "Targos"))
        self.assertEqual((targos["status"], targos["source"]), ("read", "read"))
        cairn = by_label["Kelvin's Cairn"]
        self.assertEqual((cairn["targetKind"], cairn["target"]), (None, None))  # (not matched)
        self.assertNotIn(place, str(got))  # (no entry id leaves the door)

    async def test_a_turned_down_pin_is_not_listed(self) -> None:
        await self.pin("Old mark", 0.3, 0.3, status="rejected")
        await self.pin("Kept mark", 0.4, 0.4, status="confirmed", source="dm", n=2)
        self.assertEqual([f["label"] for f in (await self.features())["features"]], ["Kept mark"])

    async def test_the_scale_grid_and_north_come_with_where_each_came_from(self) -> None:
        bare = await self.features()
        self.assertIsNone(bare["scale"])
        self.assertEqual(bare["grid"]["kind"], "none")
        self.assertEqual((bare["north"]["angle"], bare["north"]["from"]), (0, "assumed"))
        await self.sql(
            "UPDATE campaign_maps SET scale_value = 10, scale_unit = 'miles', scale_from = 'read',"
            " grid_kind = 'hex', grid_cell_px = 30, grid_cell_distance = 6, grid_from = 'dm',"
            " north_angle = 90, north_from = 'read' WHERE guild_id = %s AND id = %s",
            self.g, self.dm_map,
        )  # fmt: skip
        got = await self.features()
        self.assertEqual(got["scale"], {"value": 10, "unit": "miles", "from": "read"})
        self.assertEqual(got["grid"]["kind"], "hex")
        self.assertEqual(
            (got["grid"]["cellPx"], got["grid"]["cellDistance"], got["grid"]["from"]),
            (30, 6, "dm"),
        )
        self.assertEqual(got["north"], {"angle": 90, "from": "read"})

    async def test_only_this_maps_pins_and_only_this_campaigns_map(self) -> None:
        await self.pin("Here", 0.1, 0.1, n=1)
        await self.pin("Elsewhere", 0.9, 0.9, map_id=self.shown, n=2)
        self.assertEqual([f["label"] for f in (await self.features())["features"]], ["Here"])
        with self.assertRaises(RecapError) as missing:
            await self.features("f" * 32)
        self.assertEqual(missing.exception.code, "not_found")
        with self.assertRaises(RecapError) as bad:
            await self.features("not an id")
        self.assertEqual(bad.exception.code, "not_found")

    async def test_a_player_or_a_stranger_gets_nothing_even_for_a_map_shown_to_the_table(
        self,
    ) -> None:
        await self.pin("Secret spot", 0.5, 0.5, map_id=self.shown, n=1)
        bob = await self.signed(BOB, THURSDAY)
        for map_id in (self.dm_map, self.shown):
            with self.assertRaises(RecapError) as refused:
                await self.features(map_id, who=bob)
            self.assertEqual(refused.exception.code, "not_allowed")


class TheTargets(Pins):
    async def road(self, status: str = "confirmed", campaign: str | None = None) -> tuple[str, str]:
        a = await self.add("Targos", "place")
        b = await self.add("Bryn Shander", "place")
        made = await self.memory.add_relation(
            self.g, campaign or self.one.id, a, "route_to", b, source="dm", confidence=1.0,
            status=status,
        )  # fmt: skip
        return made.value[0].id, a

    async def road_pin(self, relation: str, n: int = 1) -> None:
        await self.sql(
            "INSERT INTO map_features (guild_id, campaign_id, id, map_id, shape, points,"
            " relation_id, label, status, source, created_at)"
            " VALUES (%s, %s, %s, %s, 'path', %s, %s, 'Road', 'read', 'read', 100)",
            self.g, self.one.id, f"{n:032x}", self.dm_map, [0.1, 0.1, 0.9, 0.9], relation,
        )  # fmt: skip

    async def test_a_kept_road_names_its_two_ends_and_is_a_path(self) -> None:
        relation, _ = await self.road()
        await self.road_pin(relation)
        (road,) = (await self.features())["features"]
        self.assertEqual((road["shape"], road["points"]), ("path", [0.1, 0.1, 0.9, 0.9]))
        self.assertEqual((road["targetKind"], road["target"]), ("road", "Bryn Shander to Targos"))

    async def test_a_road_still_waiting_for_the_dm_has_no_target(self) -> None:
        relation, _ = await self.road(status="proposed")
        await self.road_pin(relation)
        (road,) = (await self.features())["features"]
        self.assertEqual((road["targetKind"], road["target"]), (None, None))

    async def test_another_campaigns_map_is_not_found_even_with_a_real_id(self) -> None:
        added = await self.pictures.add(
            self.g, self.two.id, poster_id=self.session.user_id, session_id="", line_id=None,
            message_id=99, description="", consent_version=7, right_granted_at=900,
            sha256=f"{99:064x}", object_key=f"{self.g}/{self.two.id}/{99:064x}.png",
            preview_key="", size_bytes=1, preview_bytes=0, storage_mb=10,
        )  # fmt: skip
        assert added.picture is not None
        made = await self.maps.add_map(
            self.g, self.two.id, picture_id=added.picture.id, uploader_id=self.session.user_id,
            uploader_is_dm=True, right_to_use=True, kind="region", came_from="website",
            storage_mb=10, title="Elsewhere",
        )  # fmt: skip
        with self.assertRaises(RecapError) as missing:
            await self.features(made.value.id)  # (a real map, but not this campaign's)
        self.assertEqual(missing.exception.code, "not_found")
