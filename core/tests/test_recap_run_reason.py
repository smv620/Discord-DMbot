"""A recap_run that does not finish says why, on stderr, in one line with none of the session's
words (#4543); the sample run, which swallows recap_run's report because it holds the recap, passes
that line on instead of a bare exit code."""

from __future__ import annotations

import contextlib
import inspect
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
                file=sys.stderr,
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


class OtherStderrStillShows(unittest.IsolatedAsyncioTestCase):
    async def test_a_warning_that_is_not_the_tools_reason_is_still_printed(self) -> None:
        async def noisy(argv: list[str]) -> int:
            print("a warning from somewhere", file=sys.stderr)
            print(
                "recap_run: the recap did not finish (state failed): no progress", file=sys.stderr
            )
            return 1

        shown, why = io.StringIO(), []  # type: ignore[var-annotated]
        with contextlib.redirect_stderr(shown):
            await steps.write_recap(1, "c1", 2, run=noisy, why=why)
        self.assertIn("a warning from somewhere", shown.getvalue())
        self.assertNotIn("recap_run:", shown.getvalue())  # (the reason goes to `why`)
        self.assertEqual(len(why), 1)

    async def test_the_reason_is_kept_even_when_the_run_raises(self) -> None:
        async def boom(argv: list[str]) -> int:
            print("recap_run: could not start", file=sys.stderr)
            raise RuntimeError("x")

        why: list[str] = []
        with self.assertRaises(RuntimeError):
            await steps.write_recap(1, "c1", 2, run=boom, why=why)
        self.assertEqual(why, ["recap_run: could not start"])


class TheSampleRunsMessage(unittest.IsolatedAsyncioTestCase):
    """The failure text of the sample run's recap step names the reason, or says there is none."""

    def test_with_and_without_a_reason(self) -> None:
        from dmbot.devtools.sample_run import __main__ as cli

        source = inspect.getsource(cli)
        self.assertIn("recap_run exit {code}){reason}", source)
        self.assertIn("(and no reason was printed)", source)


class TheComparePath(unittest.TestCase):
    def test_a_draft_that_did_not_finish_is_named_on_stderr(self) -> None:
        from dmbot.devtools.recap_run import compare

        source = inspect.getsource(compare.run_compare)
        self.assertIn("did not finish (state", source)
        self.assertIn("file=sys.stderr", source)
