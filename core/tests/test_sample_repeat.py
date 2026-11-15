"""`sample_run --repeat` (#3545; Supervisor's rulings of 2026-11-15): N runs back to back, only the
last one kept (the recap and part 2's snapshot), the $5 cap measured from what each run spent, and
one summary saved for the next deploy to compare with. The runs themselves are stand-ins."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from dmbot.devtools.sample_run import __main__ as cli
from dmbot.devtools.sample_run import spread


def _args(folder: str, *extra: str) -> Any:
    return cli.parse_args(
        ["--guild", "1", "--dm", "2", "--lore", "l", "--session", "s", "--backup-dir", folder,
         "--commit", "abc1234", "--yes", *extra]
    )  # fmt: skip


class Repeat(unittest.TestCase):
    def go(self, folder: str, cost: float, *extra: str) -> tuple[list[dict[str, Any]], list[str]]:
        made: list[dict[str, Any]] = []
        said: list[str] = []

        async def run(args: Any, **kw: Any) -> int:
            made.append({"recap": kw["recap"], "snapshot": kw["snapshot"]})
            kw["metrics"].update(
                {"spend: the bot's AI ($)": cost, "backup and score (both, sure + AI): ties found":
                 40 + len(made), "questions: total": 30}
            )  # fmt: skip
            return 0

        with mock.patch.object(cli, "run", run):
            asyncio.run(cli.run_many(_args(folder, *extra), say=said.append))
        return made, said

    def test_five_runs_the_last_kept_and_a_summary_saved(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            made, said = self.go(d, 0.5)
            self.assertEqual(len(made), 5)
            self.assertEqual([m["recap"] for m in made], [False] * 4 + [True])
            self.assertEqual([m["snapshot"] is not None for m in made], [False] * 4 + [True])
            text = "\n".join(said)
            self.assertIn(
                "backup and score (both, sure + AI): ties found: median 43 (41 to 45, spread 4, "
                "5 runs)",
                text,
            )
            self.assertIn("questions: total: median 30 (30 to 30, spread 0, 5 runs)", text)
            [saved] = Path(d).glob("summary-*.json")
            self.assertEqual(spread.load(saved)[0], "abc1234")

    def test_the_cap_stops_new_runs_and_keeps_the_last_one_made(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            made, said = self.go(d, 1.5)  # (1.5, 3.0, 4.5: a fourth would pass $5)
        self.assertEqual([m["recap"] for m in made], [False, False, True])
        self.assertIn("Sample summary, commit abc1234: 3 runs, about $4.50 (#3545)", said)

    def test_the_next_deploy_is_compared_with_the_last_summary(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            old = spread.summarize([{"backup and score (both, sure + AI): ties found": 30}] * 5)
            spread.save(Path(d, "summary-20000101-000000.json"), old, commit="old1")
            _, said = self.go(d, 0.5)
        self.assertIn(
            "backup and score (both, sure + AI): ties found: 30 → 43 (+13; spreads 0 and 4):"
            " changed",
            said,
        )

    def test_without_yes_it_is_one_run_that_spends_nothing(self) -> None:
        calls: list[Any] = []

        async def run(args: Any, **kw: Any) -> int:
            calls.append(kw)
            return 0

        with tempfile.TemporaryDirectory() as d, mock.patch.object(cli, "run", run):
            args = _args(d)
            args.yes = False
            asyncio.run(cli.run_many(args, say=lambda _: None))
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
