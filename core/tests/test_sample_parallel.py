"""`sample_run --repeat N --parallel K` (#4551): the runs go K at a time, each its own campaign
"sample N.k"; the money for all of them at once is set aside first; a failed run is counted and
its siblings still summarised. The runs themselves are stand-ins."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from typing import Any, ClassVar
from unittest import mock

from dmbot.devtools import sample_name as sn
from dmbot.devtools.sample_run import __main__ as cli
from dmbot.devtools.sample_run.plan import SampleError


def _args(folder: str, *extra: str) -> Any:
    return cli.parse_args(
        ["--guild", "1", "--dm", "2", "--lore", "l", "--session", "s", "--backup-dir", folder,
         "--commit", "abc1234", "--yes", "--deploy", "31", *extra]
    )  # fmt: skip


class Names(unittest.TestCase):
    def test_a_run_name_is_the_deploy_dot_the_run_and_both_are_sample_names(self) -> None:
        self.assertEqual(sn.name_for(31, 2), "test of the frostmaiden (sample 31.2)")
        self.assertEqual(
            (sn.deploy_of(sn.name_for(31, 2)), sn.run_of(sn.name_for(31, 2))), (sn.Label(31), 2)
        )
        self.assertEqual(
            (sn.deploy_of(sn.name_for(31)), sn.run_of(sn.name_for(31))), (sn.Label(31), None)
        )
        self.assertTrue(sn.is_sample(sn.name_for(31, 5)))
        for good in (sn.name_for(31, 1), sn.name_for(31, 10), sn.name_for(31, 99)):
            self.assertTrue(sn.is_sample(good), good)
        with self.assertRaises(ValueError):
            sn.name_for(31, 100)
        bad_names = (
            "test of the frostmaiden (sample 31.)",
            "test of the frostmaiden (sample 31.2.3)",
            "Test of the frostmaiden (sample 31.2)",
            "test of the frostmaiden (sample .2)",
            "test of the frostmaiden (sample 31.100)",
            "test of the frostmaiden (sample 31.0)",
            "test of the frostmaiden (sample 31.01)",
        )
        for bad in bad_names:
            self.assertFalse(sn.is_sample(bad), bad)
        with self.assertRaises(ValueError):
            sn.name_for(31, 0)

    def test_a_deploy_counts_once_however_many_runs_it_has(self) -> None:
        have = [
            ("a1", sn.name_for(28, 1)), ("a2", sn.name_for(28, 2)),
            ("b1", sn.name_for(29, 1)), ("b2", sn.name_for(29, 2)),
            ("c1", sn.name_for(30)),
            ("d1", sn.name_for(27, 1)),
        ]  # fmt: skip
        plan = sn.plan_keep(have, 31)
        self.assertEqual(set(plan.keep), {"b1", "b2", "c1"})  # (the newest two deploys, all runs)
        self.assertEqual(set(plan.delete), {"a1", "a2", "d1"})
        self.assertEqual(plan.kept_numbers, (sn.Label(29), sn.Label(30), sn.Label(31)))
        self.assertEqual(plan.deleted_numbers, (sn.Label(27), sn.Label(28)))

    def test_a_re_run_deletes_every_run_of_the_same_deploy(self) -> None:
        have = [("x1", sn.name_for(31, 1)), ("x2", sn.name_for(31, 2)), ("y", sn.name_for(30))]
        plan = sn.plan_keep(have, 31)
        self.assertEqual(set(plan.delete), {"x1", "x2"})
        self.assertTrue(plan.replaced)


class Slots(unittest.TestCase):
    """(#4551, Supervisor's ruling: the paid slots are counted before anything is made)"""

    HAVE: ClassVar[list[tuple[str, str]]] = [
        ("a1", sn.name_for(28, 1)), ("a2", sn.name_for(28, 2)),
        ("b1", sn.name_for(29, 1)), ("b2", sn.name_for(29, 2)),
        ("b3", sn.name_for(29, 3)), ("b4", sn.name_for(29, 4)),
        ("c1", sn.name_for(30)),
    ]  # fmt: skip

    def test_short_slots_drop_the_oldest_kept_deploys_runs_and_never_the_newest(self) -> None:
        plan = sn.plan_keep(self.HAVE, 31)  # (28 goes by keep-3; 29 and 30 stay)
        self.assertEqual(set(plan.keep), {"b1", "b2", "b3", "b4", "c1"})
        ids, names, room = sn.slot_drops(self.HAVE, plan, need=5, free=0)
        # 28's two runs already go (keep-3), dropping 29's four makes room for 6: five are needed
        self.assertEqual((len(plan.delete), room), (2, 5))
        self.assertNotIn("c1", ids)  # (the newest kept deploy stays whole)
        self.assertEqual(set(ids), {"b1", "b2", "b3", "b4"})
        self.assertEqual(sn.slot_line(names), "Not kept for lack of slots: 29.1\u201329.4.")

    def test_enough_slots_drop_nothing(self) -> None:
        plan = sn.plan_keep(self.HAVE, 31)
        self.assertEqual(sn.slot_drops(self.HAVE, plan, need=2, free=3), ((), (), 2))

    def test_no_limit_drops_nothing(self) -> None:
        plan = sn.plan_keep(self.HAVE, 31)
        self.assertEqual(sn.slot_drops(self.HAVE, plan, need=5, free=None), ((), (), 5))

    def test_with_nothing_left_to_drop_fewer_runs_or_none(self) -> None:
        only = [("c1", sn.name_for(30))]
        plan = sn.plan_keep(only, 31)
        self.assertEqual(sn.slot_drops(only, plan, need=5, free=2), ((), (), 2))
        self.assertEqual(sn.slot_drops(only, plan, need=5, free=0), ((), (), 0))

    def test_a_deploy_the_guard_refuses_is_left_alone(self) -> None:
        plan = sn.plan_keep(self.HAVE, 31)
        ids, _names, _room = sn.slot_drops(
            self.HAVE, plan, need=5, free=0, deletable=lambda cid: cid != "b2"
        )
        self.assertEqual(ids, ())  # (29 has a run it may not delete: all of 29 stays)


class Fit(unittest.TestCase):
    def test_the_money_for_all_the_runs_at_once_is_set_aside_or_fewer_go(self) -> None:
        self.assertEqual(cli.fit_parallel(10.0, 3.0, 5), 3)
        self.assertEqual(cli.fit_parallel(20.0, 3.0, 5), 5)
        self.assertEqual(cli.fit_parallel(2.0, 3.0, 5), 0)
        with self.assertRaises(SampleError):
            cli.fit_parallel(5.0, 0.0, 2)

    def test_the_default_run_cost_fits_five_runs_at_about_nine_dollars(self) -> None:
        # (#4652: at $3.00 a run, five runs needed $15 and `--parallel 5` ran one at a time)
        budget = 9.0 - cli.RECAP_RESERVE_USD - cli.MAP_RESERVE_USD
        self.assertEqual(cli.fit_parallel(budget, cli.RUN_USD, 5), 5)


class Parallel(unittest.TestCase):
    def go(
        self,
        folder: str,
        *extra: str,
        fail: frozenset[int] = frozenset(),
        cost: float = 0.5,
        slots: int | None = None,
    ) -> tuple[list[int], list[str], int, dict[str, Any]]:
        started: list[int] = []
        said: list[str] = []
        peak = {"now": 0, "peak": 0}
        finished: dict[str, Any] = {}

        async def run(args: Any, **kw: Any) -> int:
            k = kw["run_k"]
            started.append(k)
            peak["now"] += 1
            peak["peak"] = max(peak["peak"], peak["now"])
            await asyncio.sleep(0.01)
            kw["kept"].update(guild=1, campaign=f"c{k}", started=100)
            kw["metrics"].update({"spend: the bot's AI ($)": cost, "questions: total": 30})
            peak["now"] -= 1
            if k in fail:
                raise RuntimeError("stand-in")
            return 0

        async def room(args: Any, deploy: int, say: Any, need: int = 1) -> int:
            finished["room"] = deploy
            finished["need"] = need
            return min(need, slots) if slots is not None else need

        async def finish(args: Any, say: Any, kept: Any, failed: Any, runs: Any, *rest: Any) -> int:
            finished.update(kept=dict(kept), failed=failed, runs=len(runs))
            return 1 if failed else 0

        with (
            mock.patch.object(cli, "run", run),
            mock.patch.object(cli, "_make_room", room),
            mock.patch.object(cli, "_finish", finish),
        ):
            code = asyncio.run(cli.run_many(_args(folder, *extra), say=said.append))
        finished["peak"] = peak["peak"]
        return started, said, code, finished

    def test_runs_go_together_each_its_own_campaign_and_the_room_is_made_once(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            started, _said, code, done = self.go(
                d, "--repeat", "4", "--parallel", "4", "--max-usd", "100"
            )
            self.assertEqual(
                (sorted(started), code, done["room"], done["peak"]),
                ([1, 2, 3, 4], 0, sn.Label(31), 4),
            )
            self.assertEqual(done["kept"]["campaign"], "c1")  # (the recap goes to run 1's campaign)

    def test_only_as_many_go_at_once_as_the_money_set_aside_allows(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            started, said, _code, done = self.go(
                d, "--repeat", "5", "--parallel", "5", "--max-usd", "8", "--run-usd", "2"
            )
            # 8 - 0.75 recap = 7.25 -> 3 at a time; the second batch is sized from what run cost
            self.assertEqual(done["peak"], 3)
            self.assertIn("Only 3 at once fit", "\n".join(said))
            self.assertEqual(len(started), 5)

    def test_not_even_one_fitting_stops_before_anything_is_made(self) -> None:
        with (
            tempfile.TemporaryDirectory() as d,
            self.assertRaisesRegex(SampleError, "not enough for one run"),
        ):
            self.go(d, "--repeat", "3", "--parallel", "3", "--max-usd", "2", "--run-usd", "3")

    def test_a_failed_run_is_counted_and_the_others_are_still_summarised(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            started, said, code, done = self.go(
                d, "--repeat", "6", "--parallel", "3", "--max-usd", "100", fail=frozenset({2})
            )
            self.assertEqual(
                (sorted(started), code, done["runs"]), ([1, 2, 3], 1, 2)
            )  # (the failed run is not a finished one)
            self.assertTrue(done["failed"])  # (no new batch after a failure)
            self.assertIn("1 run(s) stopped", "\n".join(said))
            self.assertTrue(any(line.startswith("[run 2] stopped") for line in said))

    def test_a_later_batch_is_sized_even_when_the_finished_runs_read_no_spend(self) -> None:
        """(the reviewer: a dearest run of $0 must not stop the deploy after the money is spent)"""
        with tempfile.TemporaryDirectory() as d:
            started, _said, code, done = self.go(
                d, "--repeat", "5", "--parallel", "5", "--max-usd", "8", "--run-usd", "2", cost=0.0
            )
            self.assertEqual((len(started), code, done["runs"]), (5, 0, 5))

    def test_runs_the_slots_cannot_hold_are_not_made(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            started, said, code, done = self.go(
                d, "--repeat", "5", "--parallel", "5", "--max-usd", "100", slots=3
            )
            self.assertEqual((sorted(started), code, done["need"]), ([1, 2, 3], 0, 5))
            self.assertIn("Only 3 of 5 runs have a paid campaign slot", "\n".join(said))
