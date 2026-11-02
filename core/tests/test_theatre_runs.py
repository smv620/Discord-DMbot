"""Only one script of a session is made at a time (#3244, slice 5b), against a real Postgres."""

from __future__ import annotations

from dmbot.transcript.theatre_runs import STALE_S, TheatreRuns
from tests.test_memory_names import GUILD, NamesTest

START = 1_700_000_000


class TheRunCheck(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.transcript.store import TranscriptStore

        self.now = float(START)
        self.runs = TheatreRuns(self.db, clock=lambda: self.now)
        self.session = await TranscriptStore(self.db).open_session(GUILD, self.campaign.id, START)

    async def test_a_second_press_is_refused_while_a_run_is_going(self) -> None:
        self.assertTrue(await self.runs.begin(GUILD, self.session))
        self.assertFalse(await self.runs.begin(GUILD, self.session))

    async def test_a_run_that_moved_keeps_its_place(self) -> None:
        await self.runs.begin(GUILD, self.session)
        self.now += STALE_S - 1
        await self.runs.moved(GUILD, self.session, 2, 5)
        self.now += STALE_S - 1
        self.assertFalse(await self.runs.begin(GUILD, self.session))

    async def test_a_dead_run_is_taken_over(self) -> None:
        await self.runs.begin(GUILD, self.session)
        self.now += STALE_S + 1
        self.assertTrue(await self.runs.begin(GUILD, self.session))

    async def test_a_finished_or_failed_run_lets_the_next_press_start(self) -> None:
        for ok in (True, False):
            self.assertTrue(await self.runs.begin(GUILD, self.session))
            await self.runs.finish(GUILD, self.session, ok=ok)
        self.assertTrue(await self.runs.begin(GUILD, self.session))
