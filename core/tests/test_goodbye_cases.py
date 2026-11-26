"""The goodbye reader's own checks against an invented session (#4537 slice 6; #4991, the pattern
of `test_bias_cases`): `goodbye_cases_session.txt` and `goodbye_cases_key.json`. The reader is AI;
each case is a goodbye an AI might propose, with its quote, so the score is what `parse_leaves`
keeps (kept, refused) and every must-not proposal must be refused. Behind DMBOT_PARTY_LEAVES, a
wrong goodbye is costly: the must-not count matters most. Pure: no AI, no database."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from dmbot.memory import goodbye_reading

FIXTURES = Path(__file__).parent / "fixtures" / "story_graph"


def load() -> tuple[dict[str, str], dict[str, Any]]:
    lines: dict[str, str] = {}
    for raw in (FIXTURES / "goodbye_cases_session.txt").read_text().splitlines():
        number, _, said = raw.partition(" ")
        if number.isdigit():
            lines[number] = said
    key: dict[str, Any] = json.loads((FIXTURES / "goodbye_cases_key.json").read_text())
    return lines, key


def kept(case: dict[str, str], lines: dict[str, str]) -> bool:
    answer = json.dumps(
        {
            "claims": [],
            "leaves": [{"who": case["who"], "quote": case["quote"], "lines": [case["line"]]}],
        }
    )
    return bool(goodbye_reading.parse_leaves(answer, lines).goodbyes)


def score(lines: dict[str, str], key: dict[str, Any]) -> dict[str, int]:
    counts = {"found": 0, "missed": 0, "must_not_said": 0}
    for g in key["goodbyes"]:
        counts["found" if kept(g, lines) else "missed"] += 1
    counts["must_not_said"] = sum(kept(m, lines) for m in key["must_not"])
    return counts


class GoodbyeCases(unittest.TestCase):
    def test_the_key_is_well_formed(self) -> None:
        lines, key = load()
        for case in (*key["goodbyes"], *key["must_not"]):
            self.assertIn(case["quote"].casefold(), lines[case["line"]].casefold())

    def test_the_score(self) -> None:
        lines, key = load()
        counts = score(lines, key)
        print("goodbye cases:", counts)  # (counts only: the fixture is invented)
        self.assertEqual(counts, EXPECTED)


# What the checks score today; a change that moves these updates them on purpose
EXPECTED = {"found": 10, "missed": 0, "must_not_said": 0}
