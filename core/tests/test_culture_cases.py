"""The place culture and population reader against its own invented fixture (#4257; #4982, the
pattern of `test_bias_cases`): `culture_cases_lore.txt` and `culture_cases_key.json`. It scores what
`place_peoples.read` finds for each place (found, missed, wrong, extra) and checks every `must_not`
place gives nothing, so a change to the reader shows up here as counts. Pure: no AI, no database."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from dmbot.memory import place_peoples

FIXTURES = Path(__file__).parent / "fixtures" / "story_graph"


def load() -> tuple[str, dict[str, Any]]:
    text = (FIXTURES / "culture_cases_lore.txt").read_text()
    key: dict[str, Any] = json.loads((FIXTURES / "culture_cases_key.json").read_text())
    return text, key


def places(key: dict[str, Any]) -> list[str]:
    return [c["place"] for c in key["cases"]] + [m["place"] for m in key["must_not"]]


def row(f: place_peoples.Found) -> tuple[str, str, str]:
    return f.layer, f.culture or f.value, f.how


def score(text: str, key: dict[str, Any]) -> dict[str, int]:
    known = places(key)
    counts = {"found": 0, "missed": 0, "wrong": 0, "extra": 0, "must_not_said": 0}
    wanted: dict[str, set[tuple[str, str, str]]] = {}
    for c in key["cases"]:
        wanted.setdefault(c["place"], set()).add((c["layer"], c["value"], c["how"]))
    for place, want in wanted.items():
        got = {row(f) for f in place_peoples.read(place, text, known)}
        for layer, value, how in want:
            same = [g for g in got if g[0] == layer]
            if (layer, value, how) in got:
                counts["found"] += 1
            elif same:
                counts["wrong"] += 1
            else:
                counts["missed"] += 1
        counts["extra"] += sum(1 for g in got if g[0] not in {w[0] for w in want})
    for m in key["must_not"]:
        counts["must_not_said"] += bool(place_peoples.read(m["place"], text, known))
    return counts


class CultureCases(unittest.TestCase):
    def test_the_key_is_well_formed(self) -> None:
        text, key = load()
        for c in key["cases"]:
            self.assertIn(c["place"], text)
            self.assertIn(c["layer"], (place_peoples.CULTURE, place_peoples.POPULATION))
        for m in key["must_not"]:
            self.assertIn(m["line"], text)  # (each must-not line is in the lore, word for word)
            self.assertIn(m["place"], m["line"])

    def test_the_score(self) -> None:
        text, key = load()
        counts = score(text, key)
        print("culture cases:", counts)  # (counts only: the fixture is invented, no lore words)
        self.assertEqual(counts, EXPECTED)


# What the reader scores today; a reader change that moves these updates them on purpose
EXPECTED = {"found": 12, "missed": 0, "wrong": 0, "extra": 0, "must_not_said": 0}
