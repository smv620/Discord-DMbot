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
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.store import MemoryStore
from dmbot.recaps import draft as rd
from dmbot.recaps import pipeline as pl
from dmbot.recaps.away import things_of
from dmbot.recaps.beats import Beat
from dmbot.recaps.discord import RecapFeatures
from tests.pg import DatabaseTest


def beat(n: int, where: str) -> Beat:
    return Beat(n, "Mira drew her blade.", ("Mira",), (n,), "event", where=where)


def worker(colour: Any = None) -> Any:
    w: Any = pl.RecapWorker.__new__(pl.RecapWorker)
    w._colour = colour
    w._reveals = None
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
        self.assertEqual(
            got, ("Mira: the party's rogue", pl.COLOUR_MARK + "Bryn Shander: land ice and snow.")
        )
        # only the places the beats visited, once each, none made up
        self.assertEqual(colour.await_args_list[-1].args[3], ["Bryn Shander", "Targos"])

    async def test_the_colour_keeps_its_room_when_the_sheet_is_full(self) -> None:
        w = worker(AsyncMock(return_value=["Bryn Shander: land ice.", "Targos: land hills."]))
        w._read_people_sheet = AsyncMock(return_value=tuple(f"Person {i}" for i in range(60)))
        got = await w._read_sheet(1, "c", JOB, INPUTS, [beat(1, "Bryn Shander")])
        self.assertEqual(len(got), rd.SHEET_LINES)
        self.assertTrue(all(x.startswith(pl.COLOUR_MARK) for x in got[-2:]))
        self.assertEqual(got[0], "Person 0")

    async def test_a_colour_line_is_background_and_yields_no_thing_to_the_checks(self) -> None:
        line = pl.COLOUR_MARK + "Bryn Shander: land ice and snow; ruled by Ten-Towns."
        self.assertIn("never said by anyone", line)
        given = rd.Given(who_and_what=(line,))
        self.assertEqual(things_of(given.who_and_what), ())

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


def entry(
    entity_id: str, text: str, *, secret: bool = False, hidden: bool = False, person: bool = False
) -> Any:
    return SimpleNamespace(
        entity_id=entity_id, text=text, secret=secret, entry_hidden=hidden, person=person
    )


def entity(entity_id: str, kind: str = "place", merged: str | None = None) -> Any:
    return SimpleNamespace(id=entity_id, type=kind, merged_into=merged)


class TheBotsReader(unittest.IsolatedAsyncioTestCase):
    def bot(
        self,
        entries: list[Any],
        entities: dict[str, Any],
        known: frozenset[str] | None = None,
        members: tuple[str, ...] = ("pc",),
    ) -> Any:
        # (what the party at the session knows, #4877; by default every entry but the ones
        # flagged "not known yet", and given apart where the two must disagree)
        if known is None:
            known = frozenset(e.entity_id for e in entries if not e.entry_hidden)

        def for_viewer(secrets: bool, seen: Any = None) -> Any:
            # (as `CampaignLookup.for_viewer`: the entries that party doesn't hold are gone)
            kept = [e for e in entries if seen is None or e.entity_id in seen]
            by_text: dict[str, list[Any]] = {}
            for e in kept:
                by_text.setdefault(e.text, []).append(e)
            return SimpleNamespace(
                entities={k: v for k, v in entities.items() if seen is None or k in seen},
                names=tuple(kept),
                exact=lambda heard: by_text.get(heard, ()),
            )

        lookup = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(for_viewer=for_viewer)))
        memory = SimpleNamespace(
            recap_colour=AsyncMock(return_value=["a line"]),
            party_at_session=AsyncMock(
                return_value=SimpleNamespace(members=members, entries=known)
            ),
        )
        return SimpleNamespace(lookup=lookup, memory=memory)

    async def go(self, bot: Any, places: list[str]) -> list[str]:
        return await RecapFeatures.recap_colour(bot, 1, "c", JOB, places)

    async def test_only_the_players_names_and_places_go_to_the_reader(self) -> None:
        bot = self.bot(
            [
                entry("p1", "Bryn Shander"),
                entry("p2", "Secret Keep", secret=True),
                entry("p3", "Unseen Hold", hidden=True),
                entry("f1", "Ten-Towns"),
                entry("f2", "The Veiled Court", secret=True),
                entry("f3", "Hidden Throne", hidden=True),
                entry("c1", "Cerric", person=True),
            ],
            {
                e.id: e
                for e in (
                    entity("p1"),
                    entity("p2"),
                    entity("p3"),
                    entity("f1", "faction"),
                    entity("f2", "faction"),
                    entity("f3", "faction"),
                    entity("c1", "character"),
                )
            },
        )
        got = await self.go(
            bot, ["Bryn Shander", "Secret Keep", "Unseen Hold", "Cerric", "Nowhere"]
        )
        self.assertEqual(got, ["a line"])
        args = bot.memory.recap_colour.await_args.args
        self.assertEqual(list(args[2]), ["p1"])  # the one place the players know by that name
        # a ruler is named only by a name the players may see: never a secret or unseen entry
        self.assertEqual(args[3], {"p1": "Bryn Shander", "f1": "Ten-Towns"})
        # (the party at that session, read once and passed on, #4877)
        bot.memory.party_at_session.assert_awaited_once_with(1, "c", 100)
        self.assertEqual(bot.memory.recap_colour.await_args.kwargs["members"], ("pc",))

    async def test_the_sessions_party_decides_not_todays_flag(self) -> None:
        # (unflagged but unknown to that party: left out; flagged but known to it: used)
        bot = self.bot(
            [entry("p1", "Bryn Shander"), entry("p2", "Targos", hidden=True)],
            {"p1": entity("p1"), "p2": entity("p2")},
            known=frozenset({"p2"}),
        )
        await self.go(bot, ["Bryn Shander", "Targos"])
        self.assertEqual(list(bot.memory.recap_colour.await_args.args[2]), ["p2"])

    async def test_no_party_at_the_session_passes_no_members(self) -> None:
        bot = self.bot([entry("p1", "Bryn Shander")], {"p1": entity("p1")}, members=())
        await self.go(bot, ["Bryn Shander"])
        self.assertEqual(bot.memory.recap_colour.await_args.kwargs["members"], ())

    async def test_a_place_matched_through_an_alias_is_named_the_way_the_beat_said_it(self) -> None:
        bot = self.bot(
            [entry("p1", "Bryn Shander"), entry("p1", "the Frozen Town")],
            {"p1": entity("p1")},
        )
        await self.go(bot, ["the Frozen Town"])
        self.assertEqual(bot.memory.recap_colour.await_args.args[3]["p1"], "the Frozen Town")

    async def test_a_merged_entity_and_a_thing_that_is_not_a_place_are_left_out(self) -> None:
        bot = self.bot(
            [entry("p1", "Old Town"), entry("i1", "Frostbrand")],
            {"p1": entity("p1", merged="p2"), "i1": entity("i1", "item")},
        )
        await self.go(bot, ["Old Town", "Frostbrand"])
        self.assertEqual(list(bot.memory.recap_colour.await_args.args[2]), [])

    async def test_nothing_without_places_or_story_memory(self) -> None:
        bot = self.bot([], {})
        self.assertEqual(await self.go(bot, []), [])
        bot.memory = None
        self.assertEqual(await self.go(bot, ["Bryn Shander"]), [])


class _StoreBase(DatabaseTest):
    """A campaign with a place the players know and one player character (so the party sees)."""

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


class TheStoreReader(_StoreBase):
    """`MemoryStore.recap_colour` through a real database: a row the DM kept and marked known to the
    table shows; an unkept read, or a kept row the table doesn't know, never does."""

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


class TheRealLookup(_StoreBase):
    """The whole path with the real lookup of the players' view: a ruler the DM hasn't shown the
    players is never named in a line, and the place's line says only what the table knows."""

    async def control(self, faction: str) -> None:
        async with self.db.guild(1) as conn:
            await conn.execute(
                "INSERT INTO memory_map_facts (guild_id, campaign_id, id, entity_id, layer,"
                " faction_id, how, status, source, also_text, table_knows, created_at)"
                " VALUES (%s, %s, %s, %s, 'control', %s, 'rules', 'confirmed', 'dm', false, true,"
                " %s)",
                (1, self.campaign, uuid.uuid4().hex, self.place, faction, 1000),
            )  # fmt: skip

    async def test_a_ruler_the_players_don_t_know_is_never_named(self) -> None:
        known = (
            await self.memory.add_entity(
                1, self.campaign, type="faction", name="Ten-Towns", source="dm",
                status="confirmed",
            )
        ).value.id  # fmt: skip
        async with self.db.guild(1) as conn:
            await conn.execute(
                "UPDATE memory_entities SET hidden_from_players = false WHERE id = %s", (known,)
            )
        unseen = (
            await self.memory.add_entity(
                1, self.campaign, type="faction", name="The Veiled Court", source="dm",
                status="confirmed", hidden_from_players=True,
            )
        ).value.id  # fmt: skip
        await self.fact("tundra", status="confirmed", known=True)
        await self.control(known)
        await self.control(unseen)
        lookup = CampaignLookup.build(await self.memory.lookup_data(1, self.campaign))
        bot: Any = SimpleNamespace(
            lookup=SimpleNamespace(get=AsyncMock(return_value=lookup)), memory=self.memory
        )
        # (a session after Cerric joined: the party at a session nobody was in is common knowledge
        # only, #4806)
        job: Any = SimpleNamespace(session_at=4_000_000_000_000)
        lines = await RecapFeatures.recap_colour(bot, 1, self.campaign, job, ["Bryn Shander"])
        self.assertEqual(len(lines), 1)
        self.assertIn("tundra", lines[0])
        self.assertNotIn("Veiled", lines[0])


if __name__ == "__main__":
    unittest.main()
