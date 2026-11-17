"""What a map shows outright, as layer rows (#4259, MapBot slice 8a; docs/design/mapbot_layers.md
§5.2, §7.1 to 7.4; Supervisor's ruling on #4259): the read's `shows` (a legend, a labelled border, a
colour key or a terrain symbol only), the words each layer allows, kept places and rulers only, and
the store's write (`source = 'map'`, the map's id, the map above the text, the DM's word above the
map, one batch with the pins). Invented names. The last class needs Postgres."""

from __future__ import annotations

import json
import unittest
from typing import Any

from dmbot import images
from dmbot.maps import layers_read
from dmbot.memory import map_facts as mf
from dmbot.memory.models import name_key
from tests.test_map_pins import PinsBase, close_answer
from tests.test_map_store import GUILD_A


def shown(place: str, layer: str, value: str, by: str = "legend") -> images.Shown:
    return images.Shown(place, layer, value, by)


class TheRead(unittest.TestCase):
    def test_only_a_named_place_from_a_legend_border_key_or_symbol(self) -> None:
        raw = json.loads(close_answer())
        raw["shows"] = [
            {"place": "Targos", "layer": "terrain", "value": "Arctic", "from": "symbol"},
            {"place": "Targos", "layer": "control", "value": "Ten-Towns", "from": "banner"},
            {"place": "Luskan", "layer": "terrain", "value": "coastal", "from": "legend"},
            {"place": "Targos", "layer": "weather", "value": "snow", "from": "legend"},
            {"place": "Bryn Shander", "layer": "culture", "value": "", "from": "colour_key"},
        ]
        read = images.parse_close_map(json.dumps(raw))
        assert read is not None
        self.assertEqual(read.shows, (shown("Targos", "terrain", "Arctic", "symbol"),))

    def test_an_older_answer_without_it_shows_nothing(self) -> None:
        read = images.parse_close_map(close_answer())
        assert read is not None
        self.assertEqual(read.shows, ())


PLACES: dict[str, str | None] = {
    name_key("Targos"): "t", name_key("Bryn Shander"): "b", name_key("Twin"): None,
}  # fmt: skip
RULERS: dict[str, str | None] = {name_key("Ten-Towns Council"): "c", name_key("Targos"): "t"}


class TheWords(unittest.TestCase):
    def test_each_layers_own_words(self) -> None:
        got = layers_read.reads(
            [
                shown("Targos", "terrain", "Arctic"), shown("Targos", "climate", "cold"),
                shown("Bryn Shander", "culture", "Elven"),
                shown("Bryn Shander", "population", "dwarves"),
                shown("Bryn Shander", "control", "Ten-Towns Council"),
            ],
            PLACES, RULERS,
        )  # fmt: skip
        self.assertEqual(
            [(r.place_id, r.layer, r.value, r.how) for r in got],
            [
                ("t", mf.TERRAIN, "arctic", ""), ("t", mf.CLIMATE, "cold", ""),
                ("b", mf.CULTURE, "elf", layers_read.MAP_CULTURE_HOW),
                ("b", mf.POPULATION, "dwarf", ""), ("b", mf.CONTROL, "c", "rules"),
            ],
        )  # fmt: skip

    def test_not_sure_means_nothing(self) -> None:
        got = layers_read.reads(
            [
                shown("Targos", "terrain", "icy wastes"),  # (not a word on the list)
                shown("Twin", "terrain", "hill"),  # (a name that is two places)
                shown("Luskan", "terrain", "coastal"),  # (not a kept place)
                shown("Targos", "control", "the Black Banner"),  # (no kept group by that name)
                shown("Targos", "control", "Targos"),  # (a place never rules itself)
                shown("Targos", "terrain", "arctic"), shown("Targos", "terrain", "Arctic"),
            ],
            PLACES, RULERS,
        )  # fmt: skip
        self.assertEqual([(r.layer, r.value) for r in got], [(mf.TERRAIN, "arctic")])


class TheWrite(PinsBase):  # (Postgres: Targos is a kept place; the map is the DM's)
    async def record(self, **more: Any) -> Any:
        read = images.parse_close_map(close_answer(**more))
        assert read is not None
        return await self.maps.record_positions(
            GUILD_A, self.one.id, self.map_id, read, layers=True
        )

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT layer, value, status, source, also_text, map_id, to_session_at"
                " FROM memory_map_facts WHERE entity_id = %s ORDER BY layer, value",
                (self.targos,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def test_written_as_the_maps_read(self) -> None:
        got = await self.record(shows=[
            {"place": "Targos", "layer": "terrain", "value": "arctic", "from": "symbol"}
        ])  # fmt: skip
        self.assertEqual(got.written, {mf.TERRAIN: 1})
        (row,) = await self.rows()
        self.assertEqual(
            (row["value"], row["status"], row["source"], row["also_text"], row["map_id"]),
            ("arctic", mf.READ, mf.FROM_MAP, False, self.map_id),
        )

    async def test_switched_off_writes_nothing(self) -> None:
        read = images.parse_close_map(close_answer(shows=[
            {"place": "Targos", "layer": "terrain", "value": "arctic", "from": "symbol"}
        ]))  # fmt: skip
        assert read is not None
        await self.maps.record_positions(GUILD_A, self.one.id, self.map_id, read)
        self.assertEqual(await self.rows(), [])

    async def test_the_same_word_the_text_read_becomes_the_maps(self) -> None:
        from dmbot.memory.map_fact_store import Read

        await self.memory.add_read_map_facts(
            GUILD_A, self.one.id, [Read(self.targos, mf.TERRAIN, "arctic")], source="extractor"
        )
        await self.record(shows=[
            {"place": "Targos", "layer": "terrain", "value": "arctic", "from": "legend"}
        ])  # fmt: skip
        (row,) = await self.rows()  # (one row, never two)
        self.assertEqual(
            (row["source"], row["also_text"], row["map_id"]), (mf.FROM_MAP, True, self.map_id)
        )

    async def test_the_map_corrects_the_texts_read_but_never_the_dms_word(self) -> None:
        from dmbot.memory.map_fact_store import Read

        await self.memory.add_read_map_facts(
            GUILD_A, self.one.id, [Read(self.targos, mf.TERRAIN, "forest")], source="extractor"
        )
        await self.record(shows=[
            {"place": "Targos", "layer": "terrain", "value": "arctic", "from": "symbol"}
        ])  # fmt: skip
        self.assertEqual(
            [(r["value"], r["status"], r["source"]) for r in await self.rows()],
            [("arctic", mf.READ, mf.FROM_MAP), ("forest", mf.REJECTED, mf.FROM_TEXT)],
        )
        await self.memory.set_map_fact(
            GUILD_A, self.one.id, self.targos, mf.TERRAIN, "hill", source="dm"
        )
        got = await self.record(shows=[
            {"place": "Targos", "layer": "terrain", "value": "mountain", "from": "symbol"}
        ])  # fmt: skip
        self.assertEqual(got.layers.get("noted"), 1)  # (against the DM's word: World notes)
        self.assertNotIn("mountain", [r["value"] for r in await self.rows()])

    async def test_one_undo_takes_the_pins_and_the_rows_back(self) -> None:
        got = await self.record(shows=[
            {"place": "Targos", "layer": "terrain", "value": "arctic", "from": "symbol"}
        ])  # fmt: skip
        self.assertEqual(got.written, {mf.TERRAIN: 1})
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT max(batch) AS b FROM memory_changes WHERE table_name = 'memory_map_facts'"
            )
            batch = (await cur.fetchone() or {}).get("b")
        assert batch is not None
        await self.memory.undo(GUILD_A, self.one.id, int(batch))
        self.assertEqual(await self.rows(), [])
        self.assertEqual(await self.features(), [])
