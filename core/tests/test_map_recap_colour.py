"""Recap colour (#4260, MapBot layers slice 10b; Supervisor's ruling on #4260): one plain line for
each place a session visited, from the players' view only. Invented names. The last class needs
Postgres."""

from __future__ import annotations

import time
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from dmbot.maps import recap_colour
from dmbot.memory import gate, layer_words, map_fact_store
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
        self.assertEqual(
            said,
            "Bryn Shander: land ice and snow; climate freezing; ruled by Ten-Towns; culture "
            f"{layer_words.culture_words(fact(mf.CULTURE, 'dwarf', 'strong'))}; who lives there "
            f"{layer_words.people_words(fact(mf.POPULATION, 'human', 'mostly'))}.",
        )

    def test_every_way_of_ruling_is_said(self) -> None:
        for how, ruled in layer_words.RULED.items():
            with self.subTest(how=how):
                said = recap_colour.line("Bryn Shander", [fact(mf.CONTROL, GROUP, how)], NAMES)
                self.assertEqual(said, f"Bryn Shander: {ruled.lower()} Ten-Towns.")

    def test_two_lands_are_listed(self) -> None:
        said = recap_colour.line(
            "Bryn Shander", [fact(mf.TERRAIN, "arctic"), fact(mf.TERRAIN, "hill")], NAMES
        )
        self.assertEqual(said, "Bryn Shander: land ice and snow, hills.")

    def test_a_ruler_the_caller_did_not_name_is_left_out(self) -> None:
        said = recap_colour.line("Bryn Shander", [fact(mf.CONTROL, GROUP, "rules")], {})
        self.assertIsNone(said)

    def test_nothing_to_say_is_no_line(self) -> None:
        self.assertIsNone(recap_colour.line("Bryn Shander", [], NAMES))


class TheView(unittest.IsolatedAsyncioTestCase):
    async def test_the_partys_view_only_and_named_places_only(self) -> None:
        reader = AsyncMock(return_value={TOWN: [fact(mf.TERRAIN, "arctic")]})
        with patch.object(map_fact_store, "facts_for", reader):
            got = await recap_colour.lines(object(), [TOWN, "x" * 32, TOWN], NAMES)  # type: ignore[arg-type]
        self.assertEqual(got, ["Bryn Shander: land ice and snow."])
        assert reader.await_args is not None
        _scope, asked, viewer = reader.await_args.args
        self.assertEqual(asked, [TOWN])  # (a place with no name is never asked about)
        self.assertEqual(viewer, gate.Viewer.the_party())  # (never the DM's view)
        self.assertFalse(viewer.dm)

    async def test_at_most_max_places(self) -> None:
        many = {uuid.uuid4().hex: f"Town {n}" for n in range(recap_colour.MAX_PLACES + 3)}
        reader = AsyncMock(return_value={})
        with patch.object(map_fact_store, "facts_for", reader):
            await recap_colour.lines(object(), list(many), many)  # type: ignore[arg-type]
        assert reader.await_args is not None
        self.assertEqual(reader.await_args.args[1], list(many)[: recap_colour.MAX_PLACES])

    async def test_no_names_no_read(self) -> None:
        reader = AsyncMock()
        with patch.object(map_fact_store, "facts_for", reader):
            got = await recap_colour.lines(object(), [TOWN], {})  # type: ignore[arg-type]
        self.assertEqual(got, [])
        reader.assert_not_awaited()


class NeverTheDMs(PinsBase):  # (Postgres: the party's view, through THE reader)
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # (with no player character the party sees nothing, and every "never" would pass vacuously)
        await self.memory.add_entity(
            GUILD_A, self.one.id, name="Cerric", type="character", role="player_character",
            played_by=4242, status="confirmed", source="dm",
        )  # fmt: skip

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

    async def said(self, names: dict[str, str] | None = None) -> list[str]:
        async with self.memory._read(GUILD_A, self.one.id) as scope:
            return await recap_colour.lines(scope, [self.targos], names or {self.targos: "Targos"})

    async def test_a_kept_row_the_table_knows_is_said(self) -> None:
        await self.row(status=mf.CONFIRMED, table_knows=True)
        self.assertEqual(await self.said(), ["Targos: land ice and snow."])

    async def test_dmbots_unkept_read_never(self) -> None:
        await self.row(status=mf.READ, table_knows=True, source="map")  # (a DM-only map's read)
        self.assertEqual(await self.said(), [])

    async def test_a_kept_dm_only_map_row_the_table_doesnt_know_never(self) -> None:
        await self.row(status=mf.CONFIRMED, table_knows=False, source="map")
        self.assertEqual(await self.said(), [])

    async def test_a_row_the_table_doesnt_know_never(self) -> None:
        await self.row(status=mf.CONFIRMED, table_knows=False)
        self.assertEqual(await self.said(), [])

    async def test_a_place_the_party_doesnt_know_never(self) -> None:
        await self.row(status=mf.CONFIRMED, table_knows=True)
        await self.memory.set_hidden_from_players(
            GUILD_A, self.one.id, self.targos, True, source="dm", dm_chose_secret=True
        )
        self.assertEqual(await self.said(), [])

    async def test_a_ruler_the_party_doesnt_know_is_left_out(self) -> None:
        made = await self.memory.add_entity(
            GUILD_A, self.one.id, name="The Ten", type="faction", status="confirmed", source="dm"
        )
        group = made.value.id
        await self.memory.set_map_fact(
            GUILD_A, self.one.id, self.targos, mf.CONTROL, group, how="rules", source="dm"
        )
        await self.memory.show_map_facts(GUILD_A, self.one.id, self.targos, True, source="dm")
        names = {self.targos: "Targos", group: "The Ten"}
        self.assertEqual(await self.said(names), ["Targos: ruled by The Ten."])
        await self.memory.set_hidden_from_players(
            GUILD_A, self.one.id, group, True, source="dm", dm_chose_secret=True
        )
        self.assertEqual(await self.said(names), [])
