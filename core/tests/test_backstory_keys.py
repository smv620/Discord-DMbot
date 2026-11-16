"""The four secret backstory keys and the lore key's knowers (#4523 slice 1a): the answer keys the
two-stage sample run scores against hang together. Every row says who knows it, every name a row
uses is one of the key's own entries, ids never repeat, and what the public lore reveals points
at rows that exist and say the same thing. The repo is public, so a key holds names, kinds, ties
and knowers, never a sentence of the owner's documents (Supervisor's ruling on #4523). The word
cap here only keeps notes short: the proof that nothing is quoted is the review's comparison with
the documents, which are not in the repo. No database or network."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from dmbot.devtools import graph_key_check as gkc

FIXTURES = Path(__file__).parent / "fixtures" / "story_graph"
BACKSTORIES = ("cerric", "okthandom", "claude", "belleros")
PLAYERS = {"Cerric Vanya'elen", "Okthandom Korran", "Claude Ambere", "Belleros Stormgrace"}
GROUP_KNOWERS = {"party", "everyone", "dm"}
SPEAKERS = {
    "cerric": "Cerric Vanya'elen",
    "okthandom": "Okthandom Korran",
    "claude": "Claude Ambere",
    "belleros": "Belleros Stormgrace",
}
ROWS = ("pairs", "events", "threads")
WORDS_MAX = 25  # (a note or an event is a short paraphrase, never a passage)
TIERS = {"stated", "inferred"}


def load(name: str) -> dict[str, Any]:
    with (FIXTURES / name).open(encoding="utf-8") as f:
        return dict(json.load(f))


# Rows whose knowers leave out someone on them, on purpose: the ringmaster died there.
NOT_EVERY_END = {"ae04"}


def on_row(key: dict[str, Any], row: dict[str, Any]) -> set[str]:
    """The people and creatures a row names: a pair's two ends, an event's who."""
    people = {n["name"] for n in key["nodes"] if n["kind"] in {"person", "creature"}}
    return {row.get("a"), row.get("b"), *row.get("who", ())} & people


def names_of(key: dict[str, Any]) -> set[str]:
    """Every name and alias the key's entries answer to."""
    found: set[str] = set()
    for node in key["nodes"]:
        found.add(node["name"])
        found.update(node.get("aliases", ()))
    return found


class TheBackstoryKeys(unittest.TestCase):
    def setUp(self) -> None:
        self.keys = {slug: load(f"backstory_{slug}.json") for slug in BACKSTORIES}
        self.lore = load("lore_key.json")

    def test_each_is_its_owners_backstory(self) -> None:
        for slug, key in self.keys.items():
            with self.subTest(slug):
                self.assertEqual(key["speaker"], SPEAKERS[slug])
                self.assertIn(key["speaker"], {n["name"] for n in key["nodes"]})
                self.assertEqual(key["campaign"], self.lore["campaign"])
                self.assertNotIn("http", key["source"])  # (the document's address is the README's)

    def test_ids_never_repeat_across_the_keys(self) -> None:
        seen: dict[str, str] = {}
        for slug, key in self.keys.items():
            for section in ROWS:
                for row in key[section]:
                    with self.subTest(row["id"]):
                        self.assertNotIn(row["id"], seen, f"also in {seen.get(row['id'])}")
                        seen[row["id"]] = slug

    def test_every_pair_names_the_keys_own_entries(self) -> None:
        for slug, key in self.keys.items():
            names = names_of(key)
            for pair in key["pairs"]:
                with self.subTest(f"{slug} {pair['id']}"):
                    self.assertIn(pair["a"], names)
                    self.assertIn(pair["b"], names)
                    self.assertTrue(pair["line"] > 0)

    def test_knowers_are_a_group_word_or_the_keys_people(self) -> None:
        for slug, key in self.keys.items():
            people = {n["name"] for n in key["nodes"] if n["kind"] in {"person", "creature"}}
            for section in ("nodes", *ROWS):
                for row in key[section]:
                    knowers = row["knowers"]  # (written out on every row)
                    label = f"{slug} {row.get('id', row.get('name'))}"
                    with self.subTest(label):
                        self.assertIn(row.get("check", False), {True, False})
                        if isinstance(knowers, str):
                            self.assertIn(knowers, GROUP_KNOWERS)
                        else:
                            self.assertTrue(knowers)
                            self.assertLessEqual(set(knowers), people)

    def test_the_owners_ruling_c_cases_are_in_the_keys(self) -> None:
        """What happens between two is known to both, NPCs included (Supervisor on #4566); something
        nobody knows, not even the speaker, is the DM's alone (Claude doesn't remember what he made
        himself forget); and a death only Claude saw is Claude's."""
        cerric = {r["id"]: r for s in ("pairs", "events") for r in self.keys["cerric"][s]}
        self.assertEqual(set(cerric["ce04"]["knowers"]), {"Cerric Vanya'elen", "Wit"})
        self.assertEqual(set(cerric["c018"]["knowers"]), {"Cerric Vanya'elen", "Wit"})
        self.assertIn("Wit", cerric["c016"]["knowers"])
        self.assertTrue(cerric["c006"]["check"])  # (the grandfather is dead)
        claude = {r["id"]: r for r in self.keys["claude"]["events"]}
        self.assertEqual(claude["ae01"]["knowers"], "dm")
        self.assertEqual(claude["ae04"]["knowers"], ["Claude Ambere"])

    def test_everyone_on_a_row_knows_it(self) -> None:
        """Ruling C for every row: a list of knowers holds every person on the row."""
        for slug, key in self.keys.items():
            for section in ("pairs", "events"):
                for row in key[section]:
                    knowers = row["knowers"]
                    if isinstance(knowers, str) or row["id"] in NOT_EVERY_END:
                        continue
                    with self.subTest(f"{slug} {row['id']}"):
                        self.assertLessEqual(on_row(key, row), set(knowers))
                        self.assertIn(key["speaker"], knowers)

    def test_every_event_names_the_keys_own_entries(self) -> None:
        for slug, key in self.keys.items():
            names = names_of(key)
            for event in key["events"]:
                with self.subTest(f"{slug} {event['id']}"):
                    self.assertTrue(event["who"])
                    self.assertLessEqual(set(event["who"]), names)

    def test_rows_use_the_checks_words(self) -> None:
        """So graph_key_check can score these keys as it scores the others."""
        for slug, key in self.keys.items():
            for pair in key["pairs"]:
                with self.subTest(f"{slug} {pair['id']}"):
                    self.assertIn(pair["relation"], gkc.GROUPS)
                    self.assertIn(pair["tier"], TIERS)
            for node in key["nodes"]:
                with self.subTest(f"{slug} {node['name']}"):
                    self.assertIn(node["kind"], gkc.ENTRY_TYPES)

    def test_a_row_everyone_knows_joins_entries_everyone_knows(self) -> None:
        """Or a scorer would call a right "everyone knows Waterdeep" a leak of a secret."""
        for slug, key in self.keys.items():
            nodes = {n["name"]: n for n in key["nodes"]}
            for pair in key["pairs"]:
                if pair.get("knowers") == "everyone":
                    with self.subTest(f"{slug} {pair['id']}"):
                        for end in (pair["a"], pair["b"]):
                            self.assertEqual(nodes[end].get("knowers"), "everyone", end)

    def test_no_row_is_long_enough_to_be_a_quote(self) -> None:
        def strings(value: Any) -> list[str]:
            if isinstance(value, str):
                return [value]
            if isinstance(value, dict):
                return [s for v in value.values() for s in strings(v)]
            if isinstance(value, list):
                return [s for v in value for s in strings(v)]
            return []

        for slug, key in self.keys.items():
            for section in ("nodes", *ROWS, "languages", "must_not_find"):
                for text in strings(key[section]):
                    with self.subTest(f"{slug} {text[:40]}"):
                        self.assertLessEqual(len(text.split()), WORDS_MAX)

    def test_threads_the_lore_also_tells_point_at_its_threads(self) -> None:
        lore_threads = {t["id"] for t in self.lore["threads"]}
        for key in self.keys.values():
            for thread in key["threads"]:
                if "same_as" in thread:
                    with self.subTest(thread["id"]):
                        self.assertIn(thread["same_as"], lore_threads)
                        self.assertIn(thread["taker"], PLAYERS)


class TheLoreKeysKnowers(unittest.TestCase):
    def setUp(self) -> None:
        self.lore = load("lore_key.json")
        self.rows = {
            row["id"]: row
            for slug in BACKSTORIES
            for section in ROWS
            for row in load(f"backstory_{slug}.json")[section]
        }

    def test_every_node_pair_and_event_says_who_knows_it(self) -> None:
        for section in ("nodes", "pairs", "events"):
            for row in self.lore[section]:
                with self.subTest(row.get("id", row.get("name"))):
                    self.assertIn(row.get("knowers"), {"party", "everyone"})
        self.assertEqual(self.lore["thread_knowers"], "party")

    def test_the_world_is_everyones_and_the_party_s_own_story_is_the_party_s(self) -> None:
        pairs = {p["id"]: p for p in self.lore["pairs"]}
        self.assertEqual(pairs["l046"]["knowers"], "everyone")  # (a town in the Ten-Towns)
        self.assertEqual(pairs["l001"]["knowers"], "party")  # (Cerric's father, his background)
        nodes = {n["name"]: n for n in self.lore["nodes"]}
        self.assertEqual(nodes["Auril the Frostmaiden"]["knowers"], "everyone")
        self.assertEqual(nodes["Roceran"]["knowers"], "party")

    def test_a_revealed_pair_says_what_its_lore_pair_says(self) -> None:
        """Judged row by row: the same two entries and the same tie, after aliases."""
        canon: dict[str, str] = {}
        for slug in BACKSTORIES:
            for node in load(f"backstory_{slug}.json")["nodes"]:
                for name in (node["name"], *node.get("aliases", ())):
                    canon[name] = node["name"]
        for node in self.lore["nodes"]:
            for name in (node["name"], *node.get("aliases", ())):
                canon.setdefault(name, canon.get(node["name"], node["name"]))
        lore_pairs = {p["id"]: p for p in self.lore["pairs"]}

        def triple(p: dict[str, Any]) -> tuple[str, str, str]:
            return (canon.get(p["a"], p["a"]), p["relation"], canon.get(p["b"], p["b"]))

        revealed_pairs = set()
        for by, ids in self.lore["reveals"].items():
            for revealed in ids:
                row = self.rows[revealed]
                if by in lore_pairs and "relation" in row:
                    revealed_pairs.add(revealed)
                    with self.subTest(f"{by} {revealed}"):
                        self.assertEqual(triple(row), triple(lore_pairs[by]))
        lore_triples = {triple(p) for p in lore_pairs.values()}
        for revealed, row in self.rows.items():
            if "relation" in row and triple(row) in lore_triples:
                with self.subTest(revealed):
                    self.assertIn(revealed, revealed_pairs, "the lore says it: list it in reveals")

    def test_every_reveal_is_a_lore_row_and_a_backstory_row(self) -> None:
        lore_ids = {
            row["id"] for section in ("pairs", "events", "threads") for row in self.lore[section]
        }
        for by, ids in self.lore["reveals"].items():
            with self.subTest(by):
                self.assertIn(by, lore_ids)
                for revealed in ids:
                    self.assertIn(revealed, self.rows)

    def test_what_stays_secret_after_the_lore(self) -> None:
        """Belleros told no one about his shadow; the lore never names it, so it stays his."""
        revealed = {i for ids in self.lore["reveals"].values() for i in ids}
        self.assertNotIn("bb02", revealed)
        self.assertNotIn("c018", revealed)  # (Wit's respect: Cerric and Wit only)
        self.assertNotIn("ae04", revealed)  # (the ringmaster's death: the lore never says it)
        self.assertNotIn("a003", revealed)
        self.assertIn("c001", revealed)


if __name__ == "__main__":
    unittest.main()
