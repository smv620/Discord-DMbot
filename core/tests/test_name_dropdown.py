"""The name dropdown on Connect to… and Same as… (#2321): the order (closest first or A to Z),
its pages, who sees which connections, and that a pick goes the same way a typed search does."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

import discord

from dmbot.memory import closeness
from dmbot.memory.models import CONFIRMED
from dmbot.ui import name_dropdown as dd


def entity(eid: str, name: str, status: str = CONFIRMED, kind: str = "npc") -> Any:
    return SimpleNamespace(id=eid, name=name, status=status, type=kind, kind_key=kind)


def lookup(people: list[Any], public: dict[str, set[str]], private: dict[str, set[str]]) -> Any:
    return SimpleNamespace(
        entities={e.id: e for e in people},
        neighbours={k: frozenset(v) for k, v in private.items()},
        confirmed_neighbours={k: frozenset(v) for k, v in public.items()},
    )


class Distances(unittest.TestCase):
    def test_nearest_first_and_a_path_is_the_shortest_one(self) -> None:
        web = {"a": {"b", "c"}, "b": {"a", "d"}, "c": {"a", "d"}, "d": {"b", "c", "e"}, "e": {"d"}}
        self.assertEqual(closeness.distances(web, "a"), {"a": 0, "b": 1, "c": 1, "d": 2, "e": 3})

    def test_the_walk_stops_at_the_limit(self) -> None:
        chain = {str(i): {str(i + 1)} for i in range(10)}
        self.assertNotIn("9", closeness.distances(chain, "0", limit=3))

    def test_the_unconnected_come_last_in_alphabetical_order(self) -> None:
        entries = [("z", "Zed"), ("m", "mira"), ("n", "Near"), ("a", "Ann")]
        order = closeness.by_closeness(entries, {"n": 1, "z": 2})
        self.assertEqual([name for _, name in order], ["Near", "Zed", "Ann", "mira"])

    def test_alphabetical_ignores_case(self) -> None:
        order = closeness.alphabetical([("1", "bell"), ("2", "Aria"), ("3", "Cerric")])
        self.assertEqual([name for _, name in order], ["Aria", "bell", "Cerric"])


class Ordering(unittest.TestCase):
    def setUp(self) -> None:
        self.people = [
            entity("me", "Jack"),
            entity("g", "Thieves Guild", kind="faction"),
            entity("s", "The Shadow"),
            entity("a", "Ann"),
            entity("p", "Sam", kind="person"),  # a person at the table: not a story name
            entity("x", "Old Entry", status="proposed"),
        ]
        self.public = {"me": {"a"}, "a": {"me"}}
        self.private = {**self.public, "me": {"a", "g"}, "g": {"me"}}
        self.names = lookup(self.people, self.public, self.private)

    def test_other_confirmed_story_names_only(self) -> None:
        ids = {i for i, _ in dd.candidates(self.names, "me")}
        self.assertEqual(ids, {"g", "s", "a"})

    def test_a_dm_sees_secret_connections_in_the_order_and_a_player_does_not(self) -> None:
        dm = dd.ordered(self.names, "me", dd.CLOSEST, secrets=True)
        player = dd.ordered(self.names, "me", dd.CLOSEST, secrets=False)
        self.assertEqual([i for i, _ in dm][:2], ["a", "g"])  # both one step away
        self.assertEqual([i for i, _ in player], ["a", "s", "g"])  # the guild tie is secret

    def test_a_to_z(self) -> None:
        order = dd.ordered(self.names, "me", dd.AZ, secrets=True)
        self.assertEqual([n for _, n in order], ["Ann", "The Shadow", "Thieves Guild"])


class TheView(unittest.TestCase):
    def view(self, count: int, *, mode: str = dd.CLOSEST, page: int = 0) -> dd.NameDropdown:
        people = [entity(f"e{i}", f"Name {i:03}") for i in range(count)]
        names = lookup(people, {}, {})
        shown = [(e.id, e.name) for e in people][:25]
        last = max((count - 1) // dd.PAGE, 0)
        return dd.NameDropdown(
            "c1", "me", "member_of", "Jack", names, shown,
            secret=False, mode=mode, page=page, last=last,
        )  # fmt: skip

    @staticmethod
    def labels(view: dd.NameDropdown) -> list[str]:
        return [str(c.label) for c in view.children if isinstance(c, discord.ui.Button)]

    def test_a_page_holds_at_most_a_menus_worth(self) -> None:
        self.assertEqual(len(self.view(60).pick.options), 25)

    def test_paging_buttons_show_only_where_they_go(self) -> None:
        first, middle = self.view(60), self.view(60, page=1)
        self.assertEqual([x for x in self.labels(first) if x in "◀▶"], ["▶"])
        self.assertEqual([x for x in self.labels(middle) if x in "◀▶"], ["◀", "▶"])
        self.assertEqual([x for x in self.labels(self.view(5)) if x in "◀▶"], [])

    def test_the_switch_names_the_order_in_use(self) -> None:
        self.assertIn(dd.SORT_LABELS[dd.CLOSEST], self.labels(self.view(5)))
        self.assertIn(dd.SORT_LABELS[dd.AZ], self.labels(self.view(5, mode=dd.AZ)))

    def test_typing_a_name_is_still_there_and_every_label_fits_a_phone(self) -> None:
        labels = self.labels(self.view(60, page=1))
        self.assertIn(dd.TYPE_LABEL, labels)
        self.assertTrue(all(len(x) <= 25 for x in labels), labels)
