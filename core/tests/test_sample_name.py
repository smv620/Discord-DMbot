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


def L(*numbers: int) -> tuple[sample_name.Label, ...]:
    """Plain deploy numbers as the labels `kept_numbers` holds."""
    return tuple(sample_name.Label(n) for n in numbers)


class Names(unittest.TestCase):
    def test_the_name_for_a_deploy(self) -> None:
        self.assertEqual(N(31), "test of the frostmaiden (sample 31)")
        self.assertEqual(sample_name.deploy_of(N(9999)), sample_name.Label(9999))

    def test_only_a_whole_number_from_1_to_9999_is_a_deploy(self) -> None:
        for bad in (0, -1, 10000, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                N(bad)

    def test_the_guard_takes_exactly_the_sample_names_and_nothing_like_them(self) -> None:
        for good in (
            N(1),
            N(31),
            N(9999),
            sample_name.OLD_NAME,
            sample_name.name_for(sample_name.Label(40, "b")),
            sample_name.name_for(sample_name.Label(40, "b"), 2),
        ):
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
            (sample_name.Label(31), 2, "no-capitals"),
        )
        lettered = N(sample_name.parse_label("41b"), 2, "no-capitals")
        self.assertEqual(lettered, "test of the frostmaiden (sample 41b.2 no capitals)")
        self.assertEqual(sample_name.deploy_of(lettered), sample_name.Label(41, "b"))
        self.assertEqual(sample_name.version_of(lettered), "no-capitals")
        self.assertEqual(sample_name.version_of(N(31)), "typed")
        self.assertIsNone(sample_name.version_of(sample_name.OLD_NAME))
        for bad in (
            "test of the frostmaiden (sample 31 No capitals)",
            "test of the frostmaiden (sample 31 no-capitals)",
            "test of the frostmaiden (sample 31  no capitals)",
            "test of the frostmaiden (sample 31 typed)",
        ):
            self.assertFalse(sample_name.is_sample(bad), bad)

    def test_the_respelled_version_is_its_own_series_too(self) -> None:
        # (#5133: dev only, until the spoken recording exists)
        name = N(31, 2, "respelled")
        self.assertEqual(name, "test of the frostmaiden (sample 31.2 respelled)")
        self.assertTrue(sample_name.is_sample(name))
        self.assertEqual(sample_name.version_of(name), "respelled")

    def test_a_version_run_keeps_the_same_deploys_other_version(self) -> None:
        have = [
            ("t1", N(31, 1)), ("t2", N(31, 2)), ("n1", N(31, 1, "no-capitals")), ("old", N(30, 1)),
        ]  # fmt: skip
        kept = sample_name.plan_keep(have, 31, version="no-capitals")
        self.assertEqual(kept.delete, ("n1",))  # (only its own version is re-made)
        self.assertCountEqual(kept.keep, ["t1", "t2", "old"])
        # (a deploy counts once, whatever its versions)
        self.assertEqual(kept.kept_numbers, (sample_name.Label(30), sample_name.Label(31)))
        typed = sample_name.plan_keep(have, 31)
        self.assertCountEqual(typed.delete, ["t1", "t2"])
        self.assertIn("n1", typed.keep)

    def test_short_of_slots_an_older_deploys_rerun_never_drops_its_other_version(self) -> None:
        # (the reviewer's case: typed 30 again with 3 runs and no free slot)
        have = [("t33", N(33)), ("n30", N(30, None, "no-capitals")), ("t30", N(30))]
        kept = sample_name.plan_keep(have, 30)
        extra, _names, room = sample_name.slot_drops(have, kept, need=3, free=0)
        self.assertNotIn("n30", extra)
        self.assertEqual(extra, ())  # (33 is the newest other deploy: never touched)
        self.assertEqual(room, 1)  # (only the typed 30 it replaces frees a slot)

    def test_short_of_slots_the_newest_other_deploy_is_still_never_dropped(self) -> None:
        have = [("t34", N(34)), ("n35", N(35, None, "no-capitals"))]
        kept = sample_name.plan_keep(have, 35)
        extra, _names, room = sample_name.slot_drops(have, kept, need=2, free=0)
        self.assertEqual((extra, room), ((), 0))


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
        self.assertEqual(kept.kept_numbers, L(29, 30, 31))
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
        self.assertEqual(kept.kept_numbers, L(32, 33))

    def test_the_issues_example_30_31_32_then_33(self) -> None:
        kept = self.plan([30, 31, 32], 33)
        self.assertEqual(kept.kept_numbers, L(31, 32, 33))
        self.assertEqual(kept.delete, ("id30",))
        self.assertEqual(
            sample_name.counts_line(kept), "Sample campaigns kept: 31, 32, 33. Deleted: 30."
        )

    def test_an_unnumbered_one_always_goes_and_counts_as_oldest(self) -> None:
        kept = self.plan([None, 30], 31)
        self.assertEqual(kept.delete, ("old0",))
        self.assertEqual(kept.kept_numbers, L(30, 31))
        self.assertIn("1 unnumbered", sample_name.counts_line(kept))

    def test_the_same_deploy_again_replaces_it_and_never_leaves_two(self) -> None:
        kept = self.plan([30, 31], 31)
        self.assertEqual(kept.delete, ("id31",))
        self.assertEqual(kept.kept_numbers, L(30, 31))
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
        self.assertEqual(kept.kept_numbers, L(5, 40))
        self.assertEqual(kept.delete, ())


class TheCommandLine(unittest.TestCase):
    def test_a_run_without_a_deploy_says_which(self) -> None:
        with self.assertRaises(SampleError) as err:
            cli.deploy_label(None)
        self.assertEqual(str(err.exception), "Say which deploy this is: --deploy N")

    def test_a_deploy_that_is_not_a_label_is_refused(self) -> None:
        for bad in ("0", "-3", "10000", "40bb"):
            with self.assertRaises(SampleError):
                cli.deploy_label(bad)
        self.assertEqual(cli.deploy_label("33"), sample_name.Label(33))
        self.assertEqual(cli.deploy_label("40b"), sample_name.Label(40, "b"))

    def test_keep_old_is_gone_and_deploy_is_an_option(self) -> None:
        args = cli.parse_args(["--guild", "1", "--dm", "2", "--deploy", "33"])
        self.assertEqual(args.deploy, "33")
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


class TheLabels(unittest.TestCase):
    """A deploy's label is its number with an optional lowercase letter (#5057): 40 and 40b are
    two deploys, so a re-run of 40b never touches 40's campaigns."""

    def test_parse_accepts_a_number_and_a_number_with_one_lowercase_letter(self) -> None:
        self.assertEqual(sample_name.parse_label("40"), sample_name.Label(40))
        self.assertEqual(sample_name.parse_label("40b"), sample_name.Label(40, "b"))
        self.assertEqual(sample_name.parse_label("9999z"), sample_name.Label(9999, "z"))
        self.assertEqual(sample_name.parse_label("1"), sample_name.Label(1))

    def test_parse_refuses_anything_else(self) -> None:
        for bad in (
            "40bb",
            "040",
            "0040",
            "b40",
            "40-b",
            "40B",
            " 40",
            "40 ",
            "0",
            "0b",
            "10000",
            "",
            "4 0",
            "40.1",
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sample_name.parse_label(bad)

    def test_names_use_the_label_and_the_run(self) -> None:
        self.assertEqual(
            sample_name.name_for(sample_name.Label(40, "b")),
            "test of the frostmaiden (sample 40b)",
        )
        self.assertEqual(
            sample_name.name_for(sample_name.Label(40, "b"), 2),
            "test of the frostmaiden (sample 40b.2)",
        )
        self.assertEqual(sample_name.name_for(31), "test of the frostmaiden (sample 31)")

    def test_deploy_of_reads_the_label_back(self) -> None:
        for label in (sample_name.Label(40), sample_name.Label(40, "b")):
            self.assertEqual(sample_name.deploy_of(sample_name.name_for(label)), label)
            self.assertEqual(sample_name.deploy_of(sample_name.name_for(label, 2)), label)
        for name in (
            "test of the frostmaiden (sample)",
            "test of the frostmaiden (sample 40B)",
            "test of the frostmaiden (sample 40bb)",
            "my game",
        ):
            with self.subTest(name=name):
                self.assertIsNone(sample_name.deploy_of(name))

    def test_a_new_40b_keeps_41_and_42_and_drops_40_and_40a_as_the_oldest(self) -> None:
        existing = [
            ("c40", "test of the frostmaiden (sample 40)"),
            ("c40a", "test of the frostmaiden (sample 40a)"),
            ("c41", "test of the frostmaiden (sample 41)"),
            ("c42", "test of the frostmaiden (sample 42)"),
        ]
        kept = sample_name.plan_keep(existing, sample_name.Label(40, "b"))
        self.assertEqual(set(kept.keep), {"c41", "c42"})
        self.assertEqual(set(kept.delete), {"c40", "c40a"})
        self.assertEqual(
            kept.kept_numbers,
            (sample_name.Label(40, "b"), sample_name.Label(41), sample_name.Label(42)),
        )

    def test_labels_sort_by_number_then_letter(self) -> None:
        labels = [
            sample_name.Label(41),
            sample_name.Label(40, "b"),
            sample_name.Label(40),
            sample_name.Label(40, "a"),
        ]
        self.assertEqual(
            sorted(labels),
            [
                sample_name.Label(40),
                sample_name.Label(40, "a"),
                sample_name.Label(40, "b"),
                sample_name.Label(41),
            ],
        )

    def test_plan_keep_for_40b_leaves_40_and_40a_alone(self) -> None:
        # The other labels are 40a and 41, so the keep rule drops nothing: only 40b's own go.
        existing = [
            ("c40b", "test of the frostmaiden (sample 40b)"),
            ("c40b2", "test of the frostmaiden (sample 40b.2)"),
            ("c40a", "test of the frostmaiden (sample 40a)"),
            ("c41", "test of the frostmaiden (sample 41)"),
        ]
        kept = sample_name.plan_keep(existing, sample_name.Label(40, "b"))
        self.assertEqual(set(kept.delete), {"c40b", "c40b2"})
        self.assertEqual(set(kept.keep), {"c40a", "c41"})
        self.assertTrue(kept.replaced)

    def test_counts_line_prints_labels(self) -> None:
        kept = sample_name.Kept(
            keep=(),
            delete=(),
            kept_numbers=(sample_name.Label(40), sample_name.Label(40, "b")),
            deleted_numbers=(sample_name.Label(39),),
            deleted_old=0,
            deploy=sample_name.Label(40, "b"),
            replaced=True,
        )
        self.assertEqual(
            sample_name.counts_line(kept),
            "Sample campaigns kept: 40, 40b. Deleted: 39. Replaced: 40b.",
        )
