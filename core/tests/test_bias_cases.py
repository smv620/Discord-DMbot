"""The feelings-toward-peoples reader against its own invented fixture (#4229 slice 4; plan on
#3545, item 2): `bias_cases_lore.txt` and `bias_cases_key.json`. It scores what the reader finds
(found, missed, wrong step, extra) and checks every `must_not` sentence gives nothing, so a change
to `bias_read` shows up here as counts. No AI, no database: the reader is pure."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from dmbot.memory import bias_read

FIXTURES = Path(__file__).parent / "fixtures" / "story_graph"


def load() -> tuple[str, dict[str, Any]]:
    text = (FIXTURES / "bias_cases_lore.txt").read_text()
    key: dict[str, Any] = json.loads((FIXTURES / "bias_cases_key.json").read_text())
    return text, key


def ids(key: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """The reader's inputs: a holder for each place, group or people (never a person: the caller
    leaves people out, as `name_background.store_biases` does), and an id for each culture."""
    persons = set(key["person_holders"])
    holders = {h: f"h:{h}" for h in key["holders"] if h not in persons}
    cultures = {c: f"c:{c}" for c in key["cultures"]}
    return holders, cultures


def score(text: str, key: dict[str, Any]) -> dict[str, int]:
    holders, cultures = ids(key)
    culture_of = {v: k for k, v in cultures.items()}
    got = {
        (f.holder_id, f.lineage or culture_of[f.culture_id]): f.step
        for f in bias_read.read(text, holders, cultures)
    }
    counts = {"found": 0, "missed": 0, "wrong_step": 0, "extra": 0}
    wanted = set()
    for want in key["attitudes"]:
        pair = (holders[want["holder"]], want["toward"])
        wanted.add(pair)
        if pair not in got:
            counts["missed"] += 1
        elif got[pair].casefold() != want["step"]:
            counts["wrong_step"] += 1
        else:
            counts["found"] += 1
    counts["extra"] = sum(1 for pair in got if pair not in wanted)
    return counts


class BiasCases(unittest.TestCase):
    def test_the_key_is_well_formed(self) -> None:
        text, key = load()
        holders, _ = ids(key)
        for want in key["attitudes"]:
            self.assertIn(want["holder"], holders)
            self.assertNotIn(want["holder"], key["person_holders"])
        for line in key["must_not"]:
            self.assertIn(line, text)  # (each must-not line is in the lore, word for word)

    def test_every_must_not_line_gives_nothing(self) -> None:
        _, key = load()
        holders, cultures = ids(key)
        for line in key["must_not"]:
            with self.subTest(line):
                self.assertEqual(bias_read.read(line, holders, cultures), [])

    def test_the_score(self) -> None:
        # (the counts a reader change moves: #4980's nine, then #4982's five fresh sentences)
        text, key = load()
        counts = score(text, key)
        print("bias cases:", counts)  # (counts only: the fixture is invented, no lore words)
        self.assertEqual(counts, EXPECTED)


# What the reader scores today; a reader change that moves these updates them on purpose. The four
# misses are #4982's fresh shapes, waiting for Supervisor's ruling on that issue.
EXPECTED = {"found": 13, "missed": 1, "wrong_step": 0, "extra": 0}
