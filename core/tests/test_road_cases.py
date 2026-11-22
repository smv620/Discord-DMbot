"""The road condition reader against its own invented fixture (#4258; #4982, the pattern of
`test_bias_cases`): `road_cases_lore.txt` and `road_cases_key.json`. It scores what
`route_condition.read` and `read_between` find (found, missed, wrong, extra) and checks every
`must_not` route gives nothing, so a change to the reader shows up here as counts. Pure: no AI, no
database."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from dmbot.memory import route_condition as rc

FIXTURES = Path(__file__).parent / "fixtures" / "story_graph"


def load() -> tuple[str, dict[str, Any]]:
    text = (FIXTURES / "road_cases_lore.txt").read_text()
    key: dict[str, Any] = json.loads((FIXTURES / "road_cases_key.json").read_text())
    return text, key


def score(text: str, key: dict[str, Any]) -> dict[str, int]:
    counts = {"found": 0, "missed": 0, "wrong": 0, "extra": 0, "must_not_said": 0}
    for c in key["cases"]:
        said = rc.read(c["route"], text, is_route=True)
        if said is None:
            counts["missed"] += 1
        else:
            counts["found" if said.value == c["value"] else "wrong"] += 1
    between = {(b.a, b.b): b.said.value for b in rc.read_between(text)}
    wanted = {(b["a"], b["b"]): b["value"] for b in key["between"]}
    for pair, value in wanted.items():
        if pair not in between:
            counts["missed"] += 1
        else:
            counts["found" if between[pair] == value else "wrong"] += 1
    counts["extra"] = sum(1 for pair in between if pair not in wanted)
    for m in key["must_not"]:
        counts["must_not_said"] += rc.read(m["route"], text, is_route=True) is not None
    return counts


class RoadCases(unittest.TestCase):
    def test_the_key_is_well_formed(self) -> None:
        text, key = load()
        for c in key["cases"]:
            self.assertIn(c["route"], text)
            self.assertIn(c["value"], rc.VALUES)
        for m in key["must_not"]:
            self.assertIn(m["line"], text)
            self.assertIn(m["route"], m["line"])

    def test_the_score(self) -> None:
        text, key = load()
        counts = score(text, key)
        print("road cases:", counts)  # (counts only: the fixture is invented, no lore words)
        self.assertEqual(counts, EXPECTED)


# What the reader scores today; a reader change that moves these updates them on purpose
EXPECTED = {"found": 11, "missed": 1, "wrong": 0, "extra": 0, "must_not_said": 0}
