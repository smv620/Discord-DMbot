"""The recap writer's background about visited places (#4721, `DMBOT_RECAP_COLOUR`, MapBot slice
10b): off, the writer's sheet is exactly what it was; on, one line for each place the beats visited,
as the players know it, and nothing for a place the beats didn't name. Stand-ins, no database."""

from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.campaigns import CampaignStore
from dmbot.memory.store import MemoryStore
from dmbot.recaps import pipeline as pl
from dmbot.recaps.beats import Beat
from dmbot.recaps.discord import RecapFeatures
from tests.pg import DatabaseTest


def beat(n: int, where: str) -> Beat:
    return Beat(n, "Mira drew her blade.", ("Mira",), (n,), "event", where=where)


def worker(colour: Any = None) -> Any:
    w: Any = pl.RecapWorker.__new__(pl.RecapWorker)
    w._colour = colour
    w._read_people_sheet = AsyncMock(return_value=("Mira: the party's rogue",))
    return w


JOB: Any = SimpleNamespace(session_at=100)
INPUTS: Any = SimpleNamespace()


class TheSheet(unittest.IsolatedAsyncioTestCase):
    async def test_off_the_sheet_is_exactly_the_people_sheet(self) -> None:
        w = worker(None)
        got = await w._read_sheet(1, "c", JOB, INPUTS, [beat(1, "Bryn Shander")])
        self.assertEqual(got, ("Mira: the party's rogue",))

    async def test_on_it_adds_one_line_per_visited_place_after_the_people(self) -> None:
        colour = AsyncMock(return_value=["Bryn Shander: land ice and snow."])
        w = worker(colour)
        shown = [beat(1, "Bryn Shander"), beat(2, "Targos"), beat(3, "Bryn Shander"), beat(4, "")]
        got = await w._read_sheet(1, "c", JOB, INPUTS, shown)
        self.assertEqual(got, ("Mira: the party's rogue", "Bryn Shander: land ice and snow."))
        # only the places the beats visited, once each, none made up
        self.assertEqual(colour.await_args.args[3], ["Bryn Shander", "Targos"])

    async def test_no_visited_place_asks_for_nothing(self) -> None:
        colour = AsyncMock(return_value=["never"])
        got = await worker(colour)._read_sheet(1, "c", JOB, INPUTS, [beat(1, "")])
        self.assertEqual(got, ("Mira: the party's rogue",))
        colour.assert_not_awaited()

    async def test_a_failure_costs_the_colour_and_never_the_recap(self) -> None:
        colour = AsyncMock(side_effect=RuntimeError("Secret words about Bryn Shander"))
        with self.assertLogs("dmbot.recaps.pipeline", level="WARNING") as logged:
            got = await worker(colour)._read_sheet(1, "c", JOB, INPUTS, [beat(1, "Targos")])
        self.assertEqual(got, ("Mira: the party's rogue",))
        self.assertIn("RuntimeError", logged.output[0])
        self.assertNotIn("Secret words", logged.output[0])  # (the type, never the text)


def entry(entity_id: str, *, secret: bool = False, hidden: bool = False) -> Any:
    return SimpleNamespace(entity_id=entity_id, secret=secret, entry_hidden=hidden)


class TheBotsReader(unittest.IsolatedAsyncioTestCase):
    def bot(self, found: dict[str, list[Any]], entities: dict[str, str]) -> Any:
        players = SimpleNamespace(
            entities={
                k: SimpleNamespace(id=k, name=v, merged_into=None) for k, v in entities.items()
            },
            exact=lambda heard: found.get(heard, ()),
        )
        lookup = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(for_viewer=lambda secrets: players))
        )
        memory = SimpleNamespace(recap_colour=AsyncMock(return_value=["a line"]))
        return SimpleNamespace(lookup=lookup, memory=memory)

    async def go(self, bot: Any, places: list[str]) -> list[str]:
        return await RecapFeatures.recap_colour(bot, 1, "c", JOB, places)

    async def test_only_the_players_names_and_places_go_to_the_reader(self) -> None:
        bot = self.bot(
            {
                "Bryn Shander": [entry("p1")],
                "Secret Keep": [entry("p2", secret=True)],
                "Unseen Hold": [entry("p3", hidden=True)],
                "Gone": [entry("p9")],  # (not among the players' entries)
            },
            {"p1": "Bryn Shander", "p2": "Secret Keep", "p3": "Unseen Hold", "f1": "Ten-Towns"},
        )
        got = await self.go(bot, ["Bryn Shander", "Secret Keep", "Unseen Hold", "Gone", "Nowhere"])
        self.assertEqual(got, ["a line"])
        args = bot.memory.recap_colour.await_args.args
        self.assertEqual(list(args[2]), ["p1"])  # the one place the players know by that name
        self.assertEqual(args[3]["f1"], "Ten-Towns")  # (a ruler is named the way they know it)
        self.assertNotIn("p9", args[3])

    async def test_nothing_without_places_or_story_memory(self) -> None:
        bot = self.bot({}, {})
        self.assertEqual(await self.go(bot, []), [])
        bot.memory = None
        self.assertEqual(await self.go(bot, ["Bryn Shander"]), [])


if __name__ == "__main__":
    unittest.main()


class TheStoreReader(DatabaseTest):
    """`MemoryStore.recap_colour` through a real database: a row the DM kept and marked known to the
    table shows; an unkept read, or a kept row the table doesn't know, never does."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaign = (await CampaignStore(self.db).create(1, "One", 7)).id
        self.memory = MemoryStore(self.db)
        made = await self.memory.add_entity(
            1, self.campaign, type="place", name="Bryn Shander", source="dm", status="confirmed"
        )
        self.place = made.value.id
        # (with no player character the party sees nothing, and every "never" would pass vacuously)
        await self.memory.add_entity(
            1, self.campaign, name="Cerric", type="character", role="player_character",
            played_by=4242, status="confirmed", source="dm",
        )  # fmt: skip
        async with self.db.guild(1) as conn:  # (a name a DM adds starts unseen by the players)
            await conn.execute(
                "UPDATE memory_entities SET hidden_from_players = false WHERE id = %s",
                (self.place,),
            )

    async def fact(self, value: str, *, status: str, known: bool) -> None:
        async with self.db.guild(1) as conn:
            await conn.execute(
                "INSERT INTO memory_map_facts (guild_id, campaign_id, id, entity_id, layer, value,"
                " status, source, also_text, table_knows, created_at)"
                " VALUES (%s, %s, %s, %s, 'terrain', %s, %s, 'dm', false, %s, %s)",
                (1, self.campaign, uuid.uuid4().hex, self.place, value, status, known, 1000),
            )  # fmt: skip

    async def test_only_what_the_dm_kept_and_the_table_knows_is_said(self) -> None:
        await self.fact("tundra", status="confirmed", known=True)
        await self.fact(
            "forest", status="confirmed", known=False
        )  # (the DM's own: not the table's)
        await self.fact("swamp", status="read", known=True)  # (DMbot's read, never kept)
        lines = await self.memory.recap_colour(
            1, self.campaign, [self.place], {self.place: "Bryn Shander"}
        )
        self.assertEqual(len(lines), 1)
        self.assertIn("Bryn Shander", lines[0])
        self.assertIn("tundra", lines[0])
        for word in ("forest", "swamp"):
            self.assertNotIn(word, lines[0])

    async def test_a_place_with_nothing_the_table_knows_says_nothing(self) -> None:
        await self.fact("forest", status="confirmed", known=False)
        self.assertEqual(
            await self.memory.recap_colour(1, self.campaign, [self.place], {self.place: "Bryn"}), []
        )

    async def test_a_place_with_no_name_in_the_players_view_is_left_out(self) -> None:
        await self.fact("tundra", status="confirmed", known=True)
        self.assertEqual(await self.memory.recap_colour(1, self.campaign, [self.place], {}), [])
