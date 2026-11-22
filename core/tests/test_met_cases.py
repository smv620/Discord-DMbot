"""The met reader's pure checks against their own invented fixture (#4997, as #4991's goodbyes):
`met_deed_cases_session.txt` and `met_cases_key.json`. The AI is not run: each key item is sent to
`met_reading.parse_met` as the AI might answer, and the test scores what is kept (found, missed)
and every `must_not` that gets through, so a change to the checks shows up here as counts. No AI,
no database."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from dmbot.memory import met_reading

FIXTURES = Path(__file__).parent / "fixtures" / "story_graph"


def load_session() -> tuple[dict[str, str], set[str]]:
    """The session's lines (number to words) and the numbers the DM spoke."""
    lines: dict[str, str] = {}
    dm: set[str] = set()
    for raw in (FIXTURES / "met_deed_cases_session.txt").read_text().splitlines():
        number, _, rest = raw.partition(" ")
        speaker, _, said = rest.partition(": ")
        if number.isdigit():
            lines[number] = said
            if speaker == "DM":
                dm.add(number)
    return lines, dm


def met(item: dict[str, Any], lines: dict[str, str], dm: set[str]) -> list[met_reading.Met]:
    answer = {"what": item["what"], "count": item["count"], "lines": item["lines"]}
    got = met_reading.parse_met(json.dumps({"claims": [], "met": [answer]}), lines, dm_lines=dm)
    return got.met


def kept(item: dict[str, Any], lines: dict[str, str], dm: set[str]) -> bool:
    """A must-not that gets through in any form counts (so a tidier `what` can't hide it)."""
    return bool(met(item, lines, dm))


def score(key: dict[str, Any]) -> dict[str, int]:
    lines, dm = load_session()
    counts = {"found": 0, "missed": 0, "must_not_said": 0}
    for item in key["met"]:
        found = any(m.what == item["what"] for m in met(item, lines, dm))
        counts["found" if found else "missed"] += 1
    counts["must_not_said"] = sum(kept(m, lines, dm) for m in key["must_not"])
    return counts


def load_key() -> dict[str, Any]:
    key: dict[str, Any] = json.loads((FIXTURES / "met_cases_key.json").read_text())
    return key


class MetCases(unittest.TestCase):
    def test_the_key_cites_lines_of_the_session(self) -> None:
        lines, dm = load_session()
        self.assertEqual(len(lines), 22)
        for item in load_key()["met"]:
            self.assertTrue(set(map(str, item["lines"])) & dm, item["what"])

    def test_the_score(self) -> None:
        counts = score(load_key())
        print("met cases:", counts)  # (counts only: the fixture is invented)
        self.assertEqual(counts, EXPECTED)


# What the checks score today; a change that moves these updates them on purpose
EXPECTED = {"found": 11, "missed": 0, "must_not_said": 2}  # (known: a trace and B, #4997)


if __name__ == "__main__":
    unittest.main()
