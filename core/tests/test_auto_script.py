"""A script for every session, made by itself (#3901): which sessions are due, and that the round
checks the switch and the plan, begins at most a few runs, tells the DM Bot channel and never
questions the DM. Invented ids."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot import auto_script
from dmbot.transcript import theatre_job as job
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.theatre_runs import TheatreRuns
from tests.test_memory_names import GUILD, NamesTest

NOW = 1_700_100_000
CHANNEL = 4040


class Due(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = TranscriptStore(self.db)
        self.runs = TheatreRuns(self.db)

    async def session(self, ended: int | None, lines: int = 5) -> str:
        sid = await self.store.open_session(GUILD, self.campaign.id, ended or NOW)
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE transcript_sessions SET ended_at = %s, line_count = %s WHERE id = %s",
                (ended, lines, sid),
            )
        return sid

    async def due(self) -> list[str]:
        found = await self.runs.sessions_without_script(
            GUILD,
            since=NOW - auto_script.SINCE_S,
            settled_before=NOW - auto_script.SETTLE_S,
            limit=10,
        )
        return [sid for _cid, sid in found]

    async def test_an_ended_session_with_lines_is_due(self) -> None:
        sid = await self.session(NOW - 600)
        self.assertEqual(await self.due(), [sid])

    async def test_one_still_going_or_just_ended_or_empty_is_not(self) -> None:
        await self.session(None)
        await self.session(NOW - 10)  # ended seconds ago: its transcript is not final
        await self.session(NOW - 700, lines=0)
        self.assertEqual(await self.due(), [])

    async def test_a_week_old_session_is_left_to_the_websites_button(self) -> None:
        await self.session(NOW - auto_script.SINCE_S - 100)
        self.assertEqual(await self.due(), [])

    async def test_a_session_with_a_run_row_is_not_due_again(self) -> None:
        sid = await self.session(NOW - 600)
        token = await self.runs.begin(GUILD, sid)
        assert token is not None
        await self.runs.finish(GUILD, sid, token, ok=False)  # (a failed run is not retried)
        self.assertEqual(await self.due(), [])


def bot_for(*, on: bool = True, plan: bool = True, ok: bool = True) -> Any:
    campaign = SimpleNamespace(dm_bot_channel_id=CHANNEL, id="c" * 32)
    me = SimpleNamespace(
        settings=SimpleNamespace(theatre_on=on),
        theatre_ai=object(),
        memory=object(),
        transcripts=SimpleNamespace(
            session=AsyncMock(return_value=SimpleNamespace(lines=4, number=3))
        ),
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign), db=object()),
        plan_allows=AsyncMock(return_value=plan),
        post_message=AsyncMock(),
    )
    me.outcome = job.Outcome("📜 Script made: https://x.test/s", started=True, ok=ok)
    return me


class Round(NamesTest):
    async def run_with(self, me: Any, due: list[tuple[str, str]]) -> tuple[int, AsyncMock]:
        make = AsyncMock(return_value=me.outcome)
        me.campaigns.db = self.db
        with (
            patch.object(
                auto_script.TheatreRuns, "sessions_without_script", AsyncMock(return_value=due)
            ),
            patch.object(auto_script.theatre_button, "make_for", make),
        ):
            tried = await auto_script.run_round(me, GUILD, NOW)
        return tried, make

    async def test_a_due_session_gets_its_script_and_one_line_in_the_dm_bot_channel(self) -> None:
        me = bot_for()
        tried, make = await self.run_with(me, [("c" * 32, "s" * 32)])
        self.assertEqual((tried, make.await_count), (1, 1))
        me.post_message.assert_awaited_once()
        self.assertEqual(me.post_message.await_args.args[0], CHANNEL)
        self.assertIn("Script made", me.post_message.await_args.args[1])

    async def test_a_failed_script_says_so_and_points_to_the_website(self) -> None:
        me = bot_for(ok=False)
        me.outcome = job.Outcome("Nothing was changed.", started=True, ok=False)
        await self.run_with(me, [("c" * 32, "s" * 32)])
        said = me.post_message.await_args.args[1]
        self.assertIn("couldn't be made", said)
        self.assertIn("website", said)

    async def test_nothing_is_made_with_the_switch_off(self) -> None:
        tried, make = await self.run_with(bot_for(on=False), [("c" * 32, "s" * 32)])
        self.assertEqual((tried, make.await_count), (0, 0))

    async def test_a_plan_without_story_memory_makes_none_and_does_not_count(self) -> None:
        me = bot_for(plan=False)
        tried, make = await self.run_with(me, [("c" * 32, "s" * 32)])
        self.assertEqual((tried, make.await_count), (0, 0))
        self.assertEqual(me.plan_allows.await_args.kwargs, {"fail_open": False})

    async def test_only_a_few_are_begun_each_round(self) -> None:
        due = [("c" * 32, f"{i:032x}") for i in range(6)]
        tried, make = await self.run_with(bot_for(), due)
        self.assertEqual((tried, make.await_count), (auto_script.PER_ROUND, auto_script.PER_ROUND))

    async def test_a_run_another_worker_has_is_not_announced(self) -> None:
        me = bot_for()
        me.outcome = job.Outcome(job.ALREADY, started=False)
        await self.run_with(me, [("c" * 32, "s" * 32)])
        me.post_message.assert_not_awaited()
