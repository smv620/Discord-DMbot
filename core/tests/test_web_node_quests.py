"""Each name's quests on the Story Graph, for "Cluster by quest" (#2992): the story threads a name
is joined to directly, sent by the door for a DM in Full View and for nobody else, whether or not
the quest is inside the steps asked for. Invented names throughout."""

from __future__ import annotations

from typing import Any

from tests.test_web_relationships import Web


class NodeQuests(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bell = await self.add("Find the Lost Bell", "quest")
        self.heist = await self.add("The Keep Heist", "mystery")
        # Mira is in both: twice in the bell, once in the heist. Rook is in the heist.
        await self.link("knows", self.mira, self.bell, "mira-bell")
        await self.link("ally_of", self.mira, self.bell, "mira-bell-2")
        await self.link("knows", self.mira, self.heist, "mira-heist")
        await self.link("knows", self.rook, self.heist, "rook-heist")

    async def quests_of(self, name: str, **kw: Any) -> Any:
        got = await self.graph(**kw)
        node = next(n for n in got["nodes"] if n["id"] == name)
        return node.get("quests", "absent")

    async def test_a_dm_gets_each_names_quests_the_most_connected_first(self) -> None:
        got = await self.quests_of(self.mira, center=self.mira, hops=1)
        self.assertEqual(
            got,
            [
                {"id": self.bell, "name": "Find the Lost Bell", "links": 2},
                {"id": self.heist, "name": "The Keep Heist", "links": 1},
            ],
        )
        rook = await self.quests_of(self.rook, center=self.mira, hops=1)
        self.assertEqual([q["id"] for q in rook], [self.heist])

    async def test_a_name_with_no_quest_has_an_empty_list(self) -> None:
        self.assertEqual(await self.quests_of(self.keep, center=self.mira, hops=1), [])

    async def test_a_quest_outside_the_steps_still_counts_for_the_names_inside(self) -> None:
        far = await self.add("The Far Quest", "goal")
        await self.link("knows", self.hall, far, "hall-far")
        got = await self.graph(center=self.keep, hops=1)
        ids = {n["id"] for n in got["nodes"]}
        self.assertNotIn(far, ids)  # the goal is two steps from the Keep, past the range asked for
        self.assertEqual(
            await self.quests_of(self.hall, center=self.keep, hops=1),
            [{"id": far, "name": "The Far Quest", "links": 1}],
        )

    async def test_no_one_but_a_dm_in_full_view_is_told_of_a_quest_or_a_tie_to_one(self) -> None:
        for label, ask in (
            ("a player", dict(who=self.player)),
            ("a player asking for the players' view", dict(who=self.player, view="players")),
            ("a DM in Players View", dict(who=self.session, view="players")),
        ):
            got = await self.graph(center=self.mira, hops=2, **ask)
            self.assertTrue(all("quests" not in n for n in got["nodes"]), label)
            self.assertNotIn(self.bell, {n["id"] for n in got["nodes"]}, label)

    async def test_a_quest_that_is_merged_away_or_not_confirmed_or_a_guess_is_not_one(self) -> None:
        ghost = await self.add("Old Quest", "quest")
        await self.link("knows", self.mira, ghost, "mira-ghost")
        await self.sql("UPDATE memory_entities SET status = 'rejected' WHERE id = %s", ghost)
        proposed = await self.add("Maybe Quest", "hook")
        made = await self.memory.add_relation(
            self.g, self.one.id, self.mira, "knows", proposed, source="dm", confidence=1.0,
            status="proposed",
        )  # fmt: skip
        self.assertTrue(made.value)
        got = await self.quests_of(self.mira, center=self.mira, hops=1)
        self.assertEqual({q["id"] for q in got}, {self.bell, self.heist})
