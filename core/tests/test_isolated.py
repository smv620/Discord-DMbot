"""Who is not linked to the party (#3740): the graph clean-up's rule (`memory/isolated.py`) and
the page's shape (`web/graph_cleanup.shape`). Pure; invented names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory.isolated import Entry, Link, isolated_groups
from dmbot.web import graph_cleanup


def pc(i: str) -> Entry:
    return Entry(i, "character", "confirmed", player_character=True)


def npc(i: str, status: str = "confirmed", *, unnamed: bool = False) -> Entry:
    return Entry(i, "character", status, unnamed)


def link(a: str, b: str, predicate: str = "knows", status: str = "confirmed") -> Link:
    return Link(f"{a}-{b}", a, b, predicate, status)


class TheRule(unittest.TestCase):
    def test_a_chain_to_a_player_character_is_not_isolated_and_a_pair_alone_is_one_group(
        self,
    ) -> None:
        entries = [pc("hero"), npc("bram"), npc("mira"), npc("drunk"), npc("sailors")]
        links = [link("hero", "bram"), link("bram", "mira"), link("drunk", "sailors")]
        self.assertEqual(isolated_groups(entries, links), [["drunk", "sailors"]])

    def test_the_party_is_a_seed_too(self) -> None:
        party = Entry("party", "faction", "confirmed", party=True)
        groups = isolated_groups([pc("hero"), party, npc("bram")], [link("party", "bram")])
        self.assertEqual(groups, [])

    def test_no_player_character_means_an_empty_page(self) -> None:
        self.assertEqual(isolated_groups([npc("bram"), npc("mira")], []), [])

    def test_a_party_with_no_kept_player_character_is_no_anchor(self) -> None:
        party = Entry("party", "faction", "confirmed", party=True)
        waiting_pc = Entry("hero", "character", "proposed", player_character=True)
        self.assertEqual(isolated_groups([party, waiting_pc, npc("bram")], []), [])

    def test_a_waiting_player_character_is_not_a_seed(self) -> None:
        waiting_pc = Entry("maybe", "character", "proposed", player_character=True)
        entries = [pc("hero"), waiting_pc, npc("bram")]
        self.assertEqual(isolated_groups(entries, [link("maybe", "bram")]), [["bram"]])

    def test_a_link_to_itself_ties_nobody(self) -> None:
        self.assertEqual(
            isolated_groups([pc("hero"), npc("bram")], [link("bram", "bram")]), [["bram"]]
        )

    def test_a_rejected_link_or_the_plan_s_led_to_never_joins(self) -> None:
        entries = [pc("hero"), npc("bram"), npc("mira")]
        links = [link("hero", "bram", status="rejected"), link("hero", "mira", "led_to")]
        self.assertEqual(isolated_groups(entries, links), [["bram"], ["mira"]])

    def test_a_waiting_link_joins(self) -> None:
        groups = isolated_groups(
            [pc("hero"), npc("bram")], [link("hero", "bram", status="proposed")]
        )
        self.assertEqual(groups, [])

    def test_a_waiting_name_joins_a_chain_but_is_not_listed(self) -> None:
        entries = [pc("hero"), npc("corwin", "proposed"), npc("bram"), npc("ghost", "proposed")]
        # bram reaches the hero through a waiting name; the lone waiting name is the names list's
        self.assertEqual(
            isolated_groups(entries, [link("hero", "corwin"), link("corwin", "bram")]), []
        )
        self.assertEqual(isolated_groups(entries, []), [["bram"]])

    def test_an_unnamed_person_is_listed_waiting_or_kept(self) -> None:
        entries = [pc("hero"), npc("woman", "proposed", unnamed=True)]
        self.assertEqual(isolated_groups(entries, []), [["woman"]])

    def test_a_place_is_no_interaction(self) -> None:
        town = Entry("town", "place", "confirmed")
        entries = [pc("hero"), npc("bram"), town]
        links = [link("hero", "town", "located_in"), link("bram", "town", "located_in")]
        self.assertEqual(isolated_groups(entries, links), [["bram"]])

    def test_rejected_or_merged_entries_never_count(self) -> None:
        entries = [pc("hero"), npc("gone", "rejected"), npc("bram")]
        self.assertEqual(
            isolated_groups(entries, [link("hero", "gone"), link("gone", "bram")]), [["bram"]]
        )

    def test_largest_group_first_then_by_id(self) -> None:
        entries = [pc("hero"), npc("a"), npc("b"), npc("c"), npc("z")]
        groups = isolated_groups(entries, [link("b", "c")])
        self.assertEqual(groups, [["b", "c"], ["a"], ["z"]])


class ThePageShape(unittest.TestCase):
    def entry(self, i: str, **kw: Any) -> dict[str, Any]:
        return {"id": i, "name": i.title(), "type": "character", "status": "confirmed", **kw}

    def test_groups_with_labels_scenes_and_inside_links(self) -> None:
        got = {
            "entries": [
                self.entry("hero", role="player_character"),
                self.entry(
                    "drunk", name="stale", unnamed=True, unnamedWhat="drunk with a monkey tattoo"
                ),
                self.entry("sailors", type="faction"),
            ],
            "links": [
                {"id": "l1", "a": "drunk", "b": "sailors", "predicate": "knows", "detail": "",
                 "directional": False, "status": "confirmed", "secret": False, "at": 1},
            ],
            "scenes": {"drunk": "He sat with the sailors all night."},
        }  # fmt: skip
        page = graph_cleanup.shape(got)
        self.assertEqual((page["total"], page["truncated"]), (2, False))
        (group,) = page["groups"]
        drunk = next(e for e in group["entries"] if e["id"] == "drunk")
        self.assertEqual(drunk["name"], "unnamed - drunk with a monkey tattoo")
        self.assertEqual(drunk["scene"], "He sat with the sailors all night.")
        self.assertNotIn("scene", next(e for e in group["entries"] if e["id"] == "sailors"))
        self.assertEqual([x["id"] for e in group["links"] for x in e["links"]], ["l1"])

    def test_past_the_limit_a_count(self) -> None:
        got = {
            "entries": [self.entry("hero", role="player_character")]
            + [self.entry(f"n{i:03d}") for i in range(graph_cleanup.MAX_ENTRIES + 5)],
            "links": [],
            "scenes": {},
        }
        page = graph_cleanup.shape(got)
        self.assertEqual(page["total"], graph_cleanup.MAX_ENTRIES + 5)
        self.assertTrue(page["truncated"])
        self.assertEqual(len(page["groups"]), graph_cleanup.MAX_ENTRIES)

    def test_one_cluster_bigger_than_the_page_is_cut_and_says_so(self) -> None:
        n = graph_cleanup.MAX_ENTRIES + 5
        ids = [f"n{i:03d}" for i in range(n)]
        got = {
            "entries": [self.entry("hero", role="player_character")] + [self.entry(i) for i in ids],
            "links": [
                {"id": f"l{k}", "a": ids[k], "b": ids[k + 1], "predicate": "knows",
                 "detail": "", "directional": False, "secret": False, "at": 1,
                 "status": "confirmed"}
                for k in range(n - 1)
            ],
            "scenes": {},
        }  # fmt: skip
        page = graph_cleanup.shape(got)
        self.assertEqual((page["total"], page["truncated"]), (n, True))
        (group,) = page["groups"]
        self.assertEqual(len(group["entries"]), graph_cleanup.MAX_ENTRIES)
        shown = {e["id"] for e in group["entries"]}
        for edge in group["links"]:  # (only the links inside what is shown)
            self.assertTrue({edge["a"], edge["b"]} <= shown)

    def test_the_map_s_marks_are_not_sent(self) -> None:
        got = {
            "entries": [self.entry("hero", role="player_character"), self.entry("a"),
                        self.entry("b")],
            "links": [{"id": "l1", "a": "a", "b": "b", "predicate": "knows",
                       "detail": "", "directional": False, "secret": False, "at": 1,
                 "status": "confirmed"}],
            "scenes": {},
        }  # fmt: skip
        (group,) = graph_cleanup.shape(got)["groups"]
        (link_out,) = [x for e in group["links"] for x in e["links"]]
        self.assertNotIn("revealed", link_out)
        self.assertNotIn("pinned", link_out)
