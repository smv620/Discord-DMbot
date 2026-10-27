"""The bot's after-session step that makes the report (#2661): nothing runs unless the report
switch, the extractor and the story plan all say so; it works inside the extractor's call limits;
and it passes the session's start as the report's key. A bare bot: only what is read is set."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.bot import REPORT_CALLS, DMBot
from dmbot.config import load_settings
from dmbot.memory.report_make import Made

TABLE = SimpleNamespace(guild_id=1, campaign_id="c", transcript_session_id="s")
BASE = {"DISCORD_TOKEN": "t", "EARS_SHARED_SECRET": "s", "DATABASE_URL": "postgresql://x"}


def bot(*, report: bool = True, extractor: bool = True, plan: bool = True, used: int = 0) -> Any:
    b: Any = DMBot.__new__(DMBot)
    b.settings = SimpleNamespace(
        story_report_on=report,
        story_extractor_on=extractor,
        story_extractor_max_calls=10,
        story_extractor_max_calls_day=100,
    )
    b.extractor_ai = object()
    b.memory = SimpleNamespace(_db=object())
    b.campaigns = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id="c")))
    b.plan_allows = AsyncMock(return_value=plan)
    b.transcripts = SimpleNamespace(
        session=AsyncMock(return_value=SimpleNamespace(started_at=1_700_000_000)),
        extraction=AsyncMock(return_value=(None, used)),
        extractor_calls_since=AsyncMock(return_value=0),
        record_extraction=AsyncMock(),
    )
    return b


class Making(unittest.IsolatedAsyncioTestCase):
    async def run_step(self, b: Any) -> AsyncMock:
        with (
            patch("dmbot.bot.report_make.make", new=AsyncMock(return_value=Made(ran=True))) as make,
            patch("dmbot.bot.GradeStore.of", return_value=object()),
            patch("dmbot.bot.ReportStore.of", return_value=object()),
        ):
            await b.make_report(TABLE)
        return make

    async def test_with_the_switch_off_nothing_is_made(self) -> None:
        make = await self.run_step(bot(report=False))
        make.assert_not_awaited()

    async def test_without_the_extractor_nothing_is_made(self) -> None:
        make = await self.run_step(bot(extractor=False))
        make.assert_not_awaited()

    async def test_a_plan_that_does_not_allow_the_story_is_not_run(self) -> None:
        b = bot(plan=False)
        make = await self.run_step(b)
        make.assert_not_awaited()
        self.assertEqual(b.plan_allows.await_args.kwargs, {"fail_open": False})  # closed

    async def test_it_runs_inside_the_extractors_limit_with_the_sessions_start_as_the_key(
        self,
    ) -> None:
        make = await self.run_step(bot(used=4))
        make.assert_awaited_once()
        call = make.await_args
        assert call is not None
        self.assertEqual(call.kwargs["max_calls"], min(REPORT_CALLS, 10 - 4))
        self.assertEqual(call.args[6], 1_700_000_000)  # the report's key
        self.assertEqual(call.kwargs["session_started_at"], 1_700_000_000)

    async def test_when_the_extractors_limit_is_used_nothing_is_made(self) -> None:
        make = await self.run_step(bot(used=10))
        make.assert_not_awaited()

    async def test_each_call_is_counted_in_the_extractors_tally(self) -> None:
        b = bot()
        make = await self.run_step(b)
        call = make.await_args
        assert call is not None
        await call.kwargs["count_call"]()
        b.transcripts.record_extraction.assert_awaited_once()
        self.assertEqual(b.transcripts.record_extraction.await_args.kwargs["calls"], 1)

    async def test_the_log_line_is_counts_only(self) -> None:
        made = Made(ran=True, differences=2, sorted=1, not_sorted=1, calls=3)
        with (
            self.assertLogs("dmbot.bot", "INFO") as logged,
            patch("dmbot.bot.report_make.make", new=AsyncMock(return_value=made)),
            patch("dmbot.bot.GradeStore.of", return_value=object()),
            patch("dmbot.bot.ReportStore.of", return_value=object()),
        ):
            await bot().make_report(TABLE)
        self.assertEqual(
            [line.split(":", 2)[2] for line in logged.output if "Report:" in line],
            [made.line()],
        )

    async def test_a_session_that_is_not_stored_makes_nothing(self) -> None:
        b = bot()
        b.transcripts.session = AsyncMock(return_value=None)
        make = await self.run_step(b)
        make.assert_not_awaited()


class Setting(unittest.TestCase):
    def test_the_switch_is_off_by_default_and_must_be_a_switch(self) -> None:
        from dmbot.config import ConfigError

        self.assertFalse(load_settings({**BASE}).story_report_on)
        self.assertTrue(load_settings({**BASE, "DMBOT_STORY_REPORT": "1"}).story_report_on)
        with self.assertRaisesRegex(ConfigError, "DMBOT_STORY_REPORT"):
            load_settings({**BASE, "DMBOT_STORY_REPORT": "maybe"})
