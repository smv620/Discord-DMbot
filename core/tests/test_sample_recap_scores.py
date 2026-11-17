"""The sample run prints the recap's scores on #3545 (#4556, item 3): the must-have counts and the
four storytelling numbers with their average, numbers and fixed labels only. Invented words."""

from __future__ import annotations

import asyncio
import unittest

from dmbot.devtools.sample_run import costs
from dmbot.devtools.sample_run import steps as sample_steps
from dmbot.recaps import storytelling as st

REPORT = """\
Mira opened the silver lock and Oskar wept in the rain.
Calls: 4; tokens in 1200, out 800; spend $0.42

Must-haves (12 lines):
  covered 9, missed 1, not given 2, unread 0
   1  covered
Fact check and the final checks: 5 of 5 passed

Storytelling (1 to 5; flags only, the fact check wins):
  read beside 4 recap example(s)
  scene and stakes: 4 (reads as a story here)
  characters: 3 (partly there, uneven)
  momentum: 2 (reads as a list of events)
  sound: 4 (reads as a story here)
  average: 3.25
"""


class Parsing(unittest.TestCase):
    def test_the_counts_and_the_four_numbers_come_out_and_nothing_else(self) -> None:
        got = costs.recap_scores(REPORT)
        self.assertEqual(
            got,
            {
                "recap must-haves covered": 9,
                "recap must-haves missed": 1,
                "recap must-haves not given": 2,
                "recap storytelling: scene and stakes": 4,
                "recap storytelling: characters": 3,
                "recap storytelling: momentum": 2,
                "recap storytelling: sound": 4,
                "recap storytelling: average": "3.25",
            },
        )
        self.assertNotIn("silver lock", " ".join(f"{k} {v}" for k, v in got.items()))

    def test_the_labels_match_what_the_scorer_prints(self) -> None:
        shown = "\n".join(st.lines({p: (3, "some") for p in st.POINTS}, examples=4))
        self.assertEqual(len(costs.recap_scores(shown)), 5)  # (four points and the average)

    def test_a_report_with_no_score_gives_nothing(self) -> None:
        self.assertEqual(costs.recap_scores("Mira wept.\nCalls: 1"), {})
        self.assertEqual(
            costs.recap_scores("Storytelling: the judge's answer could not be read this time"), {}
        )


class WriteRecap(unittest.TestCase):
    def go(self, code: int = 0, with_scores: bool = True) -> tuple[list[str], dict[str, int | str]]:
        seen: list[str] = []
        scores: dict[str, int | str] = {}

        async def run(argv: list[str]) -> int:
            seen.extend(argv)
            print(REPORT)
            return code

        asyncio.run(
            sample_steps.write_recap(
                1, "c" * 32, 5, run=run, scores=scores if with_scores else None
            )
        )
        return seen, scores

    def test_scores_are_asked_for_and_read_only_when_wanted(self) -> None:
        seen, scores = self.go()
        self.assertIn("--score", seen)
        self.assertEqual(scores["recap storytelling: average"], "3.25")
        seen, scores = self.go(with_scores=False)
        self.assertNotIn("--score", seen)
        self.assertEqual(scores, {})

    def test_a_recap_that_was_not_made_has_no_scores(self) -> None:
        _seen, scores = self.go(code=1)
        self.assertEqual(scores, {})
