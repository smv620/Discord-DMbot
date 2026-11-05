"""The click on a node (#3236): the route sends only `text`, `more`, `needsPeek` and `version`,
never an id or the rank, and a node the caller may not see is `not_found`. Run as the website's
own database role; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory import gate
from dmbot.memory.gate import CONNECTION, ENTRY
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import graph_view, node_summary_route, relationships
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB
from tests.test_web_api import QUILLON
from tests.test_web_relationships import Web


class NodeSummary(Web):
    async def read(self, node: str, who: Any = None, *, peek: bool = False) -> dict[str, Any]:
        return await node_summary_route.read_node_summary(
            self.web, who or self.session, self.cref, node, peek=peek
        )

    async def refusal_of(self, node: str, who: Any = None) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.read(node, who)
        return caught.exception.code

    async def test_the_answer_has_exactly_four_keys_for_a_dm_and_for_a_player(self) -> None:
        for who in (self.session, self.player):
            got = await self.read(self.mira, who)
            self.assertEqual(set(got), set(node_summary_route.KEYS))
            self.assertIsInstance(got["text"], str)
            self.assertIsInstance(got["more"], int)
            self.assertIsInstance(got["needsPeek"], bool)
        self.assertEqual((await self.read(self.mira))["version"], "dm")
        self.assertEqual((await self.read(self.mira, self.player))["version"], "players")

    async def test_no_id_of_the_story_is_in_the_answer(self) -> None:
        ids = {self.mira, self.rook, self.keep, self.hall}
        for who in (self.session, self.player):
            text = str(await self.read(self.mira, who))
            self.assertFalse([i for i in ids if i in text])

    async def test_the_dm_s_text_names_the_node(self) -> None:
        self.assertIn("Mira", (await self.read(self.mira))["text"])

    async def test_a_node_that_is_not_there_or_has_no_page_of_its_own_is_not_found(self) -> None:
        self.assertEqual(await self.refusal_of("f" * 32), "not_found")
        self.assertEqual(await self.refusal_of("alias:" + self.mira), "not_found")
        self.assertEqual(await self.refusal_of("not-an-id"), "not_found")

    async def test_an_outsider_is_not_found(self) -> None:
        outsider = await self.signed(BOB, QUILLON)
        self.assertEqual(await self.refusal_of(self.mira, outsider), "not_found")

    async def tell(self, kind: str, statement: str, who: list[str]) -> None:
        """Say exactly who knows one entry or connection (new things start out told to the player
        characters, so the tests set it themselves)."""
        await self.memory.set_statement_knowers(
            self.g, self.one.id, kind, statement, who, source=DM_SOURCE
        )

    async def from_nothing(self) -> None:
        """Start from nobody knowing anything (as the player-gate tests do): new things start out
        told to the player characters, so each test then says exactly who knows what."""
        await self.sql("DELETE FROM memory_knowers WHERE campaign_id = %s", self.one.id)

    async def test_a_node_no_player_character_knows_is_not_found_for_a_player_but_read_by_the_dm(
        self,
    ) -> None:
        assert self.hero is not None
        idol = await self.add("Hidden Idol", "place")
        await self.link("located_in", idol, self.keep, "idol-keep")
        await self.from_nothing()
        await self.tell(ENTRY, self.mira, [self.hero])  # (the player's own page is not empty)
        self.assertIn("Hidden Idol", (await self.read(idol))["text"])
        self.assertEqual(await self.refusal_of(idol, self.player), "not_found")
        # the same code as for an id that does not exist, so nothing hints at a hidden one
        self.assertEqual(await self.refusal_of("f" * 32, self.player), "not_found")

    async def test_a_player_behind_the_spoiler_gate_gets_no_text_until_they_peek(self) -> None:
        assert self.hero is not None
        wen = await self.add("Wen", "character", role="player_character", played_by=777)
        link = await self.link("ally_of", self.mira, wen, "mira-wen")
        await self.from_nothing()
        # Both players' characters know Mira; only Wen's knows that Mira is allied with Wen. That
        # is a spoiler for Bob's hero (the click is Bob's). Wen is a player character, so he ranks
        # high enough to be named (a summary names only neighbours of rank Minor or above; a rank-0
        # extra would be cut and nothing would be left to hide).
        await self.tell(ENTRY, self.mira, [self.hero, wen])
        await self.tell(ENTRY, wen, [wen])
        await self.tell(CONNECTION, link, [wen])
        # (the players' characters together must hold the tie, or nothing can be a spoiler)
        src, known, _pages = await relationships.source_of(self.web, self.session, self.cref)
        self.assertIn(wen, known["playerCharacters"])
        union = graph_view.build(src, known, gate.Viewer.player(known["playerCharacters"]))
        self.assertIn(wen, union.nodes)
        closed = await self.read(self.mira, self.player)
        self.assertEqual((closed["text"], closed["more"], closed["needsPeek"]), ("", 0, True))
        self.assertEqual(set(closed), set(node_summary_route.KEYS))
        opened = await self.read(self.mira, self.player, peek=True)
        self.assertIn("Wen", opened["text"])
        self.assertTrue(opened["needsPeek"])
        # the DM is never behind the gate
        self.assertFalse((await self.read(self.mira))["needsPeek"])
