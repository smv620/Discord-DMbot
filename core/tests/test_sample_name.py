"""The sample campaign's names, one for each deploy (#4538): the name, the guard that keeps every
sample tool off anything else, and which of the campaigns are kept. Pure: no database."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from dmbot.devtools import accept_all, graph_key_check, map_key_check, rank_key_check, sample_name
from dmbot.devtools.sample_run import __main__ as cli
from dmbot.devtools.sample_run.plan import SampleError

N = sample_name.name_for


class Names(unittest.TestCase):
    def test_the_name_for_a_deploy(self) -> None:
        self.assertEqual(N(31), "test of the frostmaiden (sample 31)")
        self.assertEqual(sample_name.deploy_of(N(9999)), 9999)

    def test_only_a_whole_number_from_1_to_9999_is_a_deploy(self) -> None:
        for bad in (0, -1, 10000, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                N(bad)

    def test_the_guard_takes_exactly_the_sample_names_and_nothing_like_them(self) -> None:
        for good in (N(1), N(31), N(9999), sample_name.OLD_NAME):
            self.assertTrue(sample_name.is_sample(good), good)
        for bad in (
            "test of the frostmaiden (sample 31) copy",
            "Test of the frostmaiden (sample 31)",
            "test of the frostmaiden (sample 31) ",
            "test of the frostmaiden (sample 0x1f)",
            "test of the frostmaiden (sample 12345)",
            "test of the frostmaiden (sample -1)",
            "test of the frostmaiden (sample)x",
            " test of the frostmaiden (sample 31)",
            "test of the frostmaiden (sample 31)\n",
            "Frostmaiden",
            "",
        ):
            self.assertFalse(sample_name.is_sample(bad), repr(bad))

    def test_the_old_name_has_no_number(self) -> None:
        self.assertIsNone(sample_name.deploy_of(sample_name.OLD_NAME))


class Versions(unittest.TestCase):
    """docs/design/session_names.md, section 8: each version of the sample session (typed, no
    capitals, spoken) is its own series, with its own campaigns."""

    def test_the_typed_name_is_unchanged_and_another_version_says_which(self) -> None:
        self.assertEqual(N(31, 2), "test of the frostmaiden (sample 31.2)")
        self.assertEqual(N(31, 2, "typed"), N(31, 2))
        self.assertEqual(
            N(31, 2, "no-capitals"), "test of the frostmaiden (sample 31.2 no capitals)"
        )
        self.assertEqual(N(31, None, "spoken"), "test of the frostmaiden (sample 31 spoken)")
        with self.assertRaises(ValueError):
            N(31, 2, "shouted")

    def test_a_version_name_is_a_sample_of_its_deploy_and_run(self) -> None:
        name = N(31, 2, "no-capitals")
        self.assertTrue(sample_name.is_sample(name))
        self.assertEqual(
            (sample_name.deploy_of(name), sample_name.run_of(name), sample_name.version_of(name)),
            (31, 2, "no-capitals"),
        )
        self.assertEqual(sample_name.version_of(N(31)), "typed")
        self.assertIsNone(sample_name.version_of(sample_name.OLD_NAME))
        for bad in (
            "test of the frostmaiden (sample 31 No capitals)",
            "test of the frostmaiden (sample 31 no-capitals)",
            "test of the frostmaiden (sample 31  no capitals)",
            "test of the frostmaiden (sample 31 typed)",
        ):
            self.assertFalse(sample_name.is_sample(bad), bad)

    def test_a_version_run_keeps_the_same_deploys_other_version(self) -> None:
        have = [
            ("t1", N(31, 1)), ("t2", N(31, 2)), ("n1", N(31, 1, "no-capitals")), ("old", N(30, 1)),
        ]  # fmt: skip
        kept = sample_name.plan_keep(have, 31, version="no-capitals")
        self.assertEqual(kept.delete, ("n1",))  # (only its own version is re-made)
        self.assertCountEqual(kept.keep, ["t1", "t2", "old"])
        self.assertEqual(kept.kept_numbers, (30, 31))  # (a deploy counts once, whatever versions)
        typed = sample_name.plan_keep(have, 31)
        self.assertCountEqual(typed.delete, ["t1", "t2"])
        self.assertIn("n1", typed.keep)


class Keeping(unittest.TestCase):
    def plan(self, numbers: list[int | None], deploy: int) -> sample_name.Kept:
        have = [
            (
                f"id{n}" if n is not None else f"old{i}",
                N(n) if n is not None else sample_name.OLD_NAME,
            )
            for i, n in enumerate(numbers)
        ]
        return sample_name.plan_keep(have, deploy)

    def test_four_old_ones_and_a_new_one_keeps_the_new_one_and_the_two_newest_others(self) -> None:
        kept = self.plan([27, 28, 29, 30], 31)
        self.assertEqual(kept.kept_numbers, (29, 30, 31))
        self.assertEqual(sorted(kept.delete), ["id27", "id28"])
        self.assertEqual(
            sample_name.counts_line(kept), "Sample campaigns kept: 29, 30, 31. Deleted: 27, 28."
        )

    def test_the_same_deploy_says_replaced_not_deleted(self) -> None:
        kept = self.plan([31, 32, 33], 33)
        self.assertEqual(
            sample_name.counts_line(kept),
            "Sample campaigns kept: 31, 32, 33. Deleted: none. Replaced: 33.",
        )

    def test_several_unnumbered_and_the_same_deploy_together(self) -> None:
        kept = self.plan([None, None, 33, 32], 33)
        self.assertCountEqual(kept.delete, ["old0", "old1", "id33"])
        self.assertEqual(kept.kept_numbers, (32, 33))

    def test_the_issues_example_30_31_32_then_33(self) -> None:
        kept = self.plan([30, 31, 32], 33)
        self.assertEqual(kept.kept_numbers, (31, 32, 33))
        self.assertEqual(kept.delete, ("id30",))
        self.assertEqual(
            sample_name.counts_line(kept), "Sample campaigns kept: 31, 32, 33. Deleted: 30."
        )

    def test_an_unnumbered_one_always_goes_and_counts_as_oldest(self) -> None:
        kept = self.plan([None, 30], 31)
        self.assertEqual(kept.delete, ("old0",))
        self.assertEqual(kept.kept_numbers, (30, 31))
        self.assertIn("1 unnumbered", sample_name.counts_line(kept))

    def test_the_same_deploy_again_replaces_it_and_never_leaves_two(self) -> None:
        kept = self.plan([30, 31], 31)
        self.assertEqual(kept.delete, ("id31",))
        self.assertEqual(kept.kept_numbers, (30, 31))
        self.assertEqual(kept.deleted_numbers, ())
        self.assertTrue(kept.replaced)

    def test_a_protected_candidate_is_left_alone_and_counted_not_reported_deleted(self) -> None:
        have = [(f"id{n}", N(n)) for n in (29, 30, 31, 32)]
        kept = sample_name.plan_keep(have, 33, protected={"id29"})
        self.assertEqual(kept.delete, ("id30",))
        self.assertEqual(kept.not_deleted, 1)
        self.assertEqual(
            sample_name.counts_line(kept),
            "Sample campaigns kept: 31, 32, 33. Deleted: 30. Not deleted, not the tool's: 1.",
        )

    def test_nothing_before_it_is_nothing_deleted(self) -> None:
        kept = self.plan([], 1)
        self.assertEqual(kept.delete, ())
        self.assertEqual(sample_name.counts_line(kept), "Sample campaigns kept: 1. Deleted: none.")

    def test_a_lower_number_than_the_kept_ones_is_still_kept_if_there_is_room(self) -> None:
        kept = self.plan([40], 5)
        self.assertEqual(kept.kept_numbers, (5, 40))
        self.assertEqual(kept.delete, ())


class TheCommandLine(unittest.TestCase):
    def test_a_run_without_a_deploy_says_which(self) -> None:
        with self.assertRaises(SampleError) as err:
            cli.deploy_number(None)
        self.assertEqual(str(err.exception), "Say which deploy this is: --deploy N")

    def test_a_deploy_out_of_range_is_refused(self) -> None:
        for bad in (0, -3, 10000):
            with self.assertRaises(SampleError):
                cli.deploy_number(bad)
        self.assertEqual(cli.deploy_number(33), 33)

    def test_keep_old_is_gone_and_deploy_is_an_option(self) -> None:
        args = cli.parse_args(["--guild", "1", "--dm", "2", "--deploy", "33"])
        self.assertEqual(args.deploy, 33)
        with self.assertRaises(SystemExit):
            cli.parse_args(["--guild", "1", "--dm", "2", "--keep-old"])


class EveryGuard(unittest.TestCase):
    """Each tool that names the sample accepts a numbered one and refuses a look-alike."""

    def test_the_constants_the_tools_keep_are_the_old_name(self) -> None:
        for tool in (graph_key_check, map_key_check, accept_all):
            self.assertEqual(tool.SAMPLE_CAMPAIGN, sample_name.OLD_NAME)

    def run_tool(self, tool: Any, name: str) -> str:
        """What the tool says on stderr for a backup of a campaign with this name (the key and
        backup are empty: only the name guard is under test)."""
        with tempfile.TemporaryDirectory() as folder:
            backup = Path(folder) / "backup.json"
            backup.write_text(json.dumps({"campaign": {"name": name}}))
            key = Path(folder) / "key.json"
            key.write_text("{}")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                tool.main([str(backup), "--key", str(key)])
        return err.getvalue()

    def test_the_key_checks_take_a_numbered_name_and_refuse_a_look_alike(self) -> None:
        refusal = "frostmaiden (sample N)"
        for tool in (graph_key_check, rank_key_check, map_key_check):
            with self.subTest(tool=tool.__name__):
                self.assertIn(refusal, self.run_tool(tool, f"{N(31)} copy"))
                self.assertNotIn(refusal, self.run_tool(tool, N(31)))
                self.assertNotIn(refusal, self.run_tool(tool, sample_name.OLD_NAME))


class WaitingBeforeDeleting(unittest.IsolatedAsyncioTestCase):
    """sample_run waits for an old sample's background work before deleting it (#4542), so its AI
    charges still find the campaign. The check is stood in for: no database."""

    async def settle(
        self, counts: list[int], *, limit_s: float = 600.0
    ) -> tuple[bool, list[float]]:
        from unittest.mock import AsyncMock, patch

        from dmbot.devtools.sample_run import steps

        slept: list[float] = []
        now = [0.0]

        async def sleep(seconds: float) -> None:
            slept.append(seconds)
            now[0] += seconds

        with patch.object(steps, "busy_count", AsyncMock(side_effect=counts)):
            done = await steps.wait_settled(
                None,  # type: ignore[arg-type]
                1,
                "c" * 32,
                sleep=sleep,
                clock=lambda: now[0],
                limit_s=limit_s,
                poll_s=5.0,
            )
        return done, slept

    async def test_it_waits_until_the_work_is_done(self) -> None:
        done, slept = await self.settle([2, 1, 0])
        self.assertTrue(done)
        self.assertEqual(slept, [5.0, 5.0])

    async def test_nothing_running_is_no_wait(self) -> None:
        done, slept = await self.settle([0])
        self.assertTrue(done)
        self.assertEqual(slept, [])

    async def test_work_that_never_ends_is_given_up_on_at_the_limit(self) -> None:
        done, slept = await self.settle([1] * 100, limit_s=20.0)
        self.assertFalse(done)
        self.assertLessEqual(sum(slept), 25.0)
