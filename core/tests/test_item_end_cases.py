"""Matching a connection's end to a kept item or place, against an invented fixture (#4923, #4991;
the pattern of `test_bias_cases`): `item_end_cases_key.json`. It scores `end_match.Ends` with the
rule that a kept name in small letters is an end only whole, as `background.keep_connections` builds
it (found, missed, wrong, and must-not ends that matched something), and pins the counts. Pure."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from dmbot.memory import descriptions, end_match
from dmbot.memory.models import name_key

FIXTURES = Path(__file__).parent / "fixtures" / "story_graph"


def load() -> dict[str, Any]:
    key: dict[str, Any] = json.loads((FIXTURES / "item_end_cases_key.json").read_text())
    return key


def reader(kept: list[str]) -> end_match.Ends:
    """As `keep_connections` builds it: a small-letter kept name is matched only whole (#4923)."""
    whole = {name_key(n) for n in kept if n == n.casefold() and descriptions.kept_thing_alone(n)}
    exact = {name_key(n): n for n in kept}
    return end_match.Ends(exact, {n for n in kept if name_key(n) not in whole})


def score(key: dict[str, Any]) -> dict[str, int]:
    ends = reader(key["kept"])
    counts = {"found": 0, "missed": 0, "wrong": 0, "must_not_said": 0}
    for c in key["cases"]:
        got = ends.match(c["end"], small_whole_only=True).name
        if got is None:
            counts["missed"] += 1
        else:
            counts["found" if got == c["name"] else "wrong"] += 1
    for m in key["must_not"]:
        hit = ends.match(m["end"], small_whole_only=True)
        counts["must_not_said"] += bool(hit.name or hit.ambiguous)
    return counts


class ItemEndCases(unittest.TestCase):
    def test_the_score(self) -> None:
        counts = score(load())
        print("item end cases:", counts)  # (counts only: the fixture is invented)
        self.assertEqual(counts, EXPECTED)


# What the matcher scores today; a change that moves these updates them on purpose
EXPECTED = {"found": 10, "missed": 0, "wrong": 0, "must_not_said": 0}
