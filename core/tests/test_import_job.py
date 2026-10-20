"""Cleaning an imported session in the background (#2105)."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock

from dmbot.ai import Reply
from dmbot.campaigns import CampaignStore
from dmbot.consent import ConsentStore
from dmbot.transcript.import_job import FAILED_NAMES, ImportJob, ImportStore
from dmbot.transcript.store import TranscriptStore
from tests.pg import DatabaseTest
from tests.test_transcript_cleaner import lookup as names_with

GUILD, DM, MIRA = 55, 5, 7001


class FakeAI:
    def __init__(self, answer: str = "", fail: bool = False) -> None:
        self.answer, self.fail, self.calls = answer, fail, 0

    async def complete(self, system: str, text: str, **kw: Any) -> Reply:
        self.calls += 1
        if self.fail:
            raise RuntimeError("down")
        return Reply(self.answer, False)


class TheJob(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaign = await self.campaigns.create(GUILD, "One", DM)
        await ConsentStore(self.db).grant(GUILD, MIRA)
        self.transcripts = TranscriptStore(self.db)
        self.store = ImportStore(self.db)
        self.session = await self.transcripts.open_session(GUILD, self.campaign.id, 1_700_000_000)
        from dmbot.transcript.models import Line

        await self.transcripts.add_lines(
            GUILD,
            self.session,
            [
                Line(
                    1_700_000_000_000,
                    MIRA,
                    "We meet Kazeth at the gate.",
                    "We meet Kazeth at the gate.",
                ),
                Line(1_700_000_005_000, MIRA, "Anyone seen the pizza?", "Anyone seen the pizza?"),
            ],
        )
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id,"
                " file_name, imported_by, imported_at) VALUES (%s, %s, %s, %s, 'a.txt', %s, 1)",
                (GUILD, self.campaign.id, self.session, "a" * 32, DM),
            )

    async def state(self) -> tuple[str, str]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT state, reason FROM session_imports")
            row = await cur.fetchone()
        assert row is not None
        return row["state"], row["reason"]

    def job(self, ai: Any | None, names: Any = None) -> ImportJob:
        async def get(_g: int, _c: str) -> Any:
            return names if names is not None else names_with()

        return ImportJob(self.store, get, ai)

    async def test_a_waiting_session_is_cleaned_labelled_and_marked_done(self) -> None:
        ai = FakeAI("1. game\n2. other")
        done = await self.job(ai).run_round(GUILD)
        self.assertEqual(done, 1)
        self.assertEqual(await self.state(), ("done", ""))
        lines = await self.transcripts.lines(GUILD, self.session)
        self.assertEqual([line.topic for line in lines], ["game", "off_topic"])
        self.assertEqual(lines[1].heard, "Anyone seen the pizza?")  # what was heard never changes

    async def test_a_misheard_name_is_fixed_in_the_cleaned_text_only(self) -> None:
        await self.job(None).run_round(GUILD)
        first = (await self.transcripts.lines(GUILD, self.session))[0]
        self.assertEqual(first.heard, "We meet Kazeth at the gate.")
        self.assertIn("Ka'zeth", first.text)

    async def test_without_an_ai_or_when_it_fails_the_lines_stay_game_talk(self) -> None:
        for ai in (None, FakeAI(fail=True)):
            async with self.db.guild(GUILD) as conn:
                await conn.execute("UPDATE session_imports SET state = 'waiting'")
            await self.job(ai).run_round(GUILD)
            self.assertEqual(await self.state(), ("done", ""))
            self.assertEqual(
                {line.topic for line in await self.transcripts.lines(GUILD, self.session)}, {"game"}
            )

    async def test_a_session_that_is_not_waiting_is_left_alone(self) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute("UPDATE session_imports SET state = 'failed'")
        ai = FakeAI("1. other")
        self.assertEqual(await self.job(ai).run_round(GUILD), 0)
        self.assertEqual(ai.calls, 0)

    async def test_names_that_cannot_be_read_fail_the_session_with_a_reason(self) -> None:
        async def broken(_g: int, _c: str) -> Any:
            raise RuntimeError("no names")

        await ImportJob(self.store, broken, None).run_round(GUILD)
        self.assertEqual(await self.state(), ("failed", FAILED_NAMES))

    async def test_a_restart_puts_a_working_session_back_in_the_queue(self) -> None:
        self.assertTrue(await self.store.claim(GUILD, self.session))
        self.assertFalse(await self.store.claim(GUILD, self.session))
        self.assertEqual(await self.store.resume(GUILD), 1)
        self.assertEqual(await self.state(), ("waiting", ""))

    async def test_someone_who_stopped_being_recorded_is_not_cleaned_or_sent_to_the_ai(
        self,
    ) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute("DELETE FROM consent WHERE user_id = %s", (MIRA,))
        ai = FakeAI("1. game\n2. game")
        await self.job(ai).run_round(GUILD)
        self.assertEqual(ai.calls, 0)
        self.assertEqual(await self.state(), ("done", ""))
        first = (await self.transcripts.lines(GUILD, self.session))[0]
        self.assertEqual(first.text, first.heard)

    async def test_an_error_never_leaves_the_session_working(self) -> None:
        self.store.rows = AsyncMock(side_effect=RuntimeError("db blip"))  # type: ignore[method-assign]
        await self.job(None).run_round(GUILD)
        self.assertEqual(await self.state(), ("failed", "error"))

    async def test_a_stopped_bot_puts_the_session_back_in_the_queue(self) -> None:
        import asyncio

        self.store.rows = AsyncMock(side_effect=asyncio.CancelledError)  # type: ignore[method-assign]
        with self.assertRaises(asyncio.CancelledError):
            await self.job(None).run_round(GUILD)
        self.assertEqual(await self.state(), ("waiting", ""))

    async def test_an_ai_that_keeps_failing_is_left_alone(self) -> None:
        from dmbot.transcript.import_job import TOPIC_FAILURES_BEFORE_REST
        from dmbot.transcript.models import Line

        await self.transcripts.add_lines(
            GUILD,
            self.session,
            [Line(1_700_000_100_000 + n * 1000, MIRA, f"line {n}", f"line {n}") for n in range(60)],
        )
        ai = FakeAI(fail=True)
        await self.job(ai).run_round(GUILD)
        self.assertEqual(ai.calls, TOPIC_FAILURES_BEFORE_REST)
        self.assertEqual(await self.state(), ("done", ""))

    async def test_a_session_gets_at_most_so_many_ai_windows(self) -> None:

        job = self.job(FakeAI("1. game"))
        topics, left, _failures = await job._topics(["a line"] * 30, 2, 0)
        self.assertEqual((len(topics), left), (30, 0))

    async def test_nothing_of_the_transcript_is_logged(self) -> None:
        with self.assertLogs("dmbot.transcript.import_job", level="INFO") as logs:
            await self.job(FakeAI("1. game\n2. game")).run_round(GUILD)
        joined = " ".join(logs.output).lower()
        self.assertNotIn("pizza", joined)
        self.assertNotIn("kazeth", joined)


if __name__ == "__main__":
    unittest.main()
