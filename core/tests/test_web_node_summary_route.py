"""The click on a node (#3236): the route sends only `text`, `more`, `needsPeek` and `version`,
never an id or the rank, and a node the caller may not see is `not_found`. Run as the website's
own database role; invented names."""

from __future__ import annotations

from typing import Any

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
