"""The monthly allowance of recaps and narrated recaps per campaign (#1428): counted from the job
rows by calendar month, Try again counted, a job that never got written not counted, free access
and grants unlimited, a text job or the narration refused past the limit with a plain line, and a
count that can't be read spending nothing."""

from __future__ import annotations

import datetime as dt
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot.config import ConfigError, load_settings
from dmbot.recaps import allowance as al
from dmbot.recaps import pipeline as pl
from dmbot.recaps.allowance import Usage
from dmbot.recaps.audio.worker import AudioInputs
from dmbot.recaps.store import Job
from tests import recap_fixtures as fx
from tests import test_recap_audio_step as audio_tests
from tests import test_recap_jobs as text_tests


def at(year: int, month: int, day: int, hour: int = 12) -> int:
    return int(dt.datetime(year, month, day, hour, tzinfo=dt.timezone.utc).timestamp())  # noqa: UP017


class TheCalendar(unittest.TestCase):
    def test_a_month_runs_from_its_first_second_to_the_next(self) -> None:
        start, end = al.month_bounds(at(2026, 10, 16))
        self.assertEqual((start, end), (at(2026, 10, 1, 0), at(2026, 11, 1, 0)))
        self.assertEqual(al.month_bounds(at(2026, 12, 31, 23))[1], at(2027, 1, 1, 0))
        self.assertEqual(al.month_bounds(at(2026, 2, 28))[1], at(2026, 3, 1, 0))

    def test_the_lines_name_the_day_it_starts_again(self) -> None:
        now = at(2026, 12, 5)
        self.assertEqual(al.next_month_text(now), "1 January")
        self.assertEqual(
            al.text_refusal(at(2026, 10, 5)),
            "No recap this time: this campaign has used all of its recaps for this month. "
            "You can make recaps again on 1 November.",
        )
        self.assertIn("This recap is text only", al.audio_refusal(now))
        self.assertTrue(al.audio_refusal(now).startswith(al.AUDIO_PREFIX))


class TheSettings(unittest.TestCase):
    def test_defaults_and_limits(self) -> None:
        base = {
            "DISCORD_TOKEN": "x",
            "EARS_SHARED_SECRET": "y",
            "DATABASE_URL": "postgresql://u@h/d",
        }
        s = load_settings({**base})
        self.assertEqual((s.recaps_per_month, s.recap_audio_per_month), (10, 8))
        s = load_settings(
            {**base, "DMBOT_RECAPS_PER_MONTH": "3", "DMBOT_RECAP_AUDIO_PER_MONTH": "1"}
        )
        self.assertEqual((s.recaps_per_month, s.recap_audio_per_month), (3, 1))
        for key in ("DMBOT_RECAPS_PER_MONTH", "DMBOT_RECAP_AUDIO_PER_MONTH"):
            for bad in ("0", "1001", "many"):
                with self.assertRaises(ConfigError):
                    load_settings({**base, key: bad})


class TheNarratedCount(text_tests.Base):
    async def test_a_retried_narration_counts_each_time(self) -> None:
        october = at(2026, 10, 10)
        self.clock.now = float(october)
        await self.store.enqueue(1, self.one.id, 1, dm_user_id=fx.DM)
        await self.store.update(1, self.one.id, 1, state="ready")
        await self.store.set_audio(1, self.one.id, 1, audio="ready")
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET tries = 2 WHERE guild_id = 1")
        self.assertEqual(await self.store.used_in_month(1, self.one.id, october), Usage(3, 3))


class TheWebSettings(unittest.TestCase):
    def test_the_same_two_settings_with_the_same_limits(self) -> None:
        from dmbot.web.settings import load_web_settings

        needed = {
            "DATABASE_URL": "postgresql://u@h/d",
            "DISCORD_CLIENT_ID": "1",
            "DISCORD_CLIENT_SECRET": "s",
            "WEB_SECRET_KEY": "k" * 40,
            "WEB_SITE_URL": "https://dmbot.example",
            "WEB_API_URL": "https://api.dmbot.example",
        }
        got = load_web_settings({**needed, "DMBOT_RECAPS_PER_MONTH": "4"})
        self.assertEqual((got.recaps_per_month, got.recap_audio_per_month), (4, 8))
        self.assertFalse(got.recaps_on)  # off unless the bot's switch is on (#2225)
        for on in ("1", "true", "ON"):
            self.assertTrue(load_web_settings({**needed, "DMBOT_RECAPS": on}).recaps_on)
        self.assertFalse(load_web_settings({**needed, "DMBOT_RECAPS": "0"}).recaps_on)
        for bad in ("0", "1001", "x"):
            with self.assertRaises(ConfigError):
                load_web_settings({**needed, "DMBOT_RECAP_AUDIO_PER_MONTH": bad})


class Counting(text_tests.Base):
    async def make(self, campaign: str, session_at: int, *, created: int, **fields: Any) -> None:
        self.clock.now = float(created)
        await self.store.enqueue(1, campaign, session_at, dm_user_id=fx.DM)
        audio = fields.pop("audio", None)
        tries = fields.pop("tries", None)
        if tries:  # only the website's Try again door moves this, so the test sets it directly
            async with self.db.guild(1) as conn:
                await conn.execute(
                    "UPDATE recap_jobs SET tries = %s WHERE guild_id = 1 AND campaign_id = %s"
                    " AND session_at = %s",
                    (tries, campaign, session_at),
                )
        if fields:
            await self.store.update(1, campaign, session_at, **fields)
        if audio:
            await self.store.set_audio(1, campaign, session_at, audio=audio)

    async def test_only_this_campaign_and_this_month_count(self) -> None:
        october = at(2026, 10, 10)
        await self.make(self.one.id, 1, created=october, state="ready")
        await self.make(self.one.id, 2, created=october, state="ready", audio="ready")
        await self.make(self.one.id, 3, created=at(2026, 9, 30), state="ready", audio="ready")
        await self.make(self.one.id, 4, created=at(2026, 11, 1, 0), state="ready")
        await self.make(self.two.id, 1, created=october, state="ready", audio="ready")
        used = await self.store.used_in_month(1, self.one.id, october)
        self.assertEqual(used, Usage(recaps=2, narrated=1))
        self.assertEqual(await self.store.used_in_month(1, self.two.id, october), Usage(1, 1))
        self.assertEqual(await self.store.used_in_month(2, self.one.id, october), Usage(0, 0))

    async def test_try_again_counts_again_and_a_failed_job_does_not(self) -> None:
        october = at(2026, 10, 10)
        await self.make(self.one.id, 1, created=october, state="ready", tries=2)
        await self.make(self.one.id, 2, created=october, state="failed")
        await self.make(self.one.id, 3, created=october)  # still waiting to start
        self.assertEqual(await self.store.used_in_month(1, self.one.id, october), Usage(3, 0))


class TheNotice(unittest.TestCase):
    def test_a_refusal_offers_no_try_again(self) -> None:
        self.assertTrue(al.is_refusal(al.text_refusal(at(2026, 10, 1))))
        self.assertTrue(al.is_refusal(al.audio_refusal(at(2026, 10, 1))))
        self.assertFalse(al.is_refusal("The model was busy."))


class FakeMeter:
    def __init__(self, kind: str) -> None:
        self.kind = kind

    async def access(self, gid: int, user: int, now: int) -> Any:
        return SimpleNamespace(kind=self.kind)


class TheBotsCheck(text_tests.Base):
    """`Bot._recap_allowance` on its own, with only what it reads."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.bot import DMBot

        self.bot = cast(Any, DMBot.__new__(DMBot))
        self.posted: list[tuple[int, str]] = []

        async def post(channel: int, text: str) -> None:
            self.posted.append((channel, text))

        self.bot.post = post
        self.bot.recaps = self.store
        self.bot.campaigns = self.campaigns
        self.bot.settings = SimpleNamespace(recaps_per_month=2, recap_audio_per_month=1)
        self.bot.meter = None
        self.now = at(2026, 10, 16)
        self.clock.now = float(self.now)
        for n in (1, 2):
            await self.store.enqueue(1, self.one.id, n, dm_user_id=fx.DM)
            await self.store.update(1, self.one.id, n, state="ready")
            await self.store.set_audio(1, self.one.id, n, audio="ready")
        self.job = cast(Job, await self.store.get(1, self.one.id, 1))

    async def test_under_the_limit_nothing_is_said(self) -> None:
        self.bot.settings.recaps_per_month = 3
        self.bot.settings.recap_audio_per_month = 3
        self.assertIsNone(await self.bot.recap_text_allowance(1, self.one.id, self.job))
        self.assertIsNone(await self.bot.recap_audio_allowance(1, self.one.id, self.job))
        self.assertEqual(self.posted, [])

    async def test_at_the_limit_the_reason_comes_back(self) -> None:
        text = await self.bot.recap_text_allowance(1, self.one.id, self.job)
        audio = await self.bot.recap_audio_allowance(1, self.one.id, self.job)
        assert text and audio
        self.assertTrue(text.startswith(al.TEXT_PREFIX))
        self.assertTrue(audio.startswith(al.AUDIO_PREFIX))

    async def test_free_access_and_grants_are_not_limited(self) -> None:
        for kind in ("free", "grant"):
            self.bot.meter = FakeMeter(kind)
            self.assertIsNone(await self.bot.recap_text_allowance(1, self.one.id, self.job))
            self.assertIsNone(await self.bot.recap_audio_allowance(1, self.one.id, self.job))
        self.bot.meter = FakeMeter("paid")
        self.assertIsNotNone(await self.bot.recap_text_allowance(1, self.one.id, self.job))

    async def test_an_unreadable_count_raises_so_nothing_is_spent(self) -> None:
        self.bot.recaps = SimpleNamespace(used_in_month=AsyncMock(side_effect=OSError("down")))
        with self.assertRaises(OSError):
            await self.bot.recap_text_allowance(1, self.one.id, self.job)


class TheTextStep(text_tests.Base):
    """The recap worker with an allowance: a first run is refused before any AI call."""

    # The job tests' worker, borrowed so their own tests are not run again here.
    worker: pl.RecapWorker
    fast: fx.Canned

    async def setUpWorker(self, fast: fx.Canned) -> None:
        await cast(Any, text_tests.Working).setUpWorker(self, fast)

    def happy(self) -> fx.Canned:
        return cast(fx.Canned, cast(Any, text_tests.Working).happy(self))

    async def go(self) -> Job:
        return cast(Job, await cast(Any, text_tests.Working).go(self))

    async def worker_with(self, answer: Any) -> None:
        await self.setUpWorker(self.happy())
        self.worker._allowance = answer  # the one place the bot's check plugs in

    async def test_over_the_allowance_fails_with_the_line_and_calls_nothing(self) -> None:
        async def over(gid: int, cid: str, job: Job) -> str | None:
            return al.text_refusal(self.clock.now)

        await self.worker_with(over)
        job = await self.go()
        self.assertEqual(job.state, "failed")
        self.assertTrue(job.last_error.startswith(al.TEXT_PREFIX))
        self.assertEqual(job.calls, 0)

    async def test_try_again_over_the_allowance_keeps_the_recap_and_the_count(self) -> None:
        async def fine(gid: int, cid: str, job: Job) -> str | None:
            return None

        await self.worker_with(fine)
        done = await self.go()
        self.assertEqual(done.state, "ready")
        async with self.db.guild(1) as conn:  # what the website's Try again door does
            await conn.execute(
                "UPDATE recap_jobs SET tries = tries + 1, state = 'queued' WHERE guild_id = 1"
            )

        async def over(gid: int, cid: str, job: Job) -> str | None:
            return al.text_refusal(self.clock.now)

        self.worker._allowance = over
        job = await self.go()
        self.assertEqual((job.state, job.tries), ("ready", 0))
        self.assertEqual(job.summary, done.summary)  # nothing of the old recap was wiped
        self.assertTrue(job.last_error.startswith(al.TEXT_PREFIX))

    async def test_under_the_allowance_it_goes_through(self) -> None:
        async def fine(gid: int, cid: str, job: Job) -> str | None:
            return None

        await self.worker_with(fine)
        self.assertEqual((await self.go()).state, "ready")

    async def test_an_unreadable_count_waits_and_spends_nothing(self) -> None:
        async def broken(gid: int, cid: str, job: Job) -> str | None:
            raise OSError("down")

        await self.worker_with(broken)
        job = await self.worker.run(1, self.one.id, 100)
        assert job is not None
        self.assertNotEqual(job.state, "ready")
        self.assertEqual(job.calls, 0)
        self.assertEqual(job.errors, 1)
        self.assertEqual(job.last_error, pl.NO_COUNT)

    async def test_a_job_already_under_way_is_not_stopped_part_way(self) -> None:
        asked: list[int] = []

        async def counting(gid: int, cid: str, job: Job) -> str | None:
            asked.append(job.calls)
            return None

        await self.worker_with(counting)
        await self.go()
        self.assertEqual(asked, [0])  # asked once, at the start, never between stages


class TheNarration(audio_tests.Base):
    def with_allowance(self, answer: Any) -> Any:
        worker = self.worker()
        worker._allowance = answer
        return worker

    async def test_past_the_allowance_the_audio_is_skipped_and_the_text_stays(self) -> None:
        async def over(gid: int, cid: str, job: Any) -> str | None:
            return al.audio_refusal(self.now)

        before = await self.job()
        job = await self.with_allowance(over).run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.state, job.audio), ("ready", "failed"))
        self.assertTrue(job.audio_error.startswith(al.AUDIO_PREFIX))
        self.assertEqual(job.summary, before.summary)
        self.assertEqual(self.fake.calls, [])  # no paid call was made

    async def test_under_the_allowance_the_audio_is_made(self) -> None:
        async def fine(gid: int, cid: str, job: Any) -> str | None:
            return None

        job = await self.with_allowance(fine).run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.audio, "ready")

    async def test_a_dm_who_turned_narration_off_is_not_told_about_the_allowance(self) -> None:
        asked: list[int] = []

        async def over(gid: int, cid: str, job: Any) -> str | None:
            asked.append(1)
            return al.audio_refusal(self.now)

        worker = self.with_allowance(over)

        async def off(gid: int, cid: str, job: Any) -> bool:
            return False

        worker._switched = off
        job = await worker.run(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.audio, "off")
        self.assertEqual(asked, [])

    async def test_an_unreadable_count_makes_no_paid_call_and_tries_later(self) -> None:
        async def broken(gid: int, cid: str, job: Any) -> str | None:
            raise OSError("down")

        job = await self.with_allowance(broken).run(1, self.one.id, 100)
        assert job is not None
        self.assertNotEqual(job.audio, "ready")
        self.assertEqual(self.fake.calls, [])


_ = AudioInputs  # imported for the type of the loader the audio tests build


if __name__ == "__main__":
    unittest.main()
