"""Who knows a held-back connection, on the website (#2935, slice D1): the story graph tells a DM in
the full view which player characters know each connection, the DM alone gets the campaign's player
characters, and no player (a peeker included) learns anything of it. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory import link_edits
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import relationships
from dmbot.web.recaps import RecapError
from tests.test_web_relationships import TheRoute, Web


class WhoKnowsOnTheWeb(Web):
    bob_has_a_hero = False  # (these tests count the campaign's player characters)

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.tavin = await self.add("Tavin", "character", role="player_character")
        self.heir = await self.add("The Hidden Heir", "character", role="npc")
        self.secret_link = await self.link(
            "ally_of", self.mira, self.heir, "mira-heir", secret=True
        )
        self.plain_link = self.links["mira-keep"]

    async def knowers(self, link: str, *characters: str) -> None:
        await self.memory.set_connection_knowers(
            self.g, self.one.id, link, list(characters), source=DM_SOURCE
        )

    def link_of(self, got: dict[str, Any], tag: str) -> dict[str, Any]:
        wanted = self.links[tag]
        for edge in got["edges"]:
            for each in edge["links"]:
                if each["id"] == wanted:
                    return each  # type: ignore[no-any-return]
        raise AssertionError(f"{tag} is not in the answer")

    async def test_a_dm_sees_who_knows_each_connection(self) -> None:
        await self.knowers(self.secret_link, self.rook, self.tavin)
        got = await self.graph(hops=2)
        some = self.link_of(got, "mira-heir")
        self.assertEqual(some["who"], "some")
        self.assertEqual(
            some["knownBy"],
            [{"id": self.rook, "name": "Rook"}, {"id": self.tavin, "name": "Tavin"}],
        )
        self.assertEqual(
            (self.link_of(got, "mira-keep")["who"], self.link_of(got, "mira-keep")["knownBy"]),
            ("all", []),
        )

    async def test_a_held_back_connection_nobody_knows_is_only_the_dms(self) -> None:
        got = await self.graph(hops=2)
        self.assertEqual(
            (self.link_of(got, "mira-heir")["who"], self.link_of(got, "mira-heir")["knownBy"]),
            ("dm", []),
        )

    async def test_a_connection_the_party_has_not_learned_counts_as_held_back(self) -> None:
        made = await self.memory.add_relation(
            self.g, self.one.id, self.mira, "knows", self.hall, source=DM_SOURCE,
            confidence=1.0, status="confirmed", unrevealed=True,
        )  # fmt: skip
        self.links["mira-hall"] = made.value[0].id
        got = await self.graph(hops=2)
        self.assertEqual(self.link_of(got, "mira-hall")["who"], "dm")
        await self.knowers(self.links["mira-hall"], self.rook)
        self.assertEqual((await self.graph(hops=2))["edges"] and 1, 1)
        again = self.link_of(await self.graph(hops=2), "mira-hall")
        self.assertEqual((again["who"], [k["name"] for k in again["knownBy"]]), ("some", ["Rook"]))

    async def test_making_it_known_to_everyone_clears_the_knowers(self) -> None:
        await self.knowers(self.secret_link, self.rook)
        relation = await self.memory.relation(self.g, self.one.id, self.secret_link)
        assert relation is not None
        await link_edits.set_who_knows(self.memory, self.g, self.one.id, relation, "everyone")
        done = self.link_of(await self.graph(hops=2), "mira-heir")
        self.assertEqual((done["who"], done["knownBy"]), ("all", []))

    async def test_a_player_learns_nothing_of_it_not_even_that_there_is_something(self) -> None:
        await self.knowers(self.secret_link, self.rook)
        got = await self.graph(self.player, hops=2)
        every = [x for e in got["edges"] for x in e["links"]]
        self.assertNotIn(self.secret_link, [x["id"] for x in every])
        for each in every:
            self.assertNotIn("who", each)
            self.assertNotIn("knownBy", each)
        self.assertNotIn("The Hidden Heir", str(got))

    async def test_a_player_who_peeks_behind_the_dm_screen_gets_no_who_either(self) -> None:
        await self.knowers(self.secret_link, self.rook)
        peeked = await self.graph(self.player, hops=2, view="full")
        every = [x for e in peeked["edges"] for x in e["links"]]
        self.assertTrue(every)
        for each in every:
            self.assertNotIn("who", each)
            self.assertNotIn("knownBy", each)

    async def test_a_dm_looking_as_the_players_do_gets_no_who(self) -> None:
        await self.knowers(self.secret_link, self.rook)
        got = await self.graph(hops=2, view="players")
        for edge in got["edges"]:
            for each in edge["links"]:
                self.assertNotIn("knownBy", each)

    async def test_the_dm_gets_the_player_characters_and_only_those(self) -> None:
        got = await relationships.read_characters(self.web, self.session, self.cref)
        self.assertEqual(
            got["characters"],
            [{"id": self.rook, "name": "Rook"}, {"id": self.tavin, "name": "Tavin"}],
        )  # (Mira, an NPC, is not offered)

    async def test_nobody_else_gets_the_player_characters(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await relationships.read_characters(self.web, self.player, self.cref)
        self.assertEqual(caught.exception.code, "not_found")
        with self.assertRaises(RecapError) as elsewhere:
            await relationships.read_characters(self.web, self.session, f"{self.g}.nope")
        self.assertEqual(elsewhere.exception.code, "not_found")


class OverHttp(TheRoute):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.add("Tavin", "character", role="player_character")

    async def test_the_characters_are_over_http_for_a_dm_only(self) -> None:
        async with await self.client(self.session) as client:
            got = await client.get(f"/me/campaigns/{self.cref}/web/characters")
            self.assertEqual(got.status_code, 200)
            names = [c["name"] for c in got.json()["characters"]]
            self.assertEqual([n for n in names if n != "Bob's hero"], ["Rook", "Tavin"])
        async with await self.client(self.player) as client:
            no = await client.get(f"/me/campaigns/{self.cref}/web/characters")
            self.assertEqual((no.status_code, no.json()["error"]), (404, "not_found"))


class AfterTheCharacterChanges(WhoKnowsOnTheWeb):
    async def test_a_character_who_is_no_longer_a_player_character_is_still_listed(self) -> None:
        # (#2999: NPCs know things too, so "Who knows this?" names them with the player characters)
        await self.knowers(self.secret_link, self.rook, self.tavin)
        await self.sql("UPDATE memory_entities SET role = 'npc' WHERE id = %s", self.tavin)
        some = self.link_of(await self.graph(hops=2), "mira-heir")
        self.assertEqual([k["name"] for k in some["knownBy"]], ["Rook", "Tavin"])

    async def test_a_connection_only_an_npc_knows_is_held_by_some_not_the_dm(self) -> None:
        await self.knowers(self.secret_link, self.tavin)
        await self.sql("UPDATE memory_entities SET role = 'npc' WHERE id = %s", self.tavin)
        some = self.link_of(await self.graph(hops=2), "mira-heir")
        self.assertEqual((some["who"], [k["name"] for k in some["knownBy"]]), ("some", ["Tavin"]))
