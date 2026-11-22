"""The route length reader against its own invented fixture (#4255; #4991, the pattern of
`test_bias_cases`): `route_length_cases_key.json`. It scores `route_length.find` (found, missed,
wrong, and must-not quotes that gave a length) and pins the counts, so a change to the reader shows
up here. Pure: no AI, no database."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from dmbot.memory import route_length

FIXTURES = Path(__file__).parent / "fixtures" / "story_graph"


def load() -> dict[str, Any]:
    key: dict[str, Any] = json.loads((FIXTURES / "route_length_cases_key.json").read_text())
    return key


def score(key: dict[str, Any]) -> dict[str, int]:
    counts = {"found": 0, "missed": 0, "wrong": 0, "must_not_said": 0}
    for c in key["cases"]:
        got = route_length.find(c["quote"], c["ends"])
        if not got:
            counts["missed"] += 1
        else:
            counts["found" if got.casefold() == c["words"].casefold() else "wrong"] += 1
    for m in key["must_not"]:
        counts["must_not_said"] += bool(route_length.find(m["quote"], m["ends"]))
    return counts


class RouteLengthCases(unittest.TestCase):
    def test_the_key_is_well_formed(self) -> None:
        key = load()
        for c in key["cases"]:
            self.assertIn(c["words"], c["quote"])

    def test_the_score(self) -> None:
        counts = score(load())
        print("route length cases:", counts)  # (counts only: the fixture is invented)
        self.assertEqual(counts, EXPECTED)


# What the reader scores today; a reader change that moves these updates them on purpose
EXPECTED = {"found": 15, "missed": 0, "wrong": 0, "must_not_said": 0}
