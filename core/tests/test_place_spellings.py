"""One place written two ways ("Northhaven", "North Haven"): the same letters once case, accents,
apostrophes, hyphens and spaces are ignored, places only (#3037). Invented names."""

from __future__ import annotations

import json
import unittest
from typing import Any

from dmbot.memory import gate, spellings
from dmbot.memory.background import Found
from dmbot.memory.recall import PLAYERS, RecallService
from dmbot.ui import name_background as nb
from dmbot.web import graph_view
from tests.test_hidden_labels import entity_row, source
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest
from tests.test_node_summary_run import PC


def found(name: str, kind: str = "place", also: tuple[str, ...] = ()) -> Found:
    return Found(name, kind, also, (), "")


class Pure(unittest.TestCase):
    def test_the_same_letters_are_one_place_whatever_the_spacing(self) -> None:
        for other in (
            "North Haven",
            "north-haven",
            "North Haven's".removesuffix("'s"),
            "NORTHHAVEN",
        ):
            self.assertEqual(spellings.place_key(other), spellings.place_key("Northhaven"), other)

    def test_a_different_letter_is_another_place(self) -> None:
        for other in ("North Heaven", "Northaven", "Haven North"):
            self.assertNotEqual(spellings.place_key(other), spellings.place_key("Northhaven"))

    def test_two_spellings_in_one_read_become_one_waiting_place(self) -> None:
        kept, folded = spellings.fold_in_read([found("Northhaven"), found("North Haven")])
        self.assertEqual(folded, 1)
        self.assertEqual([(f.name, f.also) for f in kept], [("Northhaven", ("North Haven",))])

    def test_a_person_with_a_split_name_is_left_alone(self) -> None:
        kept, folded = spellings.fold_in_read([found("Kazeth", "npc"), found("Ka Zeth", "npc")])
        self.assertEqual((len(kept), folded), (2, 0))

    def test_a_new_spelling_of_a_place_the_campaign_has_is_offered_not_made(self) -> None:
        joined, rest = spellings.near_places(
            [found("North Haven"), found("Dunmoor")], {"p1": ["Northhaven"]}
        )
        self.assertEqual(joined, {"p1": ("North Haven",)})
        self.assertEqual([f.name for f in rest], ["Dunmoor"])

    def test_a_near_miss_and_a_person_stay_new(self) -> None:
        joined, rest = spellings.near_places(
            [found("North Heaven"), found("North Haven", "npc")], {"p1": ["Northhaven"]}
        )
        self.assertEqual(joined, {})
        self.assertEqual(len(rest), 2)

    def test_two_places_that_fit_are_not_guessed_between(self) -> None:
        joined, rest = spellings.near_places(
            [found("North Haven")], {"p1": ["Northhaven"], "p2": ["North-Haven"]}
        )
        self.assertEqual((joined, len(rest)), ({}, 1))

    def test_a_spelling_the_dm_turned_down_is_a_new_place(self) -> None:
        joined, rest = spellings.near_places(
            [found("North Haven")], {"p1": ["Northhaven"]}, {("p1", "north haven")}
        )
        self.assertEqual((joined, len(rest)), ({}, 1))


class OnTheCampaign(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.haven = await self.add("Northhaven", type="place")
        self.camp = type("Camp", (), {"guild_id": GUILD_A, "id": self.c})()

    async def offer(self) -> None:
        joined, _names, rest = await nb._near_places(self.memory, self.camp, [found("North Haven")])
        self.assertEqual(rest, [])
        await nb.save_place_spellings(self.memory, self.camp, joined, None, "", "")

    async def test_the_new_spelling_waits_as_a_question_on_the_one_place(self) -> None:
        await self.offer()
        waiting = await self.memory.suggested_names(GUILD_A, self.c)
        self.assertEqual([(a.text, entry) for a, entry in waiting], [("North Haven", "Northhaven")])
        self.assertEqual(len(await self.memory.entities(GUILD_A, self.c)), 1 + 0)

    async def test_a_proposed_spelling_never_reaches_the_players_recall(self) -> None:
        await self.offer()
        got = await RecallService(self.memory, None).recall(
            GUILD_A, self.c, seeds=["Northhaven"], audience=PLAYERS, opted_in=()
        )
        self.assertNotIn("North Haven", " | ".join(i.text for i in got.items))

    async def test_no_is_remembered_and_the_spelling_is_a_new_place_after(self) -> None:
        await self.offer()
        (alias, _entry), *_ = await self.memory.suggested_names(GUILD_A, self.c)
        await self.memory.update_alias(GUILD_A, self.c, alias.id, source="dm", status="rejected")
        refused = await self.memory.refused_spellings(GUILD_A, self.c)
        self.assertIn((self.haven, "north haven"), refused)
        joined, _names, rest = await nb._near_places(self.memory, self.camp, [found("North Haven")])
        self.assertEqual((joined, len(rest)), ({}, 1))


class OnThePlayersGraph(unittest.TestCase):
    def test_a_proposed_spelling_is_not_on_the_players_graph(self) -> None:
        src, kn = source()
        kn["entities"].append(entity_row("p1", "Northhaven"))
        kn["aliasRows"].append(
            {"id": "al9", "entity_id": "p1", "text": "North Haven", "key": "north haven",
             "secret": False, "status": "proposed", "kind": "short"}
        )  # fmt: skip
        graph: Any = graph_view.build(src, kn, gate.Viewer.player([PC]))
        shown = json.dumps([graph.nodes, graph.edges], default=str)
        self.assertNotIn("North Haven", shown)
        self.assertIn("Mira", shown)  # (the graph itself is built and told)
