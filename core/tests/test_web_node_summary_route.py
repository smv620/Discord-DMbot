"""The click on a node (#3236): the route sends only `text`, `more`, `needsPeek` and `version`,
never an id or the rank, and a node the caller may not see is `not_found`. Run as the website's
own database role; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.gate import CONNECTION, ENTRY
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import node_summary_route
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

    async def test_a_node_no_player_character_knows_is_not_found_for_a_player_but_read_by_the_dm(
        self,
    ) -> None:
        idol = await self.add("Hidden Idol", "place")
        link = await self.link("located_in", idol, self.keep, "idol-keep")
        await self.tell(ENTRY, idol, [])
        await self.tell(CONNECTION, link, [])
        self.assertIn("Hidden Idol", (await self.read(idol))["text"])
        self.assertEqual(await self.refusal_of(idol, self.player), "not_found")
        # the same code as for an id that does not exist, so nothing hints at a hidden one
        self.assertEqual(await self.refusal_of("f" * 32, self.player), "not_found")

    async def test_a_player_behind_the_spoiler_gate_gets_no_text_until_they_peek(self) -> None:
        wen = await self.add("Wen", "character", role="player_character", played_by=777)
        tam = await self.add("Old Tam", "npc")
        link = await self.link("knows", self.mira, tam, "mira-tam")
        # Wen's character knows Mira knows Old Tam; Bob's hero does not: a spoiler for him
        await self.tell(ENTRY, tam, [wen])
        await self.tell(CONNECTION, link, [wen])
        closed = await self.read(self.mira, self.player)
        self.assertEqual((closed["text"], closed["more"], closed["needsPeek"]), ("", 0, True))
        self.assertEqual(set(closed), set(node_summary_route.KEYS))
        opened = await self.read(self.mira, self.player, peek=True)
        self.assertIn("Mira", opened["text"])
        self.assertTrue(opened["needsPeek"])
