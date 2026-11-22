"""The deed reader's pure checks against their own invented fixture (#4997, as #4991's
goodbyes): `met_deed_cases_session.txt` and `deed_cases_key.json`. The AI is not run: each key
deed is sent to `deed_reading.parse_deeds` as the AI might answer, and the test scores what is
kept with the right effect and kind (found, wrong, missed) and every `must_not` that gets through.
No AI, no database."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from dmbot.memory import deed_reading
from dmbot.memory.standing import DEED_EFFECTS
from tests.test_met_cases import load_session

FIXTURES = Path(__file__).parent / "fixtures" / "story_graph"
_FIELDS = ("doer", "deed", "mattered_to", "effect", "kind", "lines")


def read(item: dict[str, Any]) -> list[deed_reading.DeedProposal]:
    lines, dm = load_session()
    answer = json.dumps({"deeds": [{f: item[f] for f in _FIELDS if f in item}]})
    covered = {(a, b) for a, b in item.get("covered", [])}
    return list(deed_reading.parse_deeds(answer, lines, dm_lines=dm, covered=covered).deeds)


def score(key: dict[str, Any]) -> dict[str, int]:
    counts = {"found": 0, "missed": 0, "wrong": 0, "must_not_said": 0}
    for item in key["deeds"]:
        got = read(item)
        if not got:
            counts["missed"] += 1
        elif (
            got[0].effect != deed_reading.VERBS[item["effect"].casefold()]
            or got[0].kind != item["want_kind"]
            or list(got[0].viewers) != item.get("want_viewers", item["mattered_to"])
        ):
            counts["wrong"] += 1
        else:
            counts["found"] += 1
    counts["must_not_said"] = sum(bool(read(m)) for m in key["must_not"])
    return counts


def load_key() -> dict[str, Any]:
    key: dict[str, Any] = json.loads((FIXTURES / "deed_cases_key.json").read_text())
    return key


class DeedCases(unittest.TestCase):
    def test_the_key_uses_the_four_words(self) -> None:
        for item in load_key()["deeds"]:
            self.assertIn(deed_reading.VERBS[item["effect"].casefold()], dict(DEED_EFFECTS))

    def test_the_score(self) -> None:
        counts = score(load_key())
        print("deed cases:", counts)  # (counts only: the fixture is invented)
        self.assertEqual(counts, EXPECTED)


# What the checks score today; a change that moves these updates them on purpose
EXPECTED = {"found": 10, "missed": 0, "wrong": 0, "must_not_said": 1}  # (known: B on #4997)


if __name__ == "__main__":
    unittest.main()
