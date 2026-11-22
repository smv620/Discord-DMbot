"""The "Who is this?" answer checks against their own invented fixture (#4997, as #4991's
goodbyes): `who_cases_key.json`. The AI is not run: each ranked answer is sent to
`who_reading.parse_answers` as the AI might give it, and the test scores what is kept as the key
wants (found, wrong, missed) and every `must_not` that gets through. No AI, no database."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from dmbot.memory import who_reading
from dmbot.memory.who_reading import Answer, Choice, WhoItem

FIXTURES = Path(__file__).parent / "fixtures" / "story_graph"


def load() -> tuple[list[WhoItem], dict[str, Any]]:
    key: dict[str, Any] = json.loads((FIXTURES / "who_cases_key.json").read_text())
    items = [
        WhoItem(
            i["number"],
            i["name"],
            choices=tuple(Choice(c["ref"], c["kind"], tuple(c["names"])) for c in i["choices"]),
        )
        for i in key["items"]
    ]
    return items, key


def answers(case: dict[str, Any], items: list[WhoItem]) -> dict[int, tuple[Answer, ...]]:
    answer = json.dumps({"answers": [{"item": case["item"], "ranked": [case["entry"]]}]})
    return who_reading.parse_answers(answer, items).answers


def read(case: dict[str, Any], items: list[WhoItem]) -> tuple[Answer, ...]:
    return answers(case, items).get(case["item"], ())


def got_through(case: dict[str, Any], items: list[WhoItem]) -> bool:
    """Any answer kept for any item (an item number `true` would find item 1 without its guard)."""
    return any(answers(case, items).values())


def score(items: list[WhoItem], key: dict[str, Any]) -> dict[str, int]:
    counts = {"found": 0, "missed": 0, "wrong": 0, "must_not_said": 0}
    for case in key["cases"]:
        got = read(case, items)
        if not got:
            counts["missed"] += 1
        elif got[0] != Answer(**case["want"]):
            counts["wrong"] += 1
        else:
            counts["found"] += 1
    counts["must_not_said"] = sum(got_through(m, items) for m in key["must_not"])
    return counts


class WhoCases(unittest.TestCase):
    def test_the_key_asks_only_its_own_items(self) -> None:
        items, key = load()
        numbers = {i.number for i in items}
        for case in key["cases"]:
            self.assertIn(case["item"], numbers)

    def test_the_score(self) -> None:
        items, key = load()
        counts = score(items, key)
        print("who cases:", counts)  # (counts only: the fixture is invented)
        self.assertEqual(counts, EXPECTED)


# What the checks score today; a change that moves these updates them on purpose
EXPECTED = {"found": 10, "missed": 0, "wrong": 0, "must_not_said": 0}


if __name__ == "__main__":
    unittest.main()
