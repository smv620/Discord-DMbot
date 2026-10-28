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


class FromTheWebsite(unittest.IsolatedAsyncioTestCase):
    """The same step for a session the website named by when it began (#2805): the word it gives
    back is what the request row carries."""

    async def make(self, b: Any) -> tuple[str, AsyncMock]:
        b.transcripts.session_at = AsyncMock(
            return_value=SimpleNamespace(id="s", started_at=1_700_000_000)
        )
        with (
            patch("dmbot.bot.report_make.make", new=AsyncMock(return_value=Made(ran=True))) as m,
            patch("dmbot.bot.GradeStore.of", return_value=object()),
            patch("dmbot.bot.ReportStore.of", return_value=object()),
        ):
            return await b.report_for_session_at(1, "c", 1_700_000_000), m

    async def test_a_report_that_is_made_gives_no_word(self) -> None:
        word, make = await self.make(bot(used=4))
        self.assertEqual(word, "")
        call = make.await_args
        assert call is not None
        self.assertEqual(call.kwargs["max_calls"], min(REPORT_CALLS, 10 - 4))  # the same limits

    async def test_each_reason_not_to_is_one_short_word(self) -> None:
        for kwargs, want in (
            ({"report": False}, "unavailable"),
            ({"extractor": False}, "unavailable"),
            ({"plan": False}, "plan"),
            ({"used": 10}, "limit"),
        ):
            with self.subTest(kwargs=kwargs):
                word, make = await self.make(bot(**kwargs))
                self.assertEqual(word, want)
                make.assert_not_awaited()

    async def test_a_report_that_never_started_or_failed_inside_is_not_done(self) -> None:
        for made, want in (
            (Made(ran=False), "busy"),
            (Made(ran=True, failed="failed"), "failed"),
            (Made(ran=True, failed="out_of_funds"), "unavailable"),
            (Made(ran=True), ""),
        ):
            with self.subTest(want=want):
                b = bot()
                b.transcripts.session_at = AsyncMock(
                    return_value=SimpleNamespace(id="s", started_at=1_700_000_000)
                )
                with (
                    patch("dmbot.bot.report_make.make", new=AsyncMock(return_value=made)),
                    patch("dmbot.bot.GradeStore.of", return_value=object()),
                    patch("dmbot.bot.ReportStore.of", return_value=object()),
                ):
                    self.assertEqual(await b.report_for_session_at(1, "c", 1_700_000_000), want)

    async def test_a_session_the_campaign_does_not_have_is_failed(self) -> None:
        b = bot()
        b.transcripts.session_at = AsyncMock(return_value=None)
        self.assertEqual(await b.report_for_session_at(1, "c", 5), "failed")

    async def test_a_campaign_that_is_gone_is_failed(self) -> None:
        b = bot()
        b.campaigns = SimpleNamespace(get=AsyncMock(return_value=None))
        word, make = await self.make(b)
        self.assertEqual(word, "failed")
        make.assert_not_awaited()


class TheSweep(unittest.IsolatedAsyncioTestCase):
    """The poll loop does not wait for a report (#2805): it is a background task, one at a time
    for a server."""

    def bare(self, requests: list[Any]) -> Any:
        b: Any = DMBot.__new__(DMBot)
        self.queue: dict[int, list[Any]] = {1: list(requests), 2: []}

        async def claim(guild_id: int) -> Any:
            return self.queue[guild_id].pop(0) if self.queue[guild_id] else None

        b.report_requests = SimpleNamespace(claim_next=claim)
        b._report_tasks = {}  # declared in DMBot.__init__
        b._closing = False
        b._asking = set()
        return b

    async def test_the_sweep_returns_while_a_report_is_still_being_made(self) -> None:
        import asyncio
        from unittest.mock import PropertyMock

        started = asyncio.Event()
        release = asyncio.Event()
        req = SimpleNamespace(campaign_id="c")

        async def slow(self: Any, store: Any, guild_id: int, r: Any) -> None:
            started.set()
            await release.wait()

        b = self.bare([req, req])
        guilds = PropertyMock(return_value=[SimpleNamespace(id=1), SimpleNamespace(id=2)])
        with (
            patch.object(DMBot, "guilds", guilds),
            patch.object(DMBot, "_run_report_request", slow),
        ):
            await asyncio.wait_for(b.run_web_report_requests(), timeout=1)  # returns at once
            await started.wait()
            await asyncio.wait_for(b.run_web_report_requests(), timeout=1)  # and again
            self.assertEqual(len(b._report_tasks), 1)  # one at a time for the server
            self.assertEqual(len(self.queue[1]), 1)  # the second request was not even claimed
            release.set()
            await asyncio.gather(*b._report_tasks.values())
        await asyncio.sleep(0)
        self.assertEqual(b._report_tasks, {})  # forgotten when done

    async def test_while_closing_no_report_is_started(self) -> None:
        from unittest.mock import PropertyMock

        b = self.bare([SimpleNamespace(campaign_id="c")])
        b._closing = True
        ran: list[int] = []

        async def never(self: Any, store: Any, guild_id: int, r: Any) -> None:
            ran.append(guild_id)

        guilds = PropertyMock(return_value=[SimpleNamespace(id=1)])
        with (
            patch.object(DMBot, "guilds", guilds),
            patch.object(DMBot, "_run_report_request", never),
        ):
            await b.run_web_report_requests()
        import asyncio

        await asyncio.sleep(0)  # a started task would run now, so `ran` below means something
        # Nothing kept; the row stays claimed until RETAKE_AFTER_S.
        self.assertEqual(b._report_tasks, {})
        self.assertEqual(ran, [])

    async def test_without_a_store_nothing_happens(self) -> None:
        b: Any = DMBot.__new__(DMBot)
        b.report_requests = None
        await b.run_web_report_requests()


class Setting(unittest.TestCase):
    def test_the_switch_is_off_by_default_and_must_be_a_switch(self) -> None:
        from dmbot.config import ConfigError

        self.assertFalse(load_settings({**BASE}).story_report_on)
        self.assertTrue(load_settings({**BASE, "DMBOT_STORY_REPORT": "1"}).story_report_on)
        with self.assertRaisesRegex(ConfigError, "DMBOT_STORY_REPORT"):
            load_settings({**BASE, "DMBOT_STORY_REPORT": "maybe"})
