"""A recap waits for its session's script, at most ten minutes (#3901, part 1b): held while the
script is being made, let go when it is done or failed or the wait is over, and untouched when
scripts are off or the session has no lines. Invented ids."""

from __future__ import annotations

from dmbot.campaigns.store import CampaignStore
from dmbot.recaps.store import SCRIPT_WAIT_S, RecapStore
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.theatre_runs import TheatreRuns
from tests import recap_fixtures as fx
from tests.pg import DatabaseTest

NOW = 1_700_000_000
ENDED = NOW - 60


class Wait(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaign = (await CampaignStore(self.db).create(1, "One", fx.DM)).id
        self.now = float(NOW)
        self.store = RecapStore(self.db, clock=lambda: self.now)
        self.store.script_wait = True
        self.runs = TheatreRuns(self.db)
        # the session started at 100 and ended a minute ago, with lines
        self.session = await TranscriptStore(self.db).open_session(1, self.campaign, 100)
        await self.sql(
            "UPDATE transcript_sessions SET ended_at = %s, line_count = 5 WHERE id = %s",
            ENDED,
            self.session,
        )
        await self.store.enqueue(1, self.campaign, 100, dm_user_id=fx.DM)
        await self.sql("UPDATE recap_jobs SET created_at = %s", ENDED)

    async def sql(self, text: str, *args: object) -> None:
        async with self.db.guild(1) as conn:
            await conn.execute(text, args)

    async def due(self) -> list[tuple[str, int]]:
        return await self.store.due(1)

    async def test_the_recap_waits_while_the_script_is_not_made(self) -> None:
        self.assertEqual(await self.due(), [])
        self.assertTrue(await self.store.waiting_for_script(1, self.campaign, 100))
        self.assertFalse(await self.store.lease(1, self.campaign, 100))

    async def test_it_waits_while_the_script_is_being_made_too(self) -> None:
        assert await self.runs.begin(1, self.session) is not None
        self.assertEqual(await self.due(), [])

    async def test_a_finished_script_lets_it_go(self) -> None:
        token = await self.runs.begin(1, self.session)
        assert token is not None
        await self.runs.finish(1, self.session, token, ok=True)
        self.assertEqual(await self.due(), [(self.campaign, 100)])
        self.assertFalse(await self.store.waiting_for_script(1, self.campaign, 100))

    async def test_a_failed_script_never_blocks_it(self) -> None:
        token = await self.runs.begin(1, self.session)
        assert token is not None
        await self.runs.finish(1, self.session, token, ok=False)
        self.assertEqual(await self.due(), [(self.campaign, 100)])

    async def test_after_ten_minutes_it_goes_ahead_without_the_script(self) -> None:
        self.now = float(ENDED + SCRIPT_WAIT_S + 1)
        self.assertEqual(await self.due(), [(self.campaign, 100)])
        self.assertFalse(await self.store.waiting_for_script(1, self.campaign, 100))

    async def test_scripts_off_changes_nothing(self) -> None:
        self.store.script_wait = False
        self.assertEqual(await self.due(), [(self.campaign, 100)])
        self.assertFalse(await self.store.waiting_for_script(1, self.campaign, 100))

    async def test_a_session_with_no_lines_is_not_waited_for(self) -> None:
        await self.sql("UPDATE transcript_sessions SET line_count = 0 WHERE id = %s", self.session)
        self.assertEqual(await self.due(), [(self.campaign, 100)])

    async def test_a_recap_with_no_transcript_session_is_not_waited_for(self) -> None:
        await self.store.enqueue(1, self.campaign, 555, dm_user_id=fx.DM)  # (no such session)
        await self.sql("UPDATE recap_jobs SET state = 'failed' WHERE session_at = 100")
        self.assertEqual(await self.due(), [(self.campaign, 555)])
