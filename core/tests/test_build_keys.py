"""The two-stage run's built keys (#4523 slice 1b): `total_key.json` and `full_key.json` are what
`build_keys` makes from the keys written by hand, every row says who knows it, every backstory row
is in once, and what no public document states stays its knowers' secret. No database."""

from __future__ import annotations

import json
import unittest
from collections import Counter
from typing import Any

from dmbot.devtools import build_keys
from dmbot.devtools import graph_key_check as gkc

BACKSTORY_DOCS = {f"backstory_{s}" for s in build_keys.BACKSTORIES}


def built(name: str) -> dict[str, Any]:
    return dict(json.loads((build_keys.KEYS / name).read_text(encoding="utf-8")))


def backstory_rows() -> dict[str, tuple[str, dict[str, Any]]]:
    rows = {}
    for slug in build_keys.BACKSTORIES:
        key = built(f"backstory_{slug}.json")
        for row in key["pairs"]:
            rows[row["id"]] = (f"backstory_{slug}", row)
    return rows


class TheBuiltKeys(unittest.TestCase):
    def test_the_files_are_what_the_script_builds(self) -> None:
        self.assertEqual(build_keys.main(["--check"]), 0, "run python -m dmbot.devtools.build_keys")

    def test_every_row_says_who_knows_it(self) -> None:
        for name in build_keys.BUILT:
            key = built(name)
            for section in ("nodes", "pairs", "events", "threads", "languages"):
                for row in key[section]:
                    with self.subTest(f"{name} {row.get('id', row.get('name'))}"):
                        self.assertIn("knowers", row)

    def test_every_backstory_row_is_in_once(self) -> None:
        """A connection, event or thread: its own row, or one source of a public row."""
        for name in build_keys.BUILT:
            key = built(name)
            seen: Counter[str] = Counter()
            for section in ("pairs", "events", "threads"):
                for row in key[section]:
                    if row.get("doc") in BACKSTORY_DOCS:
                        seen[row["id"]] += 1
                    for src in (*row.get("sources", ()), *row.get("also", ())):
                        if src["doc"] in BACKSTORY_DOCS:
                            seen[src["id"]] += 1
            for slug in build_keys.BACKSTORIES:
                story = built(f"backstory_{slug}.json")
                for section in ("pairs", "events", "threads"):
                    for row in story[section]:
                        with self.subTest(f"{name} {row['id']}"):
                            self.assertEqual(seen[row["id"]], 1)

    def test_a_reveal_of_another_kind_is_known_as_the_lore_says(self) -> None:
        """The reviewer's case: the lore tells Okthandom's haunting as a thread (lq03), and Cerric's
        knowing the Brotherhood's name as an event (e01); both are the party's."""
        for name in build_keys.BUILT:
            pairs = {p["id"]: p for p in built(name)["pairs"]}
            for row_id in ("o007", "o008", "c036"):
                with self.subTest(f"{name} {row_id}"):
                    self.assertEqual(pairs[row_id]["knowers"], "party")

    def test_what_the_lore_states_is_known_as_the_lore_says(self) -> None:
        pairs = {p["id"]: p for p in built("total_key.json")["pairs"]}
        roceran = pairs["l001"]  # (Cerric's father, in his backstory and the party's lore)
        self.assertEqual(roceran["knowers"], "party")
        self.assertEqual(roceran["in"], ["lore", "backstory_cerric"])

    def test_what_no_public_document_states_stays_secret(self) -> None:
        for name in build_keys.BUILT:
            pairs = {p["id"]: p for p in built(name)["pairs"]}
            with self.subTest(name):
                self.assertEqual(set(pairs["c018"]["knowers"]), {"Cerric Vanya'elen", "Wit"})
                self.assertEqual(pairs["b009"]["knowers"], ["Belleros Stormgrace", "Shimmergloom"])

    def test_the_session_reveals_what_it_says(self) -> None:
        """A backstory entry the session names (Clan Battlehammer) is the party's in stage 2."""
        nodes = {n["name"]: n for n in built("full_key.json")["nodes"]}
        clan = nodes["Clan Battlehammer"]
        self.assertIn("session", clan["docs"])
        self.assertIn("backstory_belleros", clan["docs"])
        self.assertEqual(clan["knowers"], "party")

    def test_the_ties_use_the_checkers_words(self) -> None:
        """(Scoring backstory rows is slice 2: graph_key_check gains the backstory docs.)"""
        for name in build_keys.BUILT:
            key = built(name)
            with self.subTest(name):
                for pair in key["pairs"]:
                    self.assertIn(pair["relation"], gkc.GROUPS, pair["id"])


def small(nodes: list[dict[str, Any]], pairs: list[dict[str, Any]]) -> build_keys._Key:
    return build_keys._Key({"nodes": nodes, "pairs": pairs, "events": [], "threads": []})


def story(nodes: list[dict[str, Any]], pairs: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    return {"speaker": "Mira", "nodes": nodes, "pairs": pairs, "events": [], "threads": [], **kw}


PERSON = {"kind": "person", "knowers": ["Mira"]}


class TheMerge(unittest.TestCase):
    """`_add_backstory` on small invented keys: the paths the sample data does not reach."""

    def test_a_name_only_a_backstory_gives_is_a_secret_name_beside_the_public_one(self) -> None:
        key = small([{"name": "Tavin", "kind": "person", "docs": ["lore"], "knowers": "party"}], [])
        build_keys._add_backstory(
            key, "mira", story([{"name": "Tavin Vell", "aliases": ["Tavin"], **PERSON}], []), {}, ""
        )
        (tavin,) = key.key["nodes"]
        self.assertEqual(tavin["knowers"], "party")
        self.assertEqual(tavin["secret_aliases"], {"Tavin Vell": ["Mira"]})
        self.assertNotIn("Tavin Vell", tavin.get("aliases", []))

    def test_a_tie_the_session_states_is_merged_and_known_as_it_says(self) -> None:
        nodes = [{"name": n, "kind": "person", "docs": ["session"], "knowers": "party"}
                 for n in ("Tavin", "Ulf")]  # fmt: skip
        said = {"id": "m1", "a": "Tavin", "relation": "knows", "b": "Ulf", "in": ["session"],
                "sources": [{"doc": "session", "id": "s1"}], "knowers": "party"}  # fmt: skip
        key = small(nodes, [said])
        row = {"id": "x1", "a": "Tavin", "relation": "knows", "b": "Ulf", "tier": "stated",
               "must_find": True, "line": 3, "knowers": ["Mira", "Tavin", "Ulf"]}  # fmt: skip
        build_keys._add_backstory(key, "mira", story([], [row]), {}, "")
        (only,) = key.key["pairs"]
        self.assertEqual(only["knowers"], "party")
        self.assertEqual(only["in"], ["session", "backstory_mira"])

    def test_a_reveal_that_points_at_nothing_stops_the_build(self) -> None:
        row = {"id": "x1", "a": "A", "relation": "knows", "b": "B", "tier": "stated",
               "must_find": True, "line": 1, "knowers": ["Mira"]}  # fmt: skip
        with self.assertRaises(ValueError):
            build_keys._add_backstory(small([], []), "mira", story([], [row]), {"x1": ["l999"]}, "")

    def test_a_node_two_backstories_name_is_known_to_both(self) -> None:
        key = small([], [])
        build_keys._add_backstory(key, "a", story([{"name": "Ulf", **PERSON}], []), {}, "")
        other = {"name": "Ulf", "kind": "person", "knowers": ["Rook"]}
        build_keys._add_backstory(key, "b", story([other], []), {}, "")
        (ulf,) = key.key["nodes"]
        self.assertEqual(ulf["knowers"], ["Mira", "Rook"])


if __name__ == "__main__":
    unittest.main()
