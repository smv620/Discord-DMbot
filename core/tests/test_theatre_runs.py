"""Only one script of a session is made at a time (#3244, slice 5b), against a real Postgres."""

from __future__ import annotations

import asyncio
from typing import Any

from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.theatre_runs import STALE_S, TheatreRuns
from tests.test_memory_names import GUILD, NamesTest

START = 1_700_000_000


class TheRunCheck(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = float(START)
        self.runs = TheatreRuns(self.db, clock=lambda: self.now)
        self.session = await TranscriptStore(self.db).open_session(GUILD, self.campaign.id, START)

    async def row(self) -> dict[str, Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT * FROM theatre_runs WHERE session_id = %s", (self.session,)
            )
            row: Any = await cur.fetchone()
            return dict(row)

    async def test_a_second_press_is_refused_while_a_run_is_going(self) -> None:
        self.assertIsNotNone(await self.runs.begin(GUILD, self.session))
        self.assertIsNone(await self.runs.begin(GUILD, self.session))

    async def test_two_presses_at_once_start_one_run(self) -> None:
        got = await asyncio.gather(*(self.runs.begin(GUILD, self.session) for _ in range(5)))
        self.assertEqual(sum(t is not None for t in got), 1)

    async def test_a_run_that_moved_keeps_its_place(self) -> None:
        token = await self.runs.begin(GUILD, self.session)
        assert token is not None
        self.now += STALE_S - 1
        self.assertTrue(await self.runs.moved(GUILD, self.session, token, 2, 5))
        self.now += STALE_S - 1
        self.assertIsNone(await self.runs.begin(GUILD, self.session))
        row = await self.row()
        self.assertEqual((row["part"], row["parts"]), (2, 5))

    async def test_a_dead_run_is_taken_over_and_cannot_touch_its_successor(self) -> None:
        old = await self.runs.begin(GUILD, self.session)
        assert old is not None
        await self.runs.moved(GUILD, self.session, old, 3, 5)
        self.now += STALE_S + 1
        new = await self.runs.begin(GUILD, self.session)
        assert new is not None
        row = await self.row()
        self.assertEqual((row["part"], row["parts"]), (0, 0))  # (reset for the new run)
        self.assertFalse(await self.runs.moved(GUILD, self.session, old, 4, 5))
        self.assertFalse(await self.runs.finish(GUILD, self.session, old, ok=True))
        row = await self.row()
        self.assertEqual((row["state"], row["token"], row["part"]), ("running", new, 0))
        self.assertIsNone(await self.runs.begin(GUILD, self.session))  # (still the new run's)

    async def test_a_finished_or_failed_run_lets_the_next_press_start(self) -> None:
        for ok in (True, False):
            token = await self.runs.begin(GUILD, self.session)
            assert token is not None
            self.assertTrue(await self.runs.finish(GUILD, self.session, token, ok=ok))
            self.assertFalse(await self.runs.finish(GUILD, self.session, token, ok=not ok))
        self.assertIsNotNone(await self.runs.begin(GUILD, self.session))

    async def test_another_server_sees_nothing_and_a_deleted_session_takes_its_row(self) -> None:
        await self.runs.begin(GUILD, self.session)
        async with self.db.guild(GUILD + 1) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM theatre_runs")
            other: Any = await cur.fetchone()
        self.assertEqual(other["n"], 0)
        async with self.db.guild(GUILD) as conn:
            await conn.execute("DELETE FROM transcript_sessions WHERE id = %s", (self.session,))
            cur = await conn.execute("SELECT count(*) AS n FROM theatre_runs")
            mine: Any = await cur.fetchone()
        self.assertEqual(mine["n"], 0)
