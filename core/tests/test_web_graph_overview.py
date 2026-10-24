"""The Story graph's overview (#2632): every name the caller may see, which have no connection yet
and the groups of linked names. The owner's campaign had 51 names and the graph drew 2, because the
graph walks only the confirmed connections of its centre. Run as the website's own database role;
invented names throughout."""

from __future__ import annotations

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
