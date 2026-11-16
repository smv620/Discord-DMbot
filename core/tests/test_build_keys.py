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
            for section in ("nodes", "pairs", "events", "threads"):
                for row in key[section]:
                    with self.subTest(f"{name} {row.get('id', row.get('name'))}"):
                        self.assertIn("knowers", row)

    def test_every_backstory_pair_is_in_once(self) -> None:
        for name in build_keys.BUILT:
            key = built(name)
            sources = Counter(
                src["id"]
                for p in key["pairs"]
                for src in p["sources"]
                if src["doc"] in BACKSTORY_DOCS
            )
            for row_id in backstory_rows():
                with self.subTest(f"{name} {row_id}"):
                    self.assertEqual(sources[row_id], 1)

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

    def test_the_checker_reads_them(self) -> None:
        for name in build_keys.BUILT:
            key = built(name)
            with self.subTest(name):
                for pair in key["pairs"]:
                    self.assertIn(pair["relation"], gkc.GROUPS, pair["id"])


if __name__ == "__main__":
    unittest.main()
