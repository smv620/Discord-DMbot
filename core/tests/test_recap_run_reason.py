"""A recap_run that does not finish says why, on stderr, in one line with none of the session's
words (#4543); the sample run, which swallows recap_run's report because it holds the recap, passes
that line on instead of a bare exit code."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.devtools.recap_run import __main__ as rr
from dmbot.devtools.sample_run import steps


def outcome(state: str, *, stopped: str = "", last_error: str = "") -> Any:
    return SimpleNamespace(job=SimpleNamespace(state=state, last_error=last_error), stopped=stopped)


class TheReason(unittest.TestCase):
    def exit(self, out: Any) -> tuple[int, str, str]:
        err, std = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(std):
            code = rr.exit_code(out)
        return code, err.getvalue(), std.getvalue()

    def test_a_ready_recap_exits_0_and_says_nothing(self) -> None:
        self.assertEqual(self.exit(outcome("ready")), (0, "", ""))

    def test_a_failed_one_exits_1_with_the_state_and_the_stage_reason_on_stderr(self) -> None:
        code, err, _ = self.exit(outcome("failed", stopped="a call failed (overloaded)"))
        self.assertEqual(code, 1)
        self.assertEqual(
            err,
            "recap_run: the recap did not finish (state failed): a call failed (overloaded)\n",
        )

    def test_without_a_stage_reason_the_jobs_last_error_is_said_then_a_plain_none(self) -> None:
        self.assertIn("allowance", self.exit(outcome("failed", last_error="the allowance"))[1])
        self.assertIn("no reason recorded", self.exit(outcome("prose"))[1])

    def test_the_line_is_one_line_and_short(self) -> None:
        line = rr.why_stopped(outcome("failed", stopped="a\nb  " + "x" * 400))
        self.assertNotIn("\n", line)
        self.assertLess(len(line), 260)


class PassedOn(unittest.IsolatedAsyncioTestCase):
    async def test_the_sample_run_gets_the_reason_but_never_the_recaps_words(self) -> None:
        async def fake(argv: list[str]) -> int:
            print("THE RECAP'S OWN WORDS")
            print(
                "recap_run: the recap did not finish (state failed): no progress",
                file=__import__("sys").stderr,
            )
            return 1

        why: list[str] = []
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = await steps.write_recap(1, "c1", 2, run=fake, why=why)
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue(), "")  # (the report is still swallowed)
        self.assertEqual(why, ["recap_run: the recap did not finish (state failed): no progress"])
        self.assertNotIn("WORDS", " ".join(why))

    async def test_a_run_that_says_nothing_leaves_the_list_empty(self) -> None:
        async def quiet(argv: list[str]) -> int:
            return 1

        why: list[str] = []
        self.assertEqual(await steps.write_recap(1, "c1", 2, run=quiet, why=why), 1)
        self.assertEqual(why, [])
