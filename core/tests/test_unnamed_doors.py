"""A person the story never named is the DM's alone (#2790), even when confirmed: the website's
doors that hide the DM's entries from players (the All names list, the relationship web and its
overview), the players' recall and the doors' source all keep them out. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.recall import DM_SCREEN, PLAYERS, RecallService
from dmbot.web import relationships
from tests.test_recap_web import BOB
from tests.test_web_api import THURSDAY
from tests.test_web_relationships import Web

DOORS = (
    "dmbot_entity_parts",
    "dmbot_recap_check_names",
    "dmbot_recap_shared",
    "dmbot_web_all_names",
    "dmbot_web_campaign_facts",
    "dmbot_web_graph_overview",
    "dmbot_web_relationships",
    "dmbot_web_story_timeline",
)


class TheDoors(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        made = await self.memory.add_unnamed(
            self.g, self.one.id, what="sister", anchor_id=self.mira, role="sibling"
        )
        self.sister = made.entity.id
        self.manager = await self.signed(BOB, THURSDAY)  # manages the server, DMs nothing here
        # confirmed on purpose: the DM may confirm one before naming it
        await self.sql("UPDATE memory_entities SET status = 'confirmed' WHERE id = %s", self.sister)
        await self.link("kin_of", self.mira, self.sister, "mira-sister")

    async def test_the_all_names_list_shows_the_dm_the_person_marked_and_a_player_nothing(
        self,
    ) -> None:
        dm = {n["id"]: n for n in (await self.page())["names"]}
        self.assertIn(self.sister, dm)
        self.assertTrue(dm[self.sister]["hidden"])
        player = await self.page(self.manager)
        self.assertNotIn(self.sister, {n["id"] for n in player["names"]})
        self.assertNotIn("sister", str(player))
        self.assertEqual(sum(k["count"] for k in player["kinds"]), player["total"])

    async def test_the_relationship_web_draws_them_for_the_dm_and_never_for_a_player(self) -> None:
        dm = await self.graph(center=self.mira, hops=1)
        self.assertIn(self.sister, {n["id"] for n in dm["nodes"]})
        self.assertIn(self.links["mira-sister"], {x["id"] for e in dm["edges"] for x in e["links"]})
        player = await self.graph(self.player, center=self.mira, hops=1)
        self.assertNotIn(self.sister, {n["id"] for n in player["nodes"]})
        self.assertNotIn(
            self.links["mira-sister"], {x["id"] for e in player["edges"] for x in e["links"]}
        )
        self.assertNotIn("sister", str(player))

    async def test_the_overview_and_a_peek_at_them_are_the_same(self) -> None:
        dm = await relationships.read_overview(self.web, self.session, self.cref)
        player = await relationships.read_overview(self.web, self.player, self.cref)
        # the DM's counts hold the person and the connection to them; the player's do not
        self.assertEqual(dm["counts"]["names"], player["counts"]["names"] + 1)
        self.assertEqual(dm["counts"]["connections"], player["counts"]["connections"] + 1)
        self.assertNotIn(self.sister, str(player))
        self.assertNotIn("sister", str(player))
        for center in (self.sister,):  # a player asking for them as the centre finds nothing
            self.assertEqual(
                await self.refused_web(self.player, center=center, hops=1), "not_found"
            )

    async def test_the_players_recall_leaves_them_out_and_the_dm_screen_does_not(self) -> None:
        service = RecallService(self.memory)
        players = await service.recall(
            self.g, self.one.id, seeds=["Mira Voss"], audience=PLAYERS, hops=2, opted_in=set()
        )
        self.assertNotIn(self.sister, {i.id for i in players.items})
        screen = await service.recall(
            self.g, self.one.id, seeds=["Mira Voss"], audience=DM_SCREEN, hops=2, opted_in=set()
        )
        self.assertIn(self.sister, {i.id for i in screen.items})
        roster = {e.id for e in await service.roster(self.g, self.one.id)}
        self.assertNotIn(self.sister, roster)

    async def test_every_function_that_hides_the_dms_entries_also_hides_them(self) -> None:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT proname, prosrc FROM pg_proc WHERE proname = ANY(%s)"
                " AND pronamespace = current_schema()::regnamespace",
                (list(DOORS),),
            )
            sources: dict[str, Any] = {r["proname"]: r["prosrc"] for r in await cur.fetchall()}
        self.assertEqual(sorted(sources), sorted(DOORS))
        for name, source in sources.items():
            with self.subTest(name):
                self.assertIn("unnamed", source)

    async def test_the_search_text_has_nothing_to_say_of_them_for_players(self) -> None:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT dmbot_entity_parts(%s, %s, %s) AS p", (self.g, self.one.id, self.mira)
            )
            parts = (await cur.fetchone() or {}).get("p")
        self.assertNotIn("sister", str(parts))  # (Mira's links leave out the unnamed end)
