"""Golden questions for the rules index (#1952): what each kind of question cites, in both
editions, for the table and for the DM. Every cue list, everyday-word guard and per-name fallback
shares one index, so a change to one can quietly change what another question cites; this pins the
whole set. Pure: the real SRD index, no AI and no database. How to add a case: see the readme at
the top of tests/data/rules_golden.json."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from dmbot.rules.index import srd
from dmbot.sidebar import context

CASES = json.loads((Path(__file__).parent / "data" / "rules_golden.json").read_text("utf-8"))[
    "cases"
]
MIN_CASES = 120  # (the suite is meant to be broad; shrinking it should be deliberate)
INDEX = srd()


class GoldenQuestions(unittest.TestCase):
    def test_every_case_cites_what_it_should(self) -> None:
        for case in CASES:
            with self.subTest(question=case["question"], target=case["target"], mode=case["mode"]):
                hits = context.mentioned_rules(
                    case["question"],
                    INDEX,
                    case["target"],
                    case["fallback"],
                    both="both" in case["mode"],
                    table=case["mode"].startswith("table"),
                )
                got = [[h.entry.kind, h.entry.name, h.entry.edition] for h in hits]
                self.assertEqual(
                    got,
                    case["expect"],
                    f"\nquestion: {case['question']!r} ({case['target']}/{case['fallback']}, "
                    f"{case['mode']})\nexpected: {case['expect']}\ncited:    {got}",
                )

    def test_the_suite_is_broad_and_its_cases_are_well_formed(self) -> None:
        self.assertGreaterEqual(len(CASES), MIN_CASES)
        keys = {(c["question"], c["target"], c["fallback"], c["mode"]) for c in CASES}
        self.assertEqual(len(keys), len(CASES), "a case is listed twice")
        for case in CASES:
            self.assertIn(case["mode"], {"dm", "table", "dm both", "table both"})
            self.assertTrue(all(len(entry) == 3 for entry in case["expect"]))

    def test_the_table_never_cites_the_dms_chapter(self) -> None:
        # Whatever a case expects, a table question must not hold a DM-side entry.
        for case in CASES:
            if case["mode"].startswith("table"):
                for kind, name, edition in case["expect"]:
                    hit = INDEX.lookup(name, edition, edition, kind=kind)
                    assert hit is not None, name
                    self.assertFalse(hit.entry.dm_side, f"{name} for {case['question']!r}")
