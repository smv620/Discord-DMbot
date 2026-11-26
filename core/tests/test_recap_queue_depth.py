"""The worker's round log of the recap queue (#3845 4b, #4914): how many jobs wait and how long the
oldest has, counts only. Database tests: skipped where there is no Postgres."""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

from dmbot.campaigns.store import CampaignStore
from dmbot.recaps.discord import RecapFeatures
from dmbot.recaps.store import RecapStore
from tests import recap_fixtures as fx
from tests.pg import DatabaseTest

NOW = 1_700_000_000
LINE = re.compile(r"^Recap queue \((text|audio)\): \d+ waiting, oldest \d+ s$")


class Base(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = float(NOW)
        self.store = RecapStore(self.db, clock=lambda: self.now)
        campaigns = CampaignStore(self.db)
        self.one = await campaigns.create(1, "One", fx.DM)
        self.two = await campaigns.create(1, "Two", fx.DM)

    async def job(self, cid: str, at: int, *, wait: int, **fields: Any) -> None:
        await self.store.enqueue(1, cid, at, dm_user_id=fx.DM)
        await self.store.update(1, cid, at, next_at=NOW - wait, **fields)


class TheDepth(Base):
    async def test_it_counts_waiting_text_jobs_and_the_oldest_wait(self) -> None:
        self.assertEqual(await self.store.queue_depth(1, audio=False), (0, 0))
        await self.job(self.one.id, 100, wait=40)
        await self.job(self.two.id, 100, wait=300)
        self.assertEqual(await self.store.queue_depth(1, audio=False), (2, 300))

    async def test_a_ready_a_failed_and_a_not_yet_due_job_are_not_counted(self) -> None:
        await self.job(self.one.id, 100, wait=40, state="ready")
        await self.job(self.one.id, 200, wait=40, state="failed")
        await self.store.enqueue(1, self.two.id, 100, dm_user_id=fx.DM)
        await self.store.update(1, self.two.id, 100, next_at=NOW + 600)  # (later)
        self.assertEqual(await self.store.queue_depth(1, audio=False), (0, 0))

    async def test_it_counts_the_audio_that_waits(self) -> None:
        await self.job(self.one.id, 100, wait=40, state="ready")
        async with self.db.guild(1) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET audio = 'queued', audio_next_at = %s WHERE guild_id = 1",
                (NOW - 90,),
            )
        n, oldest = await self.store.queue_depth(1, audio=True)
        self.assertEqual((n, oldest), (1, 90))

    async def test_another_servers_jobs_are_not_counted(self) -> None:
        other = await CampaignStore(self.db).create(2, "Other", fx.DM)
        await self.store.enqueue(2, other.id, 100, dm_user_id=fx.DM)
        self.assertEqual(await self.store.queue_depth(1, audio=False), (0, 0))


class TheLine(Base):
    def bot(self, *, worker_on: bool) -> Any:
        return SimpleNamespace(settings=SimpleNamespace(worker_on=worker_on), recaps=self.store)

    async def test_the_line_is_counts_only_and_only_with_the_worker_on(self) -> None:
        await self.job(self.one.id, 100, wait=40)
        with self.assertLogs("dmbot.bot", level="INFO") as got:
            await RecapFeatures._log_queue_depth(self.bot(worker_on=True), [1], audio=False)
        (message,) = [r.getMessage() for r in got.records]
        self.assertRegex(message, LINE)
        self.assertNotIn(self.one.id, message)  # (no id of anything)
        with self.assertNoLogs("dmbot.bot", level="INFO"):
            await RecapFeatures._log_queue_depth(self.bot(worker_on=False), [1], audio=False)

    async def test_nothing_waiting_logs_nothing_and_a_failed_read_is_skipped(self) -> None:
        with self.assertNoLogs("dmbot.bot", level="INFO"):
            await RecapFeatures._log_queue_depth(self.bot(worker_on=True), [1], audio=True)

        async def broken(guild_id: int, *, audio: bool) -> tuple[int, int]:
            raise RuntimeError("down")

        bot = self.bot(worker_on=True)
        bot.recaps = SimpleNamespace(queue_depth=broken)
        with self.assertLogs("dmbot.bot", level="WARNING") as got:
            await RecapFeatures._log_queue_depth(bot, [1], audio=False)
        self.assertIn("RuntimeError", got.output[0])
