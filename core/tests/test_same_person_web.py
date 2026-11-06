"""After a "yes, the same person" (#3037) the website shows a player nothing it did not show before:
the Story Graph, its overview and the All names list, read through the website's own doors as a
player. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.web import relationships
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB
from tests.test_web_api import THURSDAY
from tests.test_web_relationships import Web


class TheWebAfterAJoin(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        made = await self.memory.add_unnamed(
            self.g, self.one.id, what="guard at the north gate", role="guard"
        )
        self.guard = made.entity.id
        self.member = await self.signed(BOB, THURSDAY)  # a server member who DMs nothing here
        self.ken = await self.add("Ken", "character", role="npc")
        # the guard is connected to places the players are shown, and has another name
        await self.link("knows", self.guard, self.keep, "guard-keep")
        await self.link("located_in", self.guard, self.hall, "guard-hall")
        await self.memory.add_alias(
            self.g, self.one.id, self.guard, "the gatekeeper", kind="title", source="dm",
            status="confirmed",
        )  # fmt: skip

    async def drawn(self, centre: str) -> Any:
        """The player's graph around a name, or the refusal when they may not see it."""
        try:
            return await self.graph(self.player, center=centre, hops=2)
        except RecapError as refused:
            return refused.code

    async def player_pages(self) -> dict[str, Any]:
        return {
            "graph": [await self.drawn(centre) for centre in (self.ken, self.keep, self.hall)],
            "overview": await relationships.read_overview(self.web, self.player, self.cref),
            "all_names": await self.page(self.member),
        }

    async def test_a_yes_changes_nothing_a_player_is_shown(self) -> None:
        before = await self.player_pages()
        await self.memory.join_same_person(self.g, self.one.id, self.guard, self.ken)
        after = await self.player_pages()
        self.assertEqual(after, before)
        for words in ("north gate", "gatekeeper", self.links["guard-keep"]):
            self.assertNotIn(words, str(after))

    async def test_the_dm_sees_the_moved_connections_on_ken(self) -> None:
        await self.memory.join_same_person(self.g, self.one.id, self.guard, self.ken)
        dm = await self.graph(center=self.ken, hops=1)
        self.assertIn(self.keep, {n["id"] for n in dm["nodes"]})  # (secret, flagged, DM only)
