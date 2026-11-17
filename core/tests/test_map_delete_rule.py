"""When a map is deleted (#4259, MapBot slice 8b; docs/design/mapbot_layers.md §7.5; Supervisor's
ruling on #4236 f): DMbot's read resting only on that map goes with it; a read the text also stated
stays as the text's; the DM's own word stays, without the pointer; one Undo puts it all back. A
picture deleted with its map follows the same rule, (backups: test_map_facts).
Invented names; needs Postgres."""

from __future__ import annotations

import time
import uuid
from typing import Any

from tests.test_map_pins import PinsBase
from tests.test_map_store import GUILD_A


class Facts(PinsBase):
    async def fact(self, value: str, *, status: str, source: str, also_text: bool) -> str:
        fact_id = uuid.uuid4().hex
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_map_facts (guild_id, campaign_id, id, entity_id, layer, value,"
                " status, source, also_text, created_at, map_id)"
                " VALUES (%s, %s, %s, %s, 'terrain', %s, %s, %s, %s, %s, %s)",
                (
                    GUILD_A, self.one.id, fact_id, self.targos, value, status, source, also_text,
                    int(time.time()), self.map_id,
                ),
            )  # fmt: skip
        return fact_id

    async def row(self, fact_id: str) -> dict[str, Any] | None:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT status, source, map_id FROM memory_map_facts WHERE id = %s", (fact_id,)
            )
            got = await cur.fetchone()
        return dict(got) if got is not None else None

    async def version(self) -> int:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT memory_version FROM campaigns WHERE id = %s", (self.one.id,)
            )
            got = await cur.fetchone()
        assert got is not None
        return int(got["memory_version"])

    async def three(self) -> tuple[str, str, str]:
        only_map = await self.fact("arctic", status="read", source="map", also_text=False)
        text_too = await self.fact("mountain", status="read", source="map", also_text=True)
        dms = await self.fact("forest", status="confirmed", source="map", also_text=False)
        return only_map, text_too, dms


class TheRule(Facts):
    async def test_each_of_the_three_cases(self) -> None:
        only_map, text_too, dms = await self.three()
        await self.maps.remove_map(GUILD_A, self.one.id, self.map_id)
        self.assertIsNone(await self.row(only_map))  # (rests only on the map: it goes)
        self.assertEqual(
            await self.row(text_too), {"status": "read", "source": "text", "map_id": None}
        )  # (the text said it too: it stays, as the text's)
        self.assertEqual(
            await self.row(dms), {"status": "confirmed", "source": "map", "map_id": None}
        )  # (the DM's word stays, without the pointer)

    async def other_area(self, fact_id: str, *, usable: bool = True) -> tuple[str, str]:
        other = await self.add(await self.picture(self.one.id, 2), dm=True, width=10, height=10)
        feature = uuid.uuid4().hex
        async with self.db.guild(GUILD_A) as conn:  # (another map's area pointing at the fact)
            await conn.execute(
                "INSERT INTO map_features (guild_id, campaign_id, id, map_id, shape, points,"
                " fact_id, status, source, created_at) VALUES (%s, %s, %s, %s, 'area',"
                " ARRAY[0.1, 0.1, 0.2, 0.1, 0.2, 0.2], %s, 'read', 'read', %s)",
                (GUILD_A, self.one.id, feature, other, fact_id, int(time.time())),
            )
            if not usable:
                await conn.execute(
                    "UPDATE campaign_maps SET use_ok = FALSE WHERE id = %s", (other,)
                )
        return other, feature

    async def area_points_at(self, feature: str) -> str | None:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT fact_id FROM map_features WHERE id = %s", (feature,))
            got = await cur.fetchone()
        assert got is not None
        return None if got["fact_id"] is None else str(got["fact_id"])

    async def test_a_fact_another_map_shows_is_kept_and_re_pointed(self) -> None:
        # (Supervisor's ruling 2 on #4694, #4696: it rests on that map now; its area untouched)
        only_map = await self.fact("arctic", status="read", source="map", also_text=False)
        other, feature = await self.other_area(only_map)
        written = await self.maps.remove_map(GUILD_A, self.one.id, self.map_id)
        self.assertEqual(
            await self.row(only_map), {"status": "read", "source": "map", "map_id": other}
        )
        self.assertEqual(await self.area_points_at(feature), only_map)
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.one.id, written.batch)  # (the pointer back)
        self.assertEqual(
            await self.row(only_map), {"status": "read", "source": "map", "map_id": self.map_id}
        )

    async def test_a_map_dmbot_may_not_use_does_not_keep_it(self) -> None:
        only_map = await self.fact("arctic", status="read", source="map", also_text=False)
        _, feature = await self.other_area(only_map, usable=False)
        await self.maps.remove_map(GUILD_A, self.one.id, self.map_id)
        self.assertIsNone(await self.row(only_map))
        self.assertIsNone(await self.area_points_at(feature))

    async def test_a_deleted_picture_re_points_it_too(self) -> None:
        only_map = await self.fact("arctic", status="read", source="map", also_text=False)
        other, feature = await self.other_area(only_map)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT picture_id FROM campaign_maps WHERE id = %s", (self.map_id,)
            )
            picture = await cur.fetchone()
        assert picture is not None
        await self.pictures.delete(GUILD_A, self.one.id, int(picture["picture_id"]))
        self.assertEqual(
            await self.row(only_map), {"status": "read", "source": "map", "map_id": other}
        )
        self.assertEqual(await self.area_points_at(feature), only_map)

    async def test_one_undo_puts_it_all_back(self) -> None:
        only_map, text_too, dms = await self.three()
        written = await self.maps.remove_map(GUILD_A, self.one.id, self.map_id)
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.one.id, written.batch)
        for fact_id, status in ((only_map, "read"), (text_too, "read"), (dms, "confirmed")):
            self.assertEqual(
                await self.row(fact_id), {"status": status, "source": "map", "map_id": self.map_id}
            )

    async def test_deleting_the_picture_follows_the_same_rule(self) -> None:
        only_map, text_too, dms = await self.three()
        rejected = await self.fact("hill", status="rejected", source="map", also_text=False)
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT picture_id FROM campaign_maps WHERE id = %s", (self.map_id,)
            )
            picture = await cur.fetchone()
        assert picture is not None
        before = await self.version()
        await self.pictures.delete(GUILD_A, self.one.id, int(picture["picture_id"]))
        self.assertGreater(await self.version(), before)  # (World notes draw these: it moves)
        self.assertIsNone(await self.row(only_map))
        self.assertEqual(
            await self.row(text_too), {"status": "read", "source": "text", "map_id": None}
        )
        for kept, status in ((dms, "confirmed"), (rejected, "rejected")):  # (the DM's word and no)
            self.assertEqual(
                await self.row(kept), {"status": status, "source": "map", "map_id": None}
            )

    async def test_the_dms_no_stays_without_the_pointer(self) -> None:
        rejected = await self.fact("hill", status="rejected", source="map", also_text=False)
        await self.maps.remove_map(GUILD_A, self.one.id, self.map_id)
        self.assertEqual(
            await self.row(rejected), {"status": "rejected", "source": "map", "map_id": None}
        )


class TheNo(Facts):
    """A DM's No to a map runs the rule on what it said (#4695; Supervisor's ruling 1 on #4694):
    the map row stays, refused, and one Undo puts everything back."""

    async def test_yes_then_no_runs_the_rule_and_keeps_the_refused_map(self) -> None:
        await self.record()  # (pins on the map, to come back with the Undo)
        pins = await self.features()
        self.assertTrue(pins)
        only_map, text_too, dms = await self.three()
        written = await self.maps.answer_use(GUILD_A, self.one.id, self.map_id, dm_id=7, yes=False)
        self.assertIsNone(await self.row(only_map))
        self.assertEqual(
            await self.row(text_too), {"status": "read", "source": "text", "map_id": None}
        )
        self.assertEqual(
            await self.row(dms), {"status": "confirmed", "source": "map", "map_id": None}
        )
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT use_ok FROM campaign_maps WHERE id = %s", (self.map_id,)
            )
            kept = await cur.fetchone()
        assert kept is not None
        self.assertIs(kept["use_ok"], False)  # (refused, never read again)
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.one.id, written.batch)
        for fact_id, status in ((only_map, "read"), (text_too, "read"), (dms, "confirmed")):
            self.assertEqual(
                await self.row(fact_id), {"status": status, "source": "map", "map_id": self.map_id}
            )
        self.assertEqual(await self.features(), pins)  # (the pins back as they were)
        self.assertIs((await self.map_row())["use_ok"], True)  # (and the answer before the No)

    async def test_a_read_finishing_after_the_no_writes_nothing(self) -> None:
        # (the caller's check came before the No: asked again under the lock)
        await self.maps.answer_use(GUILD_A, self.one.id, self.map_id, dm_id=7, yes=False)
        got = await self.record()
        self.assertEqual(got.pins, 0)
        self.assertEqual(await self.features(), [])

    async def test_a_refused_map_gives_no_pins_no_layers_and_no_how_far(self) -> None:
        # (Supervisor's condition on #4695: everything goes through `may_use_map`)
        from dmbot.maps import measure_phrase
        from dmbot.maps.store import Viewer, may_use_map

        await self.place("Bryn Shander")
        await self.record()  # (pinned and a scale read while it was a yes)
        asked = measure_phrase.read("how far is Targos from Bryn Shander?")
        assert asked is not None
        said = await self.maps.measure(GUILD_A, self.one.id, asked, dm=True)
        assert isinstance(said, str) and said.startswith("About "), said
        async with self.db.guild(GUILD_A) as conn:  # (and owed a fresh pins read, as a re-read)
            await conn.execute(
                "UPDATE campaign_maps SET close_read_at = NULL, close_read_tries = 0,"
                " pins_read_at = NULL"
            )
        await self.maps.answer_use(GUILD_A, self.one.id, self.map_id, dm_id=7, yes=False)
        refused = await self.maps.get(GUILD_A, self.one.id, self.map_id, Viewer(dm=True))
        assert refused is not None
        self.assertFalse(may_use_map(refused))
        self.assertEqual(await self.features(), [])  # (its pins went with the No)
        waiting = [m.id for _, m in await self.maps.close_read_waiting(GUILD_A)]
        waiting += [m.id for _, m in await self.maps.pins_waiting(GUILD_A)]
        self.assertNotIn(self.map_id, waiting)  # (never read again: no pins, no layers)
        said = await self.maps.measure(GUILD_A, self.one.id, asked, dm=True)
        assert isinstance(said, str)
        self.assertTrue(said.startswith("I can't find "), said)  # (no "how far" from it)
