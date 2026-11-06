"""The recap posting clock (#2435): when a post or a narration began, its step and the posted
message's link, kept by the database so every writer of the status is covered. Against a real
database; the made-up Discord link uses invented numbers."""

from __future__ import annotations

import time
from typing import Any

from dmbot.recaps.store import Job
from tests import recap_fixtures as fx
from tests.test_recap_jobs import Base

LINK = "https://discord.com/channels/111/222/333"


class TheClock(Base):
    async def ready(self, at: int = 100, campaign: str | None = None) -> int:
        campaign = campaign or self.one.id
        await self.store.enqueue(1, campaign, at, dm_user_id=fx.DM)
        await self.store.update(
            1, campaign, at, state="ready", title="The Silver Lock", summary="Dusk fell."
        )
        return at

    async def job(self, at: int = 100, campaign: str | None = None) -> Job:
        found = await self.store.get(1, campaign or self.one.id, at)
        assert found is not None
        return found

    async def test_nothing_runs_before_a_post(self) -> None:
        at = await self.ready()
        job = await self.job(at)
        self.assertEqual((job.posting_since, job.posting_step, job.posted_url), (None, "", ""))

    async def test_pressing_post_starts_the_clock_at_check_in_whole_seconds(self) -> None:
        at = await self.ready()
        before = int(time.time())
        self.assertTrue(await self.store.decide(1, self.one.id, at, "post"))
        job = await self.job(at)
        self.assertEqual((job.status, job.posting_step), ("posting", "check"))
        assert job.posting_since is not None
        self.assertGreaterEqual(job.posting_since, before)
        self.assertLessEqual(job.posting_since, int(time.time()) + 1)

    async def test_the_step_moves_check_then_post_and_is_cleared_with_the_link_kept(self) -> None:
        at = await self.ready()
        await self.store.decide(1, self.one.id, at, "post")
        since = (await self.job(at)).posting_since
        await self.store.posting_step(1, self.one.id, at, "post")
        job = await self.job(at)
        self.assertEqual((job.posting_step, job.posting_since), ("post", since))  # same start
        self.assertTrue(await self.store.posted(1, self.one.id, at, url=LINK))
        job = await self.job(at)
        self.assertEqual(
            (job.status, job.posting_step, job.posting_since, job.posted_url),
            ("posted", "", None, LINK),
        )

    async def test_a_link_that_is_not_a_discord_message_is_not_kept(self) -> None:
        for bad in ("", "http://discord.com/channels/1/2/3", "https://example.com/channels/1/2/3"):
            at = await self.ready(at=200)
            await self.store.decide(1, self.one.id, at, "post")
            await self.store.posted(1, self.one.id, at, url=bad)
            self.assertEqual((await self.job(at)).posted_url, "", bad)
            async with self.db.guild(1) as conn:
                await conn.execute("DELETE FROM recap_jobs WHERE session_at = 200")

    async def test_a_failed_post_clears_the_step_and_keeps_the_error(self) -> None:
        at = await self.ready()
        await self.store.decide(1, self.one.id, at, "post")
        await self.store.posting_step(1, self.one.id, at, "post")
        await self.store.back_to_draft(1, self.one.id, at, note="Couldn't post it.")
        job = await self.job(at)
        self.assertEqual((job.status, job.posting_step, job.posting_since), ("draft", "", None))
        self.assertEqual(job.last_error, "Couldn't post it.")
        self.assertEqual(job.posted_url, "")

    async def test_the_step_only_moves_while_a_post_is_under_way(self) -> None:
        at = await self.ready()
        await self.store.posting_step(1, self.one.id, at, "post")  # a draft: nothing happens
        self.assertEqual((await self.job(at)).posting_step, "")
        with self.assertRaises(ValueError):
            await self.store.posting_step(1, self.one.id, at, "audio")

    async def test_posting_again_after_a_failure_starts_a_new_clock(self) -> None:
        at = await self.ready()
        await self.store.decide(1, self.one.id, at, "post")
        await self.store.back_to_draft(1, self.one.id, at, note="x")
        await self.store.decide(1, self.one.id, at, "post")
        self.assertEqual((await self.job(at)).posting_step, "check")

    async def test_dropping_a_draft_never_starts_it(self) -> None:
        at = await self.ready()
        await self.store.decide(1, self.one.id, at, "drop")
        self.assertEqual((await self.job(at)).posting_step, "")

    async def test_narration_runs_the_clock_with_the_audio_step_and_stops_it(self) -> None:
        at = await self.ready()
        await self.store.set_audio(1, self.one.id, at, audio="queued", tries=0, next_at=0)
        self.assertTrue(await self.store.audio_lease(1, self.one.id, at))
        job = await self.job(at)
        self.assertEqual(job.posting_step, "audio")
        self.assertIsNotNone(job.posting_since)
        await self.store.set_audio(1, self.one.id, at, audio="ready")
        job = await self.job(at)
        self.assertEqual((job.posting_step, job.posting_since), ("", None))

    async def test_narration_never_takes_over_or_ends_a_posts_clock(self) -> None:
        at = await self.ready()
        await self.store.decide(1, self.one.id, at, "post")
        await self.store.set_audio(1, self.one.id, at, audio="queued", tries=0, next_at=0)
        self.assertTrue(await self.store.audio_lease(1, self.one.id, at))
        job = await self.job(at)
        self.assertEqual(job.posting_step, "check")  # still the post's
        since = job.posting_since
        await self.store.set_audio(1, self.one.id, at, audio="failed", error="x")
        job = await self.job(at)
        self.assertEqual((job.posting_step, job.posting_since), ("check", since))

    async def test_a_narration_clock_ends_when_the_lease_is_released_even_for_a_dropped_recap(
        self,
    ) -> None:
        at = await self.ready()
        await self.store.set_audio(1, self.one.id, at, audio="queued", tries=0, next_at=0)
        await self.store.audio_lease(1, self.one.id, at)
        await self.store.decide(1, self.one.id, at, "drop")  # the DM gave up meanwhile
        # the worker's set_audio no longer matches a dropped recap...
        await self.store.set_audio(1, self.one.id, at, audio="failed")
        self.assertEqual((await self.job(at)).posting_step, "audio")
        # ...but releasing the lease, which has no such filter, ends the clock
        await self.store.release(1, self.one.id, at)
        job = await self.job(at)
        self.assertEqual((job.posting_step, job.posting_since), ("", None))

    async def test_try_again_ends_a_narration_clock_that_a_dead_lease_left(self) -> None:
        at = await self.ready()
        await self.store.set_audio(1, self.one.id, at, audio="queued", tries=0, next_at=0)
        await self.store.audio_lease(1, self.one.id, at)
        await self.store.reset_audio(1, self.one.id, at)
        self.assertEqual((await self.job(at)).posting_step, "")

    async def test_releasing_a_lease_never_ends_a_posts_clock(self) -> None:
        at = await self.ready()
        await self.store.decide(1, self.one.id, at, "post")
        await self.store.release(1, self.one.id, at)
        self.assertEqual((await self.job(at)).posting_step, "check")

    async def test_audio_that_ran_out_of_tries_stops_the_clock(self) -> None:
        at = await self.ready()
        await self.store.set_audio(1, self.one.id, at, audio="queued", tries=0, next_at=0)
        await self.store.audio_lease(1, self.one.id, at)
        self.clock.now += 100_000  # the lease is long over
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET audio_tries = 2")
        self.assertEqual(await self.store.fail_stuck_audio(1), 1)
        self.assertEqual((await self.job(at)).posting_step, "")

    async def test_each_campaigns_clock_is_its_own(self) -> None:
        a = await self.ready(100, self.one.id)
        b = await self.ready(100, self.two.id)
        await self.store.decide(1, self.one.id, a, "post")
        self.assertEqual((await self.job(a)).posting_step, "check")
        self.assertEqual((await self.job(b, self.two.id)).posting_step, "")

    async def test_the_columns_refuse_odd_values(self) -> None:
        at = await self.ready()
        import psycopg

        async with self.db.guild(1) as conn:
            for statement in (
                "UPDATE recap_jobs SET posting_step = 'later', posting_since = 5",
                "UPDATE recap_jobs SET posting_step = 'post'",  # a step needs its start
                "UPDATE recap_jobs SET posted_url = 'https://discord.com/x'",
            ):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    async with conn.transaction():
                        await conn.execute(statement)
        self.assertEqual((await self.job(at)).posted_url, "")


class TheWebRead(TheClock):
    async def test_the_website_read_selects_the_stored_clock(self) -> None:
        """The three columns reach the row the page's view is made from, so a stored value is not
        lost on the way (the keys in `_view` come with #2436)."""
        from dmbot.web import recaps as web_recaps

        at = await self.ready()
        await self.store.decide(1, self.one.id, at, "post")
        await self.store.posting_step(1, self.one.id, at, "post")
        async with self.db.guild(1) as conn:
            cur = await conn.execute(
                f"SELECT {web_recaps._COLUMNS} {web_recaps._FROM} WHERE r.campaign_id = %s",
                (self.one.id,),
            )
            row = await cur.fetchone()
        assert row is not None
        job = await self.job(at)
        self.assertEqual(
            (row["posting_since"], row["posting_step"], row["posted_url"]),
            (job.posting_since, "post", None),
        )


class ThePostingProgress(TheClock):
    """The posting half in the shared progress shape (#3355): the step, when it began and last
    moved, a stall, and a refusal as a code. Times and codes only."""

    async def test_nothing_under_way_and_nothing_refused_reads_none(self) -> None:
        from dmbot.recaps import progress as rp

        at = await self.ready()
        async with self.db.guild(1) as conn:
            cur = await conn.execute("SELECT * FROM recap_jobs WHERE session_at = %s", (at,))
            row = await cur.fetchone()
        assert row is not None
        self.assertIsNone(rp.posting_of(row, now=int(time.time())))

    async def test_a_post_under_way_shows_its_step_and_moves_with_each_step(self) -> None:
        from dmbot.recaps import progress as rp

        at = await self.ready()
        await self.store.decide(1, self.one.id, at, "post")
        await self.store.posting_step(1, self.one.id, at, "post")

        async def row() -> dict[str, Any]:
            async with self.db.guild(1) as conn:
                cur = await conn.execute("SELECT * FROM recap_jobs WHERE session_at = %s", (at,))
                found = await cur.fetchone()
            assert found is not None
            return dict(found)

        now = int(time.time())
        shown = rp.posting_of(await row(), now=now)
        assert shown is not None
        self.assertEqual(
            (shown["stage"], shown["state"], shown["reason"]), ("posting_post", "running", None)
        )
        self.assertLessEqual(shown["startedAt"], shown["updatedAt"])
        self.assertEqual(
            set(shown), {"stage", "startedAt", "updatedAt", "serverNow", "state", "reason"}
        )
        raw = await row()
        moved = max(int(raw["updated_at"]), int(raw["posting_since"]))  # (the store's clock)
        later = rp.posting_of(raw, now=moved + 6 * 60)  # five minutes with no move
        assert later is not None
        self.assertEqual((later["state"], later["reason"]), ("stalled", "stalled"))

    async def test_a_long_narration_that_keeps_moving_is_not_stalled(self) -> None:
        """The audio worker stamps `audio_moved_at` for each part, not `updated_at`: the posting
        half takes the later of the two, so a 16-minute narration with parts still finishing
        reads running."""
        from dmbot.recaps import progress as rp

        at = await self.ready()
        await self.store.decide(1, self.one.id, at, "post")
        await self.store.posting_step(1, self.one.id, at, "post")
        async with self.db.guild(1) as conn:
            cur = await conn.execute("SELECT * FROM recap_jobs WHERE session_at = %s", (at,))
            found = await cur.fetchone()
        assert found is not None
        raw = dict(found)
        raw["posting_step"] = "audio"
        since = int(raw["posting_since"])
        raw["updated_at"] = since
        raw["audio_moved_at"] = since + 15 * 60  # a part finished a minute ago
        busy = rp.posting_of(raw, now=since + 16 * 60)
        assert busy is not None
        self.assertEqual((busy["stage"], busy["state"]), ("posting_audio", "running"))
        raw["audio_moved_at"] = 0  # nothing finished in all that time
        quiet = rp.posting_of(raw, now=since + 16 * 60)
        assert quiet is not None
        self.assertEqual(quiet["state"], "stalled")

    async def test_a_refused_post_reads_failed_with_a_code_and_never_the_sentence(self) -> None:
        from dmbot.recaps import progress as rp

        at = await self.ready()
        await self.store.decide(1, self.one.id, at, "post")
        await self.store.back_to_draft(1, self.one.id, at, note=rp.POST_NOTES["no_table_chat"])
        async with self.db.guild(1) as conn:
            cur = await conn.execute("SELECT * FROM recap_jobs WHERE session_at = %s", (at,))
            found = await cur.fetchone()
        assert found is not None
        shown = rp.posting_of(dict(found), now=int(time.time()))
        assert shown is not None
        self.assertEqual((shown["state"], shown["reason"]), ("failed", "no_table_chat"))
        self.assertNotIn(rp.POST_NOTES["no_table_chat"], str(shown))

    def test_every_refusal_has_its_own_code_and_a_sentence_the_bot_says(self) -> None:
        from dmbot.recaps import progress as rp

        self.assertEqual(
            set(rp.POST_NOTES),
            {"plan", "no_table_chat", "cannot_check_secrets", "secret_name", "player_stopped",
             "send_failed", "post_failed"},
        )  # fmt: skip
        self.assertEqual(len(set(rp.POST_NOTES.values())), len(rp.POST_NOTES))  # codes read back
