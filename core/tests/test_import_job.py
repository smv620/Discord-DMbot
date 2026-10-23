"""Cleaning an imported session in the background (#2105)."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock

from dmbot.ai import Reply
from dmbot.campaigns import CampaignStore
from dmbot.consent import TERMS_VERSION, ConsentStore
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

    def job(self, ai: Any | None, names: Any = None, consenting: Any = None) -> ImportJob:
        async def get(_g: int, _c: str) -> Any:
            return names if names is not None else names_with()

        async def now(guild_id: int) -> frozenset[int]:
            # a fresh store each time, so a change made straight in the database shows
            return await ConsentStore(self.db).consenting(guild_id)

        return ImportJob(self.store, get, ai, consenting or now)

    async def test_a_waiting_session_is_cleaned_labelled_and_marked_done(self) -> None:
        ai = FakeAI("1. game\n2. other")
        done = await self.job(ai).run_round(GUILD)
        self.assertEqual(done, 1)
        self.assertEqual(await self.state(), ("done", ""))
        lines = await self.transcripts.lines(GUILD, self.session)
        self.assertEqual([line.topic for line in lines], ["game", "off_topic"])
        self.assertEqual(lines[1].heard, "Anyone seen the pizza?")  # what was heard never changes

    async def test_cleaning_counts_its_lines_and_stamps_each_step(self) -> None:
        """#2564: the page's "n of m" and its last-moved time come from the job's own steps."""
        async with self.db.guild(GUILD) as conn:
            await conn.execute("UPDATE session_imports SET updated_at = 5")
        await self.job(None).run_round(GUILD)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT step_done, step_total, updated_at FROM session_imports"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["step_done"], row["step_total"])
        self.assertGreater(row["step_total"], 0)
        self.assertGreater(row["updated_at"], 5)  # moved forward by the steps and the finish

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

        job = self.job(None)
        job.lookup = broken
        await job.run_round(GUILD)
        self.assertEqual(await self.state(), ("failed", FAILED_NAMES))

    async def test_a_restart_puts_a_working_session_back_in_the_queue(self) -> None:
        self.assertTrue(await self.store.claim(GUILD, self.session))
        self.assertFalse(await self.store.claim(GUILD, self.session))
        self.assertEqual(await self.store.resume(GUILD, older_than_s=0), 1)
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

    async def test_consent_under_older_terms_means_no_ai_call_and_the_text_as_heard(self) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute("UPDATE consent SET terms_version = %s", (TERMS_VERSION - 1,))
        ai = FakeAI("1. other\n2. other")
        await self.job(ai).run_round(GUILD)
        self.assertEqual(ai.calls, 0)
        lines = await self.transcripts.lines(GUILD, self.session)
        self.assertEqual(
            [(line.text, line.topic) for line in lines], [(r.heard, "game") for r in lines]
        )

    async def test_a_player_who_stops_after_the_first_window_is_never_sent_again(self) -> None:
        from unittest.mock import patch

        from dmbot.transcript import import_job as ij

        consent = ConsentStore(self.db)

        class StopsAfterFirst(FakeAI):
            async def complete(self, system: str, text: str, **kw: Any) -> Reply:
                reply = await super().complete(system, text, **kw)
                consent.stop_now(GUILD, MIRA)  # the player presses Stop recording me
                return reply

        ai = StopsAfterFirst("1. other")
        with patch.object(ij, "WINDOW_LINES", 1):
            await self.job(ai, consenting=consent.consenting).run_round(GUILD)
        self.assertEqual(ai.calls, 1)  # the second window was never sent
        self.assertEqual(await self.state(), ("done", ""))

    async def test_stop_now_before_the_round_means_nothing_is_worked_on(self) -> None:
        consent = ConsentStore(self.db)
        await consent.consenting(GUILD)  # cached: in
        consent.stop_now(GUILD, MIRA)
        ai = FakeAI("1. other\n2. other")
        await self.job(ai, consenting=consent.consenting).run_round(GUILD)
        self.assertEqual(ai.calls, 0)
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
        self.assertEqual(await self.state(), ("done", "partly_labelled"))  # and it says so

    async def test_a_session_that_ran_out_of_windows_says_it_is_partly_labelled(self) -> None:
        from unittest.mock import patch

        from dmbot.transcript import import_job as ij

        with patch.object(ij, "MAX_WINDOWS", 1), patch.object(ij, "WINDOW_LINES", 1):
            await self.job(FakeAI("1. game")).run_round(GUILD)
        self.assertEqual(await self.state(), ("done", ij.PARTLY))

    async def test_an_old_claim_is_taken_again_in_the_next_round(self) -> None:
        self.assertTrue(await self.store.claim(GUILD, self.session))
        async with self.db.guild(GUILD) as conn:
            await conn.execute("UPDATE session_imports SET claimed_at = 1")
        self.assertEqual(await self.job(None).run_round(GUILD), 1)
        self.assertEqual(await self.state(), ("done", ""))

    async def test_a_fresh_claim_is_left_to_the_bot_that_holds_it(self) -> None:
        self.assertTrue(await self.store.claim(GUILD, self.session))
        self.assertEqual(await self.job(None).run_round(GUILD), 0)
        self.assertEqual(await self.state(), ("working", ""))

    async def test_a_session_gets_at_most_so_many_ai_windows(self) -> None:

        job = self.job(FakeAI("1. game"))
        topics, left, _failures, skipped = await job._topics(GUILD, [(MIRA, "a line")] * 30, 2, 0)
        self.assertEqual((len(topics), left, skipped), (30, 0, True))

    async def test_nothing_of_the_transcript_is_logged(self) -> None:
        with self.assertLogs("dmbot.transcript.import_job", level="INFO") as logs:
            await self.job(FakeAI("1. game\n2. game")).run_round(GUILD)
        joined = " ".join(logs.output).lower()
        self.assertNotIn("pizza", joined)
        self.assertNotIn("kazeth", joined)


if __name__ == "__main__":
    unittest.main()
