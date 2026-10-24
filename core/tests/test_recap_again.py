"""A DM makes or redoes the recap of any past session (#2608), against a real database: a session
that never had one, a failed one, an approved one that stays until the new one is approved, a
non-DM refused, a session with no lines left refused, and the bridge reading the right earlier
recap. Needs Postgres."""

from __future__ import annotations

import datetime as dt
from typing import Any

from dmbot.recaps.approval import text_hash
from tests import recap_fixtures as fx
from tests.test_recap_jobs import NOW, Base

SESSION = 5_000
OTHER_DM = 99


class Again(Base):
    async def lines(self, started: int, *, campaign: str | None = None, count: int = 2) -> None:
        sid = f"{started:032x}"
        await self.sql(
            "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at, ended_at,"
            " line_count, speakers) VALUES (%s, 1, %s, %s, %s, %s, %s)",
            sid, campaign or self.one.id, started, started + 3600, count, [fx.ALICE],
        )  # fmt: skip
        for n in range(count):
            await self.sql(
                "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id, heard,"
                " text) VALUES (1, %s, %s, %s, 'raw', 'clean')",
                sid, n * 1000, fx.ALICE,
            )  # fmt: skip

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(1) as conn:
            await conn.execute(statement, args)

    async def written(self, at: int, *, posted: bool = False, approved: bool = False) -> None:
        """A finished recap of the session, as the worker leaves it."""
        await self.store.enqueue(1, self.one.id, at, dm_user_id=fx.DM)
        await self.store.update(
            1, self.one.id, at, state="ready", title="The old night", summary="The old words."
        )
        if approved or posted:
            await self.sql(
                "UPDATE recap_jobs SET approved_by = %s, approved_at = 5, approved_hash = %s,"
                " audio_choice = 'text' WHERE guild_id = 1 AND session_at = %s",
                fx.DM, text_hash("The old night", "The old words."), at,
            )  # fmt: skip
        if posted:
            await self.sql(
                "UPDATE recap_jobs SET status = 'posted', posted_url = 'https://discord.com/channels/1/2/3'"
                " WHERE guild_id = 1 AND session_at = %s",
                at,
            )

    async def again(self, at: int = SESSION, who: int = fx.DM, **kw: Any) -> str:
        return await self.store.start_over(1, self.one.id, at, who, **kw)

    async def test_a_session_that_never_had_a_recap_gets_one_queued(self) -> None:
        await self.lines(SESSION)
        self.assertEqual(await self.again(), "queued")
        job = await self.store.get(1, self.one.id, SESSION)
        assert job is not None
        self.assertEqual((job.state, job.status, job.dm_user_id), ("queued", "draft", fx.DM))
        self.assertGreater(job.restarted_at, 0)

    async def test_a_player_who_is_no_dm_is_refused_and_nothing_is_made(self) -> None:
        await self.lines(SESSION)
        self.assertEqual(await self.again(who=fx.ALICE), "not_allowed")
        self.assertIsNone(await self.store.get(1, self.one.id, SESSION))

    async def test_another_campaigns_dm_is_refused(self) -> None:
        await self.lines(SESSION)
        other = await self.campaigns.create(1, "Elsewhere", OTHER_DM)
        self.assertEqual(
            await self.store.start_over(1, other.id, SESSION, OTHER_DM), "no_lines"
        )  # (its own campaign has no such session)
        self.assertEqual(await self.again(who=OTHER_DM), "not_allowed")

    async def test_a_session_with_no_lines_left_is_refused_with_words_to_upload(self) -> None:
        await self.lines(SESSION, count=0)
        self.assertEqual(await self.again(), "no_lines")
        self.assertEqual(await self.again(at=1234), "no_lines")  # no such session
        from dmbot.ui import recap_again as ui

        self.assertIn("upload one instead", ui.NO_LINES)
        self.assertIsNone(await self.store.get(1, self.one.id, SESSION))

    async def test_a_failed_recap_starts_over_clean_and_its_tries_come_back(self) -> None:
        await self.lines(SESSION)
        await self.store.enqueue(1, self.one.id, SESSION, dm_user_id=fx.DM)
        await self.store.update(
            1, self.one.id, SESSION, state="failed", last_error="It broke", errors=3, calls=7,
            beats=[{"x": 1}], problems=["p"],
        )  # fmt: skip
        await self.sql("UPDATE recap_jobs SET tries = 2 WHERE guild_id = 1")
        self.assertEqual(await self.again(), "queued")
        job = await self.store.get(1, self.one.id, SESSION)
        assert job is not None
        self.assertEqual(
            (job.state, job.last_error, job.errors, job.calls, job.beats, job.problems, job.tries),
            ("queued", "", 0, 0, [], (), 0),
        )

    async def test_a_recap_being_written_is_busy(self) -> None:
        await self.lines(SESSION)
        await self.store.enqueue(1, self.one.id, SESSION, dm_user_id=fx.DM)
        self.assertEqual(await self.again(), "busy")

    async def test_a_recap_that_is_made_asks_once_and_a_draft_is_replaced(self) -> None:
        await self.lines(SESSION)
        await self.written(SESSION)
        self.assertEqual(await self.again(), "needs_confirm")
        job = await self.store.get(1, self.one.id, SESSION)
        assert job is not None
        self.assertEqual(job.title, "The old night")  # nothing changed by asking
        self.assertEqual(await self.again(replace_ok=True), "queued")
        job = await self.store.get(1, self.one.id, SESSION)
        assert job is not None
        self.assertEqual((job.state, job.title, job.previous), ("queued", "", None))

    async def test_an_approved_recap_stays_until_the_new_one_is_approved(self) -> None:
        await self.lines(SESSION)
        await self.written(SESSION, approved=True)
        self.assertEqual(await self.again(replace_ok=True), "queued")
        job = await self.store.get(1, self.one.id, SESSION)
        assert job is not None
        self.assertEqual(job.state, "queued")
        self.assertTrue(job.keeps_previous)
        self.assertEqual(job.previous["title"], "The old night")
        self.assertFalse(job.approved)  # the old approval does not carry to the new text
        # the new one is written and approved: the old one is no longer held
        await self.store.update(1, self.one.id, SESSION, state="ready", title="N", summary="New.")
        await self.sql(
            "UPDATE recap_jobs SET approved_hash = %s WHERE guild_id = 1", text_hash("N", "New.")
        )
        job = await self.store.get(1, self.one.id, SESSION)
        assert job is not None
        self.assertFalse(job.keeps_previous)

    async def test_the_old_recap_comes_back_when_the_new_one_fails_or_is_not_wanted(self) -> None:
        await self.lines(SESSION)
        await self.written(SESSION, posted=True)
        await self.again(replace_ok=True)
        await self.store.update(1, self.one.id, SESSION, state="failed", last_error="It broke")
        self.assertTrue(await self.store.keep_previous(1, self.one.id, SESSION))
        job = await self.store.get(1, self.one.id, SESSION)
        assert job is not None
        self.assertEqual(
            (job.state, job.status, job.title, job.summary, job.posted_url, job.previous),
            (
                "ready",
                "posted",
                "The old night",
                "The old words.",
                "https://discord.com/channels/1/2/3",
                None,
            ),
        )
        self.assertTrue(job.approved)
        self.assertFalse(await self.store.keep_previous(1, self.one.id, SESSION))  # nothing left

    async def test_not_this_one_on_a_new_recap_keeps_the_old_one(self) -> None:
        await self.lines(SESSION)
        await self.written(SESSION, approved=True)
        await self.again(replace_ok=True)
        await self.store.update(1, self.one.id, SESSION, state="ready", title="N", summary="New.")
        self.assertTrue(await self.store.decide(1, self.one.id, SESSION, "drop"))
        job = await self.store.get(1, self.one.id, SESSION)
        assert job is not None
        self.assertEqual((job.status, job.title), ("draft", "The old night"))

    async def test_a_recap_started_twice_before_approval_keeps_the_first_old_one(self) -> None:
        await self.lines(SESSION)
        await self.written(SESSION, approved=True)
        await self.again(replace_ok=True)
        await self.store.update(1, self.one.id, SESSION, state="ready", title="N", summary="New.")
        await self.again(replace_ok=True)
        job = await self.store.get(1, self.one.id, SESSION)
        assert job is not None
        self.assertEqual(job.previous["title"], "The old night")

    async def test_the_bridge_reads_the_recap_of_the_session_before_not_the_newest(self) -> None:
        await self.written(1_000, posted=True)
        await self.written(9_000, posted=True)
        await self.lines(5_000)
        await self.again(at=5_000)
        before = await self.store.bridge(1, self.one.id, 5_000)
        assert before is not None
        self.assertEqual(before.session_at, 1_000)  # not the newest, 9,000

    async def test_a_recap_started_over_counts_in_the_month_it_was_started(self) -> None:
        await self.lines(SESSION)
        await self.written(SESSION)
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET created_at = 1 WHERE guild_id = 1")
        october = float(dt.datetime(2026, 10, 10, 12, tzinfo=dt.timezone.utc).timestamp())  # noqa: UP017
        self.assertEqual((await self.store.used_in_month(1, self.one.id, october)).recaps, 0)
        await self.again(replace_ok=True)
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET restarted_at = %s", (int(october),))
            await conn.execute("UPDATE recap_jobs SET state = 'ready', title = 'x'")
        self.assertEqual((await self.store.used_in_month(1, self.one.id, october)).recaps, 1)

    async def test_the_overview_says_what_each_session_has(self) -> None:
        await self.written(1_000, posted=True)
        await self.written(2_000, approved=True)
        await self.store.enqueue(1, self.one.id, 3_000, dm_user_id=fx.DM)
        got = await self.store.overview(1, self.one.id)
        self.assertEqual(got[1_000], ("ready", "posted", True))
        self.assertEqual(got[2_000], ("ready", "draft", True))
        self.assertEqual(got[3_000], ("queued", "draft", False))
        self.assertTrue(await self.store.others_working(1, self.one.id, 1_000))
        self.assertFalse(await self.store.others_working(1, self.one.id, 3_000))

    def test_the_session_picker_words(self) -> None:
        from dmbot.ui.recap_again import session_line

        self.assertEqual(session_line(None, None, False), "No recap yet")
        self.assertEqual(session_line("ready", "posted", True), "Posted")
        self.assertEqual(session_line("failed", "draft", False), "Failed")
        self.assertEqual(session_line("beats", "draft", False), "Being written")
        self.assertEqual(NOW, NOW)
