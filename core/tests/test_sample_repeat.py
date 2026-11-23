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
from dmbot.devtools.sample_run import steps as sample_steps


def _args(folder: str, *extra: str) -> Any:
    return cli.parse_args(
        ["--guild", "1", "--dm", "2", "--lore", "l", "--session", "s", "--backup-dir", folder,
         "--commit", "abc1234", "--yes", *extra]
    )  # fmt: skip


class Repeat(unittest.TestCase):
    def go(
        self, folder: str, costs: list[float], *extra: str, fail_at: int = 0
    ) -> tuple[list[dict[str, Any]], list[str], list[str], int]:
        made: list[dict[str, Any]] = []
        said: list[str] = []
        recaps: list[str] = []

        async def run(args: Any, **kw: Any) -> int:
            i = len(made)
            made.append({"recap": kw["recap"], "snapshot": kw["snapshot"]})
            kw["kept"].update(guild=1, campaign=f"c{i}", started=100)
            kw["metrics"].update(
                {"spend: the bot's AI ($)": costs[i % len(costs)],
                 "backup and score (both, sure + AI): ties found": 41 + i,
                 "questions: total": 30}
            )  # fmt: skip
            if fail_at and i + 1 == fail_at:
                raise RuntimeError("stand-in")
            return 0

        async def recap(guild: int, campaign: str, started: int, **_: Any) -> int:
            recaps.append(campaign)
            return 0

        with (
            mock.patch.object(cli, "run", run),
            mock.patch.object(sample_steps, "write_recap", recap),
        ):
            code = asyncio.run(cli.run_many(_args(folder, *extra), say=said.append))
        return made, said, recaps, code

    def test_five_runs_one_recap_for_the_kept_run_and_a_summary_saved(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            made, said, recaps, code = self.go(d, [0.5])
            self.assertEqual((len(made), code), (5, 0))
            self.assertEqual({m["recap"] for m in made}, {False})
            self.assertEqual(len({m["snapshot"] for m in made}), 1)  # (the last written is kept)
            self.assertEqual(recaps, ["c4"])
            text = "\n".join(said)
            self.assertIn(
                "backup and score (both, sure + AI): ties found: median 43 (41 to 45, spread 4, "
                "5 runs)",
                text,
            )
            [saved] = Path(d).glob("summary-*.json")
            self.assertEqual(spread.load(saved)[0], "abc1234")

    def test_the_cap_keeps_room_for_the_recap(self) -> None:
        # ($5 less the $0.75 recap reserve is $4.25: 1.5 + 1.5 = 3.0, a third would make 4.5)
        with tempfile.TemporaryDirectory() as d:
            made, said, recaps, _ = self.go(d, [1.5])
        self.assertEqual(len(made), 2)
        self.assertEqual(recaps, ["c1"])  # (the last one made is the one kept)
        self.assertIn("Stopped after 2 runs: another would pass $5.", said)

    def test_a_dear_first_run_still_gets_its_recap(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            made, _, recaps, _ = self.go(d, [3.0])
        self.assertEqual((len(made), recaps), (1, ["c0"]))

    def test_rising_costs_stop_before_the_cap(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            made, said, _, _ = self.go(d, [0.5, 1.0, 2.0, 2.0, 2.0])
        spent = [line for line in said if line.startswith("Sample summary")]
        self.assertEqual(len(made), 3)  # (0.5, 1.5, 3.5; a fourth at 2.0 would pass 4.25)
        self.assertIn("3 runs, about $3.50", spent[0])

    def test_a_failed_run_stops_but_the_summary_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            made, said, recaps, code = self.go(d, [0.5], fail_at=3)
            self.assertEqual((len(made), code, recaps), (3, 1, []))
            self.assertIn("run 3 stopped: RuntimeError", said)
            self.assertEqual(len(list(Path(d).glob("summary-*.json"))), 1)

    def test_the_next_deploy_is_compared_with_the_last_summary(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            old = spread.summarize([{"backup and score (both, sure + AI): ties found": 30}] * 5)
            spread.save(Path(d, "summary-20000101-000000.json"), old, commit="old1")
            _, said, _, _ = self.go(d, [0.5])
        self.assertIn(
            "backup and score (both, sure + AI): ties found: 30 → 43 (+13; spreads 0 and 4):"
            " changed",
            said,
        )

    def test_each_version_of_the_session_is_its_own_series(self) -> None:
        # docs/design/session_names.md, section 8: never compared across versions, and the typed
        # series keeps its file names, so the last typed summary before this is still read.
        with tempfile.TemporaryDirectory() as d:
            typed = spread.summarize([{"backup and score (both, sure + AI): ties found": 30}] * 5)
            spread.save(Path(d, "summary-20000101-000000.json"), typed, commit="typed1")
            _, said, _, _ = self.go(d, [0.5], "--version", "no-capitals")
            self.assertFalse(any(line.startswith("Since ") for line in said))
            self.assertIn("no capitals session", next(x for x in said if x.startswith("Sample")))
            [mine] = Path(d).glob("summary-no-capitals-*.json")
            self.assertEqual(spread.version_of(mine), "no-capitals")
            _, said, _, _ = self.go(d, [0.5], "--version", "no-capitals")
            self.assertTrue(any(line.startswith("Since abc1234") for line in said))
            _, said, _, _ = self.go(d, [0.5])
            self.assertTrue(any(line.startswith("Since typed1") for line in said))

    def test_compare_with_another_versions_summary_is_refused_before_any_run(self) -> None:
        calls: list[Any] = []

        async def run(args: Any, **kw: Any) -> int:
            calls.append(kw)
            return 0

        with tempfile.TemporaryDirectory() as d, mock.patch.object(cli, "run", run):
            other = Path(d, "elsewhere.json")
            spread.save(other, spread.summarize([{"x": 1}]), commit="c", version="no-capitals")
            with self.assertRaisesRegex(Exception, "never mixed"):
                asyncio.run(cli.run_many(_args(d, "--compare", str(other)), say=lambda _: None))
        self.assertEqual(calls, [])  # (nothing spent)

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
