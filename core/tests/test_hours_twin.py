"""The hours twin (#437): a replay that crosses 80%, 90% and the cap shows the warnings, the
grace (once) and the next start in the bot's exact words, Try It at the cap is refused at
start, and each option does what it says. Needs Postgres (a throwaway schema is made)."""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dmbot import hours, plans
from dmbot.devtools import hours_twin as twin
from dmbot.devtools import session_replay as sr
from tests.pg import TEST_URL, DatabaseTest
from tests.test_session_replay import make_case

TABLE_HOURS = plans.load().by_id["table"].hours_per_month  # 18
TRY_IT_HOURS = plans.load().by_id["try-it"].hours_per_month


class Options(unittest.TestCase):
    def test_bad_options_are_refused_in_plain_words(self) -> None:
        for scenario, words in (
            (twin.Scenario("platinum"), "--plan must be one of"),
            (twin.Scenario("table", -1), "0 or more"),
            (twin.Scenario("table", float("nan")), "0 or more"),
            (twin.Scenario("table", float("inf")), "0 or more"),
            (twin.Scenario("try-it", 0, True), "Try It has no payment"),
            (twin.Scenario("table", 0, False, 0), "at least 1"),
        ):
            with self.assertRaisesRegex(twin.TwinError, words):
                scenario.check()

    def test_the_flags_are_read(self) -> None:
        args = sr.parse_args(
            ["--session", "x", "--plan", "guild", "--hours-used", "12.5", "--grace",
             "--session-minutes", "90", "--hours-only"]
        )  # fmt: skip
        self.assertEqual(
            (args.plan, args.hours_used, args.grace, args.session_minutes, args.hours_only),
            ("guild", 12.5, True, 90, True),
        )
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            sr.parse_args(["--session", "x", "--plan", "platinum"])


class Twin(DatabaseTest):
    async def test_a_session_that_crosses_80_90_and_the_cap(self) -> None:
        # 14 of 18 hours is 77.8%: 80% falls at minute 24, 90% at 132, the cap at 240.
        result = await twin.play(self.db, twin.Scenario("table", 14, False, 400))
        cap = TABLE_HOURS * 60
        self.assertEqual([m for m, _ in result.warnings], [24, 132])
        self.assertEqual(
            [t for _, t in result.warnings],
            [
                hours.warning_text(cap - (14 * 60 + 24), 80, "", True, True),
                hours.warning_text(cap - (14 * 60 + 132), 90, "", True, True),
            ],
        )
        self.assertIn("(Hours are DMbot's listening time.)", result.warnings[0][1])
        # The cap finish: the grace starts once (minute 240), a stop warning follows 15
        # minutes before the 2 hours end, and the session stops at the end of the grace.
        started, soon = result.grace
        self.assertEqual(started[0], cap - 14 * 60)
        self.assertTrue(started[1].startswith("⏳ This month's listening hours are used up."))
        self.assertIn("This session can finish", started[1])
        self.assertEqual(soon[1], hours.stop_soon_text("", True, True))
        self.assertEqual(result.stopped_at, cap + hours.GRACE_MINUTES - 14 * 60)
        self.assertEqual(result.stopped_text, hours.stopped_text("", True, True))
        refusal = result.next_start_refusal or ""
        self.assertTrue(refusal.startswith("You've used all your hours this month."))
        self.assertIn("add 10 hours or change your plan", refusal)

    async def test_the_grace_is_given_once_and_the_next_start_is_refused(self) -> None:
        result = await twin.play(self.db, twin.Scenario("table", 17.5, False, 400))
        self.assertEqual(len(result.grace), 2)  # started, and the one stop warning
        self.assertEqual(sum("This session can finish" in t for _, t in result.grace), 1)
        self.assertIsNotNone(result.stopped_at)
        self.assertIsNotNone(result.next_start_refusal)

    async def test_try_it_at_the_cap_is_refused_at_start(self) -> None:
        result = await twin.play(self.db, twin.Scenario("try-it", TRY_IT_HOURS, False, 30))
        self.assertEqual(
            result.start_refusal,
            hours.refusal(
                "out_of_hours",
                is_owner=True,
                can_change_plan=True,
                renews=False,
                extra_hours=0,
            ),
        )
        self.assertIn("change your plan", result.start_refusal or "")
        self.assertNotIn("add 10 hours", result.start_refusal or "")  # Try It buys none
        self.assertEqual((result.warnings, result.grace, result.stopped_at), ([], [], None))
        self.assertIn("start refused", "\n".join(twin.report(result)))

    async def test_hours_used_decides_where_the_session_starts(self) -> None:
        fresh = await twin.play(self.db, twin.Scenario("table", 0, False, 30))
        self.assertEqual((fresh.warnings, fresh.grace, fresh.stopped_at), ([], [], None))
        self.assertIsNone(fresh.next_start_refusal)
        self.assertIn("no warning", "\n".join(twin.report(fresh)))

    async def test_grace_flag_puts_the_plan_in_its_payment_grace(self) -> None:
        result = await twin.play(self.db, twin.Scenario("table", 17, True, 200))
        # The plan still works in the grace, so the hours rules run as on any paid plan, and
        # a plan in its grace is not told it will renew.
        self.assertEqual(len(result.grace), 2)
        self.assertIn("in its payment grace", twin.report(result)[0])
        self.assertNotIn("when your plan renews", result.next_start_refusal or "")

    async def test_session_minutes_is_how_long_it_listens(self) -> None:
        short = await twin.play(self.db, twin.Scenario("two-tables", 0, False, 5))
        self.assertEqual(short.scenario.session_minutes, 5)
        self.assertIsNone(short.stopped_at)

    async def test_the_report_has_the_plan_the_notices_and_the_next_start(self) -> None:
        result = await twin.play(self.db, twin.Scenario("table", 17, False, 300))
        lines = twin.report(result)
        self.assertIn("plan table (18 h a month)", lines[0])
        self.assertIn("enforcement is on for this replay only", lines[0])
        self.assertTrue(any("DM screen" in line for line in lines))
        self.assertIn("  next /dmbot start (to the owner):", lines)

    async def test_nothing_is_left_behind_in_the_database(self) -> None:
        await twin.run(TEST_URL, twin.Scenario("table", 1, False, 3))
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM information_schema.schemata"
                " WHERE schema_name LIKE 'twin\\_%'"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)


class FromTheCommandLine(unittest.IsolatedAsyncioTestCase):
    async def run_cli(self, *argv: str, env: dict[str, str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, env, clear=False),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            folder = make_case(Path(tmp), "case")
            code = await sr.main_async(sr.parse_args(["--session", str(folder), *argv]))
        return code, out.getvalue(), err.getvalue()

    async def test_hours_only_prints_the_hours_replay(self) -> None:
        if not TEST_URL:
            self.skipTest("needs Postgres")
        code, out, _ = await self.run_cli(
            "--plan", "table", "--hours-used", "17.9", "--hours-only", "--session-minutes", "200",
            env={"DATABASE_URL": TEST_URL, "DISCORD_TOKEN": "", "WEB_SITE_URL": ""},
        )  # fmt: skip
        self.assertEqual(code, 0)
        self.assertIn("hours twin: plan table", out)
        self.assertIn("a 200 min session", out)
        self.assertIn("DMbot has stopped listening", out)

    async def test_the_session_length_is_the_default(self) -> None:
        if not TEST_URL:
            self.skipTest("needs Postgres")
        code, out, _ = await self.run_cli(
            "--plan",
            "guild",
            "--hours-only",
            env={"DATABASE_URL": TEST_URL, "DISCORD_TOKEN": "", "WEB_SITE_URL": ""},
        )
        self.assertEqual(code, 0)
        self.assertIn("a 1 min session", out)  # the saved session is under a minute long

    async def test_an_explicit_zero_is_still_checked(self) -> None:
        code, _, err = await self.run_cli(
            "--plan", "table", "--session-minutes", "0", "--hours-only",
            env={"DATABASE_URL": "postgresql://x", "DISCORD_TOKEN": "", "WEB_SITE_URL": ""},
        )  # fmt: skip
        self.assertEqual(code, 2)
        self.assertIn("at least 1", err)

    async def test_a_zero_without_a_plan_is_still_refused(self) -> None:
        code, _, err = await self.run_cli(
            "--hours-used", "0", env={"DISCORD_TOKEN": "", "WEB_SITE_URL": ""}
        )
        self.assertEqual(code, 2)
        self.assertIn("need --plan", err)

    async def test_a_plan_needs_a_database(self) -> None:
        code, _, err = await self.run_cli(
            "--plan",
            "table",
            "--hours-only",
            env={"DATABASE_URL": "", "DISCORD_TOKEN": "", "WEB_SITE_URL": ""},
        )
        self.assertEqual(code, 2)
        self.assertIn("needs DATABASE_URL", err)

    async def test_the_hours_flags_need_a_plan(self) -> None:
        code, _, err = await self.run_cli(
            "--hours-used", "3", env={"DISCORD_TOKEN": "", "WEB_SITE_URL": ""}
        )
        self.assertEqual(code, 2)
        self.assertIn("need --plan", err)

    async def test_a_bad_combination_is_refused(self) -> None:
        if not TEST_URL:
            self.skipTest("needs Postgres")
        code, _, err = await self.run_cli(
            "--plan", "try-it", "--grace", "--hours-only",
            env={"DATABASE_URL": TEST_URL, "DISCORD_TOKEN": "", "WEB_SITE_URL": ""},
        )  # fmt: skip
        self.assertEqual(code, 2)
        self.assertIn("Try It has no payment", err)

    async def test_it_never_runs_in_the_bots_own_container(self) -> None:
        code, _, err = await self.run_cli(
            "--plan", "table", "--hours-only", env={"DISCORD_TOKEN": "x"}
        )
        self.assertEqual(code, 2)
        self.assertIn("bot's own container", err)


if __name__ == "__main__":
    asyncio.run(unittest.main())  # type: ignore[arg-type]
