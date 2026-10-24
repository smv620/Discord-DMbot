"""The Story graph's overview (#2632): every name the caller may see, which have no connection yet
and the groups of linked names. The owner's campaign had 51 names and the graph drew 2, because the
graph walks only the confirmed connections of its centre. Run as the website's own database role;
invented names throughout."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import relationships
from dmbot.web.recaps import RecapError
from tests.test_web_relationships import Web


class Overview(Web):
    async def overview(self, who: Any = None, **kw: Any) -> dict[str, Any]:
        return await relationships.read_overview(self.web, who or self.session, self.cref, **kw)

    async def lonely(self, count: int, kind: str = "place") -> list[str]:
        return [await self.add(f"Lone {kind} {n:02d}", kind) for n in range(count)]


class TheNumbers(Overview):
    async def test_fifty_one_names_and_one_connection_show_every_name(self) -> None:
        # The owner's case: nearly every name has no connection. The fixture has 5 names (Mira, the
        # Keep, Rook, the Far Hall and Dawnbreaker); with 46 lonely ones that is 51.
        await self.lonely(46)
        got = await self.overview()
        self.assertEqual(got["counts"]["names"], 51)
        graph = await self.graph(hops=1)
        self.assertLess(len(graph["nodes"]), 6)  # (what the graph alone draws: the old bug)
        self.assertEqual(len(got["tray"]) + got["counts"]["connectedNames"], 51)

    async def test_the_counts_the_tray_and_the_groups_add_up(self) -> None:
        loners = await self.lonely(49)
        got = await self.overview()
        # Mira, the Keep, Rook and the Far Hall are one group of four; the rest have no connection.
        self.assertEqual(got["counts"]["names"], 4 + 1 + 49)
        self.assertEqual(got["counts"]["connections"], 4)
        self.assertEqual(got["counts"]["connectedNames"], 4)
        self.assertEqual(got["counts"]["notConnected"], 50)
        self.assertEqual(
            {n["id"] for n in got["tray"]} & {self.mira, self.keep, self.rook, self.hall}, set()
        )
        self.assertTrue(set(loners) <= {n["id"] for n in got["tray"]})
        self.assertEqual([g["size"] for g in got["groups"]], [4])
        self.assertEqual(got["groups"][0]["center"], self.keep)  # the most connected name (three)
        self.assertFalse(got["truncated"])

    async def test_a_second_group_is_offered_with_its_own_centre(self) -> None:
        a = await self.add("Brask", "npc")
        b = await self.add("Tolle Mill", "place")
        await self.link("located_in", a, b, "brask-mill")
        got = await self.overview()
        self.assertEqual([g["size"] for g in got["groups"]], [4, 2])  # the larger first
        self.assertEqual(
            {g["center"] for g in got["groups"][1:]} & {a, b}, {got["groups"][1]["center"]}
        )
        self.assertEqual(got["counts"]["notConnected"], 1)  # only Dawnbreaker

    async def test_the_tray_is_ordered_by_kind_then_name(self) -> None:
        await self.add("Zed Hollow", "place")
        await self.add("Amber Gate", "place")
        await self.add("Quill", "npc")
        kinds = [(n["kind"], n["name"]) for n in (await self.overview())["tray"]]
        self.assertEqual(
            kinds,
            [
                ("place", "Amber Gate"),
                ("place", "Zed Hollow"),
                ("npc", "Quill"),
                ("item", "Dawnbreaker"),
            ],
        )

    async def test_it_agrees_with_the_graph_about_who_is_in_a_group(self) -> None:
        """The graph and the overview share one set of checks; a group is exactly what the graph
        draws from its centre, far enough out."""
        got = await self.overview()
        graph = await self.graph(center=got["groups"][0]["center"], hops=4)
        self.assertEqual(len(graph["nodes"]), got["groups"][0]["size"])
        self.assertEqual(len(graph["edges"]), 4)
        self.assertEqual(got["counts"]["connections"], sum(len(e["links"]) for e in graph["edges"]))


class WhoSeesWhat(Overview):
    async def test_a_name_hidden_from_players_is_in_a_dms_tray_flagged_and_not_a_players(
        self,
    ) -> None:
        secret_place = await self.add("Hidden Vault", "place")
        await self.sql(
            "UPDATE memory_entities SET hidden_from_players = true WHERE id = %s", secret_place
        )
        mine = {n["id"]: n for n in (await self.overview())["tray"]}
        self.assertFalse(mine[secret_place]["revealed"])
        theirs = await self.overview(self.player)
        self.assertNotIn(secret_place, {n["id"] for n in theirs["tray"]})
        self.assertEqual(theirs["counts"]["names"], (await self.overview())["counts"]["names"] - 1)

    async def test_a_secret_flag_reaches_only_a_dm(self) -> None:
        hidden = await self.add("Veil", "npc")
        await self.alias(hidden, "Veil", secret=True)  # a secret name that is the name itself
        dm = {n["id"]: n for n in (await self.overview())["tray"]}
        self.assertTrue(dm[hidden]["secret"])
        theirs = await self.overview(self.player)
        self.assertNotIn(hidden, {n["id"] for n in theirs["tray"]})
        for n in theirs["tray"]:
            self.assertNotIn("secret", n)

    async def test_a_secret_connection_does_not_make_a_name_connected_for_a_player(self) -> None:
        lone = await self.add("Pell", "npc")
        await self.link("knows", lone, self.keep, "pell-keep", secret=True)
        mine = await self.overview()
        self.assertNotIn(lone, {n["id"] for n in mine["tray"]})  # a DM sees the connection
        theirs = await self.overview(self.player)
        self.assertIn(lone, {n["id"] for n in theirs["tray"]})  # a player does not
        self.assertEqual(theirs["counts"]["connections"], 4)

    async def test_a_players_view_asked_by_a_dm_is_what_a_player_sees(self) -> None:
        secret_place = await self.add("Hidden Vault", "place")
        await self.sql(
            "UPDATE memory_entities SET hidden_from_players = true WHERE id = %s", secret_place
        )
        got = await self.overview(view="players")
        self.assertEqual(got["view"], "players")
        self.assertFalse(got["canEdit"])
        self.assertNotIn(secret_place, {n["id"] for n in got["tray"]})

    async def test_another_campaigns_names_never_appear(self) -> None:
        await self.memory.add_entity(
            self.g,
            self.two.id,
            type="place",
            name="Elsewhere",
            source=DM_SOURCE,
            status="confirmed",
        )
        names = {n["name"] for n in (await self.overview())["tray"]}
        self.assertNotIn("Elsewhere", names)


class Refusals(Overview):
    async def test_a_stranger_another_campaign_and_a_bad_view_are_refused(self) -> None:
        with self.assertRaises(RecapError) as bad:
            await self.overview(view="everything")
        self.assertEqual(bad.exception.code, "bad_view")
        with self.assertRaises(RecapError) as gone:
            await relationships.read_overview(self.web, self.session, f"{self.g}.{'0' * 32}")
        self.assertEqual(gone.exception.code, "not_found")

    async def test_an_empty_campaign_has_zeroes_and_no_groups(self) -> None:
        await self.sql("DELETE FROM memory_relations WHERE guild_id = %s", self.g)
        got = await self.overview()
        self.assertEqual(got["groups"], [])
        self.assertEqual(got["counts"]["connections"], 0)
        self.assertEqual(got["counts"]["notConnected"], got["counts"]["names"])


class TheCaps(Overview):
    async def test_a_name_cap_says_it_cut_and_keeps_the_true_totals(self) -> None:
        await self.lonely(10)
        got = await relationships.read_overview(self.web, self.session, self.cref, max_names=6)
        self.assertTrue(got["truncated"])
        self.assertEqual(got["counts"]["names"], 15)  # (the true number, not the six listed)
        self.assertLessEqual(len(got["tray"]) + got["counts"]["connectedNames"], 6)
        again = await relationships.read_overview(self.web, self.session, self.cref, max_names=6)
        self.assertEqual(got, again)  # the same names are kept on every load

    async def test_a_pair_cap_says_it_cut_and_keeps_the_same_pairs(self) -> None:
        first = await relationships.read_overview(self.web, self.session, self.cref, max_pairs=2)
        second = await relationships.read_overview(self.web, self.session, self.cref, max_pairs=2)
        self.assertTrue(first["truncated"])  # four connections, two kept
        self.assertEqual(first, second)
        whole = await self.overview()
        self.assertFalse(whole["truncated"])

    async def test_two_connections_between_a_pair_and_one_each_way_make_one_pair(self) -> None:
        await self.link("enemy_of", self.mira, self.keep, "second")
        await self.link("owns", self.keep, self.mira, "reverse")
        got = await self.overview()
        self.assertEqual(got["counts"]["connections"], 6)  # connections, counted one by one
        self.assertEqual([g["size"] for g in got["groups"]], [4])  # still one group of four

    async def test_a_campaign_with_no_names_is_empty_lists(self) -> None:
        await self.sql("DELETE FROM memory_relations WHERE guild_id = %s", self.g)
        await self.sql("DELETE FROM memory_entities WHERE guild_id = %s", self.g)
        got = await self.overview()
        self.assertEqual((got["tray"], got["groups"]), ([], []))
        self.assertEqual(got["counts"], {
            "names": 0, "connections": 0, "connectedNames": 0, "notConnected": 0,
        })  # fmt: skip


class WhatAPlayerNeverGets(Overview):
    async def test_the_overview_and_the_graph_agree_for_a_player(self) -> None:
        limited = await self.add("Locked Door", "place")
        await self.sql("UPDATE memory_entities SET audience_limited = true WHERE id = %s", limited)
        await self.link("knows", self.mira, limited, "mira-door")
        await self.link("owns", self.mira, self.hall, "mira-hall", secret=True)
        theirs = await self.overview(self.player)
        listed = {n["id"] for n in theirs["tray"]}
        self.assertNotIn(limited, listed)
        graph = await self.graph(self.player, center=self.mira, hops=4)
        self.assertNotIn(limited, {n["id"] for n in graph["nodes"]})
        group = theirs["groups"][0]
        self.assertEqual(group["size"], len(graph["nodes"]))
        self.assertEqual(
            theirs["counts"]["connections"], sum(len(e["links"]) for e in graph["edges"])
        )

    async def test_a_player_in_a_private_campaign_cannot_ask_for_the_full_view(self) -> None:
        await self.sql(
            "UPDATE campaigns SET dm_screen_visibility = 'private' WHERE id = %s", self.one.id
        )
        with self.assertRaises(RecapError) as caught:
            await self.overview(self.player, view="full")
        self.assertEqual(caught.exception.code, "peek_off")


class TheShapingAlone(unittest.TestCase):
    """`overview_of` needs no database: it only shapes what the door sent."""

    def shape(self, names: list[tuple[str, str, str]], pairs: list[list[str]]) -> dict[str, Any]:
        return relationships.overview_of(
            {
                "names": [{"id": i, "name": n, "kind": k, "revealed": True} for i, n, k in names],
                "pairs": pairs, "total": len(names), "connections": len(pairs),
                "canEdit": True, "view": "full", "visibility": "private", "canPeek": True,
            }
        )  # fmt: skip

    def test_nothing_at_all(self) -> None:
        got = self.shape([], [])
        self.assertEqual((got["tray"], got["groups"], got["truncated"]), ([], [], False))

    def test_a_pair_that_names_a_missing_id_is_left_out(self) -> None:
        got = self.shape([("a", "Ann", "npc"), ("b", "Bo", "npc")], [["a", "zz"], ["a", "b"]])
        self.assertEqual(got["counts"]["connectedNames"], 2)
        self.assertEqual([g["size"] for g in got["groups"]], [2])

    def test_a_tie_for_the_centre_goes_to_the_earlier_name_then_the_smaller_id(self) -> None:
        got = self.shape([("b", "bo", "npc"), ("a", "Ann", "npc")], [["a", "b"]])
        self.assertEqual(got["groups"][0]["center"], "a")
        got = self.shape([("y", "Same", "npc"), ("x", "Same", "npc")], [["x", "y"]])
        self.assertEqual(got["groups"][0]["center"], "x")

    def test_groups_come_largest_first_and_the_tray_by_kind_then_name(self) -> None:
        names = [
            ("a", "A", "npc"), ("b", "B", "npc"), ("c", "C", "npc"), ("d", "D", "npc"),
            ("e", "Zeta", "npc"), ("f", "Lake", "place"), ("g", "Cave", "place"),
            ("h", "Odd", "item"),
        ]  # fmt: skip
        got = self.shape(names, [["a", "b"], ["b", "c"], ["d", "d"]])
        self.assertEqual(
            [g["size"] for g in got["groups"]], [3]
        )  # (a pair of one name is no group)
        self.assertEqual(
            [n["name"] for n in got["tray"]], ["Cave", "Lake", "D", "Zeta", "Odd"]
        )  # places, then people, then the rest

    def test_a_secret_flag_that_is_not_there_is_not_sent(self) -> None:
        got = relationships.overview_of(
            {
                "names": [{"id": "a", "name": "A", "kind": "npc", "secret": None}],
                "pairs": [], "total": 1, "connections": 0,
                "canEdit": False, "view": "players", "visibility": "peek", "canPeek": True,
            }
        )  # fmt: skip
        self.assertNotIn("secret", got["tray"][0])
