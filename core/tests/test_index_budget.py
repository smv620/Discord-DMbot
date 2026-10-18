"""Budgets for the rules index (#1953): how long it takes to build, how long a sidebar question
takes and how much memory the build uses, so the data growing can't quietly slow the bot or crowd
the small server it shares with Postgres and Whisper. The limits are generous (a CI runner is
slower and busier than a laptop) and a miss is measured once more before it fails, as a noisy
neighbour can slow one run. Measured 2026-10-18 on the dev machine, when the index held the SRD
5.2.1 and 5.1 (about 3,200 names and spellings): build 40 ms, 6.7 MB peak, 0.6 ms a question.
If a budget is missed, find the cause (lazy loading, shared strings); do not raise the limit.
"""

from __future__ import annotations

import re
import unittest
from collections.abc import Callable
from pathlib import Path

from dmbot.devtools import index_stats
from dmbot.rules import index

BUILD_SECONDS = 2.0
QUESTION_MILLISECONDS = 5.0
PEAK_MEGABYTES = 150.0
SRC = Path(index.__file__).resolve().parents[1]


def within(measure: Callable[[], float], limit: float) -> float:
    """The measurement, taken once more if it misses the limit (the better of the two)."""
    value = measure()
    return value if value <= limit else min(value, measure())


class IndexBudget(unittest.TestCase):
    def test_the_index_builds_within_its_budget(self) -> None:
        took = within(lambda: index_stats.build_seconds(3), BUILD_SECONDS)
        self.assertLess(took, BUILD_SECONDS, f"the index took {took:.2f} s to build")

    def test_a_sidebar_question_is_answered_within_its_budget(self) -> None:
        rules = index.srd()
        took = within(
            lambda: index_stats.milliseconds_per_question(rules, 300), QUESTION_MILLISECONDS
        )
        self.assertLess(took, QUESTION_MILLISECONDS, f"{took:.2f} ms a question")

    def test_the_build_stays_within_its_memory_budget(self) -> None:
        peak = within(index_stats.peak_megabytes, PEAK_MEGABYTES)
        self.assertLess(peak, PEAK_MEGABYTES, f"peak {peak:.0f} MB while building")


class BuiltOnce(unittest.TestCase):
    def test_srd_gives_the_same_index_every_time(self) -> None:
        self.assertIs(index.srd(), index.srd())

    def test_only_the_cached_builder_is_used_by_the_bot(self) -> None:
        # The sidebar, the rule card and the typeahead all call srd(); a second build would hold
        # the data twice and cost its build time again.
        offenders = [
            str(path.relative_to(SRC))
            for path in SRC.rglob("*.py")
            if path.name not in ("index.py", "index_stats.py")
            and re.search(r"\bbuild_srd\(", path.read_text("utf-8"))
        ]
        self.assertEqual(offenders, [])

    def test_the_names_are_counted_for_both_editions(self) -> None:
        counts = index.srd().key_counts()
        self.assertEqual(sorted(counts), ["2014", "2024"])
        self.assertTrue(all(n > 0 for n in counts.values()))

    def test_the_measuring_script_builds_its_own_copy_each_time(self) -> None:
        self.assertIsNot(index.build_srd(), index.srd())
