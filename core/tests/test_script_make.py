"""The bot's answer to the website's "Make the script" press (#3899): one claimed at a time, every
check made again, the job's own one-at-a-time lock respected, progress followed and a short word
when it stops. Invented ids."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot import script_make
from dmbot.script_make import ScriptMakes
from dmbot.transcript import theatre_job as job
from dmbot.transcript.store import TranscriptStore
from dmbot.ui import theatre_button
from tests.test_memory_names import DM, GUILD, MANAGER, NamesTest

NOW = int(time.time())  # (the round uses the real clock)


class Press(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = float(NOW)
        self.counter = 0
        self.makes = ScriptMakes(self.db, clock=lambda: self.now)
        self.session = await TranscriptStore(self.db).open_session(GUILD, self.campaign.id, 100)

    async def queue(self, user: int = DM, *, at: int = NOW, session: str | None = None) -> str:
        rid = f"{len(await self.rows()):032x}"
        if session is None:  # (one waiting or working press for a session: each gets its own)
            self.counter += 1
            session = await TranscriptStore(self.db).open_session(
                GUILD, self.campaign.id, 200 + self.counter
            )
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO script_makes (guild_id, campaign_id, id, session_id, user_id,"
                " created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                (GUILD, self.campaign.id, rid, session, user, at),
            )
        return rid

    async def rows(self) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT * FROM script_makes ORDER BY created_at, id")
            return [dict(r) for r in await cur.fetchall()]

    def fake_bot(
        self, *, on: bool = True, plan: bool = True, started: bool = True, ok: bool = True
    ) -> Any:
        me = SimpleNamespace(
            settings=SimpleNamespace(theatre_on=on),
            theatre_ai=object(),
            memory=object(),
            transcripts=SimpleNamespace(
                session=AsyncMock(
                    return_value=SimpleNamespace(campaign_id=self.campaign.id, lines=3, number=1)
                )
            ),
            campaigns=SimpleNamespace(get=AsyncMock(return_value=self.campaign), db=self.db),
            plan_allows=AsyncMock(return_value=plan),
        )
        me.outcome = job.Outcome("ok", started=started, ok=ok)
        return me

    async def round(self, me: Any) -> tuple[int, AsyncMock]:
        make = AsyncMock(return_value=me.outcome)
        with patch.object(theatre_button, "make_for", make):
            took = await script_make.run_round(me, GUILD)
        return took, make

    async def test_the_oldest_waiting_press_is_claimed_once(self) -> None:
        first = await self.queue(at=NOW - 20)
        await self.queue(at=NOW - 10)
        got = await self.makes.claim_next(GUILD)
        assert got is not None
        self.assertEqual(got.id, first)
        again = await self.makes.claim_next(GUILD)
        assert again is not None
        self.assertNotEqual(again.id, first)  # (the first is being worked on)
        self.assertIsNone(await self.makes.claim_next(GUILD))

    async def test_a_press_that_stopped_moving_is_taken_again_and_an_old_one_never(self) -> None:
        rid = await self.queue()
        got = await self.makes.claim_next(GUILD)
        assert got is not None
        self.now += script_make.RETAKE_AFTER_S + 5
        retaken = await self.makes.claim_next(GUILD)
        assert retaken is not None and retaken.id == rid
        self.now += script_make.GIVE_UP_S + 5  # (the door has failed it by now)
        self.assertIsNone(await self.makes.claim_next(GUILD))

    async def test_a_game_master_press_makes_the_script_and_ends_done(self) -> None:
        rid = await self.queue()
        took, make = await self.round(self.fake_bot())
        self.assertEqual((took, make.await_count), (1, 1))
        row = next(r for r in await self.rows() if r["id"] == rid)
        self.assertEqual((row["state"], row["error"]), ("done", ""))

    async def test_someone_who_is_no_longer_a_dm_gets_not_allowed_and_nothing_runs(self) -> None:
        rid = await self.queue(MANAGER)
        _, make = await self.round(self.fake_bot())
        make.assert_not_awaited()
        row = next(r for r in await self.rows() if r["id"] == rid)
        self.assertEqual((row["state"], row["error"]), ("failed", script_make.NOT_ALLOWED))

    async def test_the_switch_and_the_plan_are_checked_again(self) -> None:
        await self.queue()
        _, make = await self.round(self.fake_bot(on=False))
        self.assertEqual((await self.rows())[0]["error"], script_make.OFF)
        await self.queue()
        _, make = await self.round(self.fake_bot(plan=False))
        make.assert_not_awaited()
        self.assertEqual((await self.rows())[1]["error"], script_make.PLAN)

    async def test_a_session_of_another_campaign_is_gone(self) -> None:
        await self.queue()
        me = self.fake_bot()
        me.transcripts.session = AsyncMock(
            return_value=SimpleNamespace(campaign_id="d" * 32, lines=3, number=1)
        )
        _, make = await self.round(me)
        make.assert_not_awaited()
        self.assertEqual((await self.rows())[0]["error"], script_make.GONE)

    async def test_a_run_another_worker_has_is_reported_busy_never_run_twice(self) -> None:
        await self.queue()
        await self.round(self.fake_bot(started=False))
        row = (await self.rows())[0]
        self.assertEqual((row["state"], row["error"]), ("failed", script_make.BUSY))

    async def test_a_job_that_made_nothing_ends_with_a_short_word(self) -> None:
        await self.queue()
        await self.round(self.fake_bot(ok=False))
        row = (await self.rows())[0]
        self.assertEqual((row["state"], row["error"]), ("failed", script_make.NO_SCRIPT))
        self.assertLessEqual(len(row["error"]), 40)

    async def test_progress_is_copied_and_only_the_holder_can_write(self) -> None:
        await self.queue()
        got = await self.makes.claim_next(GUILD)
        assert got is not None
        self.now += 7
        self.assertTrue(await self.makes.moved(GUILD, got, 2, 5))
        row = (await self.rows())[0]
        self.assertEqual((row["part"], row["of_parts"]), (2, 5))
        stale = SimpleNamespace(id=got.id, claimed_at=1)
        self.assertFalse(await self.makes.moved(GUILD, stale, 3, 5))  # type: ignore[arg-type]
        self.assertFalse(await self.makes.end(GUILD, stale, ok=True))  # type: ignore[arg-type]

    async def test_no_press_waiting_takes_nothing(self) -> None:
        took, make = await self.round(self.fake_bot())
        self.assertEqual((took, make.await_count), (0, 0))
