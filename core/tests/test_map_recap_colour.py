"""Recap colour (#4260, MapBot layers slice 10b; Supervisor's ruling on #4260): one plain line for
each place a session visited, from the players' view only. Invented names. The last class needs
Postgres."""

from __future__ import annotations

import time
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from dmbot.maps import recap_colour
from dmbot.memory import gate
from dmbot.memory import map_facts as mf
from tests.test_map_pins import PinsBase
from tests.test_map_store import GUILD_A

TOWN, GROUP = "t" * 32, "g" * 32
NAMES = {TOWN: "Bryn Shander", GROUP: "Ten-Towns"}


def fact(layer: str, value: str, how: str = "") -> mf.Fact:
    return mf.Fact(
        id=uuid.uuid4().hex, place_id=TOWN, layer=layer, value=value, words="", how=how,
        part="", status=mf.CONFIRMED, source="dm", table_knows=True,
    )  # fmt: skip


class TheLine(unittest.TestCase):
    def test_every_layer_in_plain_words(self) -> None:
        said = recap_colour.line(
            "Bryn Shander",
            [
                fact(mf.TERRAIN, "arctic"),
                fact(mf.CLIMATE, "arctic"),
                fact(mf.CONTROL, GROUP, "rules"),
                fact(mf.CULTURE, "dwarf", "strong"),
                fact(mf.POPULATION, "human", "mostly"),
            ],
            NAMES,
        )
        assert said is not None
        self.assertTrue(said.startswith("Bryn Shander: land ice and snow; climate freezing;"), said)
        self.assertIn("ruled by Ten-Towns", said)
        self.assertIn("culture ", said)
        self.assertIn("who lives there ", said)
        self.assertTrue(said.endswith("."), said)

    def test_a_ruler_the_caller_did_not_name_is_left_out(self) -> None:
        said = recap_colour.line("Bryn Shander", [fact(mf.CONTROL, GROUP, "rules")], {})
        self.assertIsNone(said)

    def test_nothing_to_say_is_no_line(self) -> None:
        self.assertIsNone(recap_colour.line("Bryn Shander", [], NAMES))


class TheView(unittest.IsolatedAsyncioTestCase):
    async def test_the_partys_view_only_and_named_places_only(self) -> None:
        reader = AsyncMock(return_value={TOWN: [fact(mf.TERRAIN, "arctic")]})
        with patch.object(recap_colour.map_fact_store, "facts_for", reader):
            got = await recap_colour.lines(object(), [TOWN, "x" * 32, TOWN], NAMES)
        self.assertEqual(got, ["Bryn Shander: land ice and snow."])
        _scope, asked, viewer = reader.await_args.args
        self.assertEqual(asked, [TOWN])  # (a place with no name is never asked about)
        self.assertEqual(viewer, gate.Viewer.the_party())  # (never the DM's view)
        self.assertFalse(viewer.dm)

    async def test_no_names_no_read(self) -> None:
        reader = AsyncMock()
        with patch.object(recap_colour.map_fact_store, "facts_for", reader):
            self.assertEqual(await recap_colour.lines(object(), [TOWN], {}), [])
        reader.assert_not_awaited()


class NeverTheDMs(PinsBase):  # (Postgres: an unkept read and a row the table doesn't know)
    async def row(self, *, status: str, table_knows: bool, source: str = "dm") -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_map_facts (guild_id, campaign_id, id, entity_id, layer, value,"
                " status, source, also_text, table_knows, created_at, map_id) VALUES (%s, %s, %s,"
                " %s, 'terrain', 'arctic', %s, %s, FALSE, %s, %s, %s)",
                (
                    GUILD_A, self.one.id, uuid.uuid4().hex, self.targos, status, source,
                    table_knows, int(time.time()), self.map_id if source == "map" else None,
                ),
            )  # fmt: skip

    async def said(self) -> list[str]:
        async with self.memory._read(GUILD_A, self.one.id) as scope:
            return await recap_colour.lines(scope, [self.targos], {self.targos: "Targos"})

    async def test_dmbots_unkept_read_never(self) -> None:
        await self.row(status=mf.READ, table_knows=True, source="map")  # (a map's read)
        self.assertEqual(await self.said(), [])

    async def test_a_row_the_table_doesnt_know_never(self) -> None:
        await self.row(status=mf.CONFIRMED, table_knows=False)
        self.assertEqual(await self.said(), [])
