"""A person the story never named is the DM's alone (#2790), even when confirmed: the website's
doors that hide the DM's entries from players (the All names list, the relationship web and its
overview), the players' recall and the doors' source all keep them out. Invented names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory.recall import DM_SCREEN, PLAYERS, RecallService
from dmbot.web import relationships, see_as
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


class WithASister(Web):
    """Mira's sister, a person the story never named, confirmed, with the line to her."""

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


class TheDoors(WithASister):
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


class TheStoryGraph(WithASister):
    """The graph door (#3732): an unnamed person, and the lines to them, only in a DM's Full View;
    never a player's peek, Players View or See as…; the label built from the anchor's present name;
    waiting ones off the map, counted for the DM."""

    def drawn(self, page: dict[str, Any]) -> tuple[set[str], set[str]]:
        nodes = {str(n["id"]) for n in page["nodes"]}
        lines = {str(x["id"]) for e in page["edges"] for x in e["links"]}
        return nodes, lines

    def none_of_them(self, page: dict[str, Any]) -> None:
        nodes, lines = self.drawn(page)
        self.assertNotIn(self.sister, nodes)
        self.assertNotIn(self.links["mira-sister"], lines)
        self.assertNotIn("sister", str(page))
        self.assertNotIn("waitingUnnamed", page)

    async def test_the_dm_s_full_view_draws_them_with_a_label_built_now(self) -> None:
        page = await self.graph(center=self.mira, hops=1)
        nodes, lines = self.drawn(page)
        self.assertIn(self.sister, nodes)
        self.assertIn(self.links["mira-sister"], lines)
        sister = next(n for n in page["nodes"] if n["id"] == self.sister)
        self.assertTrue(sister["unnamed"])
        self.assertEqual(sister["name"], "unnamed - Mira Voss' sister")
        self.assertEqual(sister["unnamedAnchor"], "Mira Voss")
        self.assertEqual(sister["unnamedWhat"], "sister")  # (as the All names page has it)
        mira = next(n for n in page["nodes"] if n["id"] == self.mira)
        self.assertFalse(mira["unnamed"])
        # the stored copy gone stale (a rename relabels it, but a cut or an older anchor's rename
        # can leave it behind): the label is still built from the parts and the anchor's name
        await self.sql("UPDATE memory_entities SET name = 'stale' WHERE id = %s", self.sister)
        again = await self.graph(center=self.mira, hops=1)
        sister = next(n for n in again["nodes"] if n["id"] == self.sister)
        self.assertEqual(sister["name"], "unnamed - Mira Voss' sister")

    async def test_a_player_s_peek_gets_none_of_them(self) -> None:
        page = await self.graph(self.player, view="full", center=self.mira, hops=1)
        self.assertEqual(page["view"], "full")  # (a peek: the DM screen allows it)
        self.none_of_them(page)
        self.assertIn(self.mira, self.drawn(page)[0])  # (the rest of the peek is still there)
        self.assertEqual(
            await self.refused_web(self.player, view="full", center=self.sister), ("not_found")
        )

    async def test_a_player_s_peek_at_the_overview_gets_none_of_them(self) -> None:
        dm = await relationships.read_overview(self.web, self.session, self.cref)
        peek = await relationships.read_overview(self.web, self.player, self.cref, view="full")
        self.assertNotIn("sister", str(peek))
        self.assertEqual(dm["counts"]["names"], peek["counts"]["names"] + 1)

    async def test_a_player_s_own_view_and_the_dm_s_players_view_get_none(self) -> None:
        self.none_of_them(await self.graph(self.player, center=self.mira, hops=1))
        self.none_of_them(await self.graph(view="players", center=self.mira, hops=1))

    async def test_see_as_the_party_or_a_character_gets_none(self) -> None:
        assert self.hero is not None
        for viewer in (see_as.PARTY, self.hero, self.rook):
            with self.subTest(viewer):
                page = await see_as.read_see_as(
                    self.web, self.session, self.cref, viewer, center=self.mira, hops=1
                )
                self.none_of_them(page)

    async def test_a_waiting_one_is_off_the_map_and_counted_for_the_dm(self) -> None:
        made = await self.memory.add_unnamed(
            self.g, self.one.id, what="cousin", anchor_id=self.mira, role="cousin"
        )
        cousin = made.entity.id  # (waiting, as the reader makes them)
        dm = await self.graph(center=self.mira, hops=1)
        self.assertNotIn(cousin, self.drawn(dm)[0])
        self.assertEqual(dm["waitingUnnamed"], 1)
        self.assertNotIn("waitingUnnamed", await self.graph(self.player, center=self.mira, hops=1))
        peek = await self.graph(self.player, view="full", center=self.mira, hops=1)
        self.assertNotIn("waitingUnnamed", peek)
        self.assertNotIn("cousin", str(peek))

    async def test_a_waiting_one_is_counted_on_an_empty_map_too(self) -> None:
        await self.sql(
            "UPDATE memory_entities SET status = 'proposed' WHERE campaign_id = %s", self.one.id
        )  # (nothing confirmed: the map is empty)
        dm = await self.graph()
        self.assertEqual((dm["nodes"], dm["waitingUnnamed"]), ([], 1))
        peek = await self.graph(self.player, view="full")
        self.assertNotIn("waitingUnnamed", peek)

    async def test_a_character_the_dm_told_is_shown_the_label_by_the_gate(self) -> None:
        """The gate's own rule (`gate.name_entries`): a player whose character the DM told about the
        person sees the label built from what they may read, never a made-up name; the party (what
        every player character knows) does not. Pinned as it is today: #3732 asks Supervisor
        whether that rule stays."""
        assert self.hero is not None
        for kind, statement in (("entry", self.sister), ("connection", self.links["mira-sister"])):
            await self.memory.set_statement_knowers(
                self.g, self.one.id, kind, statement, [self.hero], source="dm"
            )
        own = await self.graph(self.player, center=self.mira, hops=1)
        sister = next(n for n in own["nodes"] if n["id"] == self.sister)
        self.assertEqual(sister["name"], "unnamed - Mira Voss' sister")
        self.assertNotIn("unnamed", sister)  # (the door's fields are the DM's)
        party = await see_as.read_see_as(
            self.web, self.session, self.cref, see_as.PARTY, center=self.mira, hops=1
        )
        self.none_of_them(party)


class ThePageShape(unittest.TestCase):
    """`relationships.page_of` alone (no database): the label and the waiting count (#3732)."""

    def got(self, nodes: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        return {
            "center": None, "nodes": nodes, "edges": [], "truncated": False, "canEdit": True,
            "seesAll": True, "view": "full", "visibility": "peek", "canPeek": True,
            "pageIds": set(), **extra,
        }  # fmt: skip

    def test_an_unnamed_node_is_labelled_from_its_anchor_and_a_plain_one_is_not(self) -> None:
        sister = {
            "id": "s", "name": "stale", "kind": "npc", "unnamed": True, "unnamedWhat": "sister",
            "unnamedAnchor": {"name": "Mira Voss", "what": None, "anchorName": None},
        }  # fmt: skip
        mira = {"id": "m", "name": "Mira Voss", "kind": "npc", "unnamed": False,
                "unnamedWhat": None, "unnamedAnchor": None}  # fmt: skip
        gated = {"id": "g", "name": "Rook", "kind": "player_character"}
        page = relationships.page_of(self.got([sister, mira, gated]))
        shown = {n["id"]: n for n in page["nodes"]}
        self.assertEqual(shown["s"]["name"], "unnamed - Mira Voss' sister")
        self.assertEqual(shown["s"]["unnamedAnchor"], "Mira Voss")
        self.assertEqual((shown["m"]["unnamed"], "unnamedAnchor" in shown["m"]), (False, False))
        self.assertNotIn("unnamed", shown["g"])  # (a node the gate worked out is left alone)

    def test_the_waiting_count_only_when_the_door_sent_one(self) -> None:
        self.assertEqual(relationships.page_of(self.got([], waitingUnnamed=2))["waitingUnnamed"], 2)
        for got in (self.got([]), self.got([], waitingUnnamed=None)):  # (a peek's is null)
            self.assertNotIn("waitingUnnamed", relationships.page_of(got))
