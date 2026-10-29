"""The recap job (#1382) against a real database: queued once, leased to one worker, resumed
where it stopped, retried, failed plainly, restarted when a player stops being recorded, kept per
campaign, in backups, and gone with the campaign."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Collection
from itertools import pairwise
from typing import Any
from unittest.mock import patch

from dmbot.ai import AIError, AIOutOfFunds, Reply
from dmbot.campaigns.models import CampaignError
from dmbot.campaigns.store import CampaignStore
from dmbot.recaps import beats as bt
from dmbot.recaps import critics as cr
from dmbot.recaps import draft as rd
from dmbot.recaps import fixed_lines
from dmbot.recaps import pipeline as pl
from dmbot.recaps import significance as sg
from dmbot.recaps import split as sp
from dmbot.recaps.approval import text_hash
from dmbot.recaps.fixed_lines import STORY_COLUMN_MAX
from dmbot.recaps.store import Job, RecapSection, RecapStore
from dmbot.recaps.text import CUE
from dmbot.transcript.models import Line
from tests import recap_fixtures as fx
from tests import test_recap_significance as st_sig
from tests import test_recap_split as st
from tests.pg import DatabaseTest

NOW = 1_800_000_000


GOOD_GRADE = {"in_world": 5, "no_quotes": 5, "tension": 5, "ending": 5, "cues": 5}


class Clock:
    def __init__(self) -> None:
        self.now = float(NOW)

    def __call__(self) -> float:
        return self.now


class Base(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.one = await self.campaigns.create(1, "One", fx.DM)
        self.two = await self.campaigns.create(1, "Two", fx.DM)
        self.clock = Clock()
        self.store = RecapStore(self.db, clock=self.clock)


class Keeping(Base):
    async def test_one_job_for_each_session(self) -> None:
        self.assertTrue(await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM))
        self.assertFalse(await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM))
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(
            (job.state, job.status, job.tries, job.dm_user_id), ("queued", "draft", 0, fx.DM)
        )

    async def test_a_job_is_leased_to_one_worker_and_a_campaign_runs_one_at_a_time(self) -> None:
        await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM)
        await self.store.enqueue(1, self.one.id, 200, dm_user_id=fx.DM)
        await self.store.enqueue(1, self.two.id, 100, dm_user_id=fx.DM)
        due = await self.store.due(1)
        self.assertEqual(sorted(due), sorted([(self.one.id, 100), (self.two.id, 100)]))  # one each
        self.assertTrue(await self.store.lease(1, self.one.id, 100))
        self.assertFalse(await self.store.lease(1, self.one.id, 100))  # taken
        self.assertFalse(await self.store.lease(1, self.one.id, 200))  # same campaign: it waits
        self.assertEqual(await self.store.due(1), [(self.two.id, 100)])
        await self.store.release(1, self.one.id, 100)
        self.assertTrue(await self.store.lease(1, self.one.id, 200))

    async def test_a_lease_that_runs_out_frees_the_job(self) -> None:
        await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM)
        self.assertTrue(await self.store.lease(1, self.one.id, 100))
        self.clock.now += 20 * 60
        self.assertTrue(await self.store.lease(1, self.one.id, 100))

    async def test_a_job_that_must_wait_is_not_due_until_its_time(self) -> None:
        await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM)
        await self.store.update(1, self.one.id, 100, next_at=NOW + 60)
        self.assertEqual(await self.store.due(1), [])
        self.clock.now += 61
        self.assertEqual(await self.store.due(1), [(self.one.id, 100)])

    async def test_only_the_listed_columns_can_be_written(self) -> None:
        await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM)
        with self.assertRaises(ValueError):
            await self.store.update(1, self.one.id, 100, status="posted")
        with self.assertRaises(ValueError):
            await self.store.update(1, self.one.id, 100, **{"state = 'ready', status": "x"})

    async def test_a_job_is_only_ever_seen_in_its_own_server_and_campaign(self) -> None:
        await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM)
        self.assertIsNone(await self.store.get(2, self.one.id, 100))
        self.assertIsNone(await self.store.get(1, self.two.id, 100))
        self.assertEqual(await self.store.due(2), [])

    async def test_only_a_posted_recap_is_a_bridge_and_only_this_campaigns_own(self) -> None:
        for at in (100, 200):
            await self.store.enqueue(1, self.one.id, at, dm_user_id=fx.DM)
            await self.store.update(
                1, self.one.id, at, state="ready", summary=f"《Calm》 Recap {at}."
            )
        await self.store.enqueue(1, self.two.id, 150, dm_user_id=fx.DM)
        await self.store.update(1, self.two.id, 150, state="ready", summary="《Calm》 Other.")
        self.assertIsNone(await self.store.bridge(1, self.one.id, 300))  # nothing posted yet
        await self.mark_posted(self.one.id, 100)
        await self.mark_posted(self.two.id, 150)
        bridge = await self.store.bridge(1, self.one.id, 300)
        assert bridge is not None
        self.assertEqual(bridge.summary, "《Calm》 Recap 100.")
        self.assertIsNone(await self.store.bridge(1, self.one.id, 100))

    async def mark_posted(self, campaign: str, at: int) -> None:
        async with self.db.guild(1) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET status = 'posting'"
                " WHERE campaign_id = %s AND session_at = %s",
                (campaign, at),
            )
        self.assertTrue(await self.store.posted(1, campaign, at))
        self.assertFalse(await self.store.posted(1, campaign, at))  # once

    async def test_a_post_that_cannot_be_sent_goes_back_with_a_note(self) -> None:
        await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM)
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET status = 'posting'")
        await self.store.back_to_draft(1, self.one.id, 100, note="No channel.")
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertEqual((job.status, job.last_error), ("draft", "No channel."))

    async def test_calls_are_counted_for_the_day(self) -> None:
        await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM)
        await self.store.update(1, self.one.id, 100, calls=5, spent=5)
        self.assertEqual(await self.store.calls_since(1, self.one.id, NOW - 10), 5)
        self.assertEqual(await self.store.calls_since(1, self.two.id, NOW - 10), 0)
        self.clock.now += 100
        self.assertEqual(await self.store.calls_since(1, self.one.id, NOW + 50), 0)

    async def test_a_waiting_recaps_old_calls_do_not_stay_in_todays_tally(self) -> None:
        """#2308: five days of waiting, two calls a day: today's tally is the last day's two."""
        await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM)
        for _ in range(5):
            self.clock.now += 86400
            before = (await self.store.get(1, self.one.id, 100)).spent  # type: ignore[union-attr]
            await self.store.update(1, self.one.id, 100, spent=before + 2)
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.spent, 10)  # the lifetime total is kept
        self.assertEqual(
            await self.store.calls_since(1, self.one.id, int(self.clock.now) - 3600 * 23), 2
        )
        self.assertEqual(
            await self.store.calls_since(1, self.one.id, int(self.clock.now) - 3600 * 60), 6
        )

    async def test_stamps_older_than_two_days_are_dropped_and_other_campaigns_are_apart(
        self,
    ) -> None:
        await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM)
        await self.store.enqueue(1, self.two.id, 100, dm_user_id=fx.DM)
        await self.store.update(1, self.one.id, 100, spent=3)
        self.clock.now += 3 * 86400
        await self.store.update(1, self.one.id, 100, spent=4)
        async with self.db.guild(1) as conn:
            cur = await conn.execute(
                "SELECT cardinality(spent_log) AS n FROM recap_jobs WHERE campaign_id = %s",
                (self.one.id,),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 1)  # the three old stamps went; the new call is dated
        self.assertEqual(await self.store.calls_since(1, self.two.id, 0), 0)

    async def test_a_job_from_before_calls_were_dated_counts_by_its_total_while_recent(
        self,
    ) -> None:
        await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM)
        await self.store.update(1, self.one.id, 100, spent=7)
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET spent_log = '{}'")  # as it was before 0205
        self.assertEqual(await self.store.calls_since(1, self.one.id, NOW - 10), 7)
        self.clock.now += 3 * 86400
        self.assertEqual(await self.store.calls_since(1, self.one.id, int(self.clock.now)), 0)

    async def test_a_job_waiting_at_deploy_keeps_its_earlier_calls_when_it_gains_one(
        self,
    ) -> None:
        await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM)
        await self.store.update(1, self.one.id, 100, spent=7)
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET spent_log = '{}'")  # as it was before 0205
        self.clock.now += 60
        await self.store.update(1, self.one.id, 100, spent=8)
        self.assertEqual(await self.store.calls_since(1, self.one.id, NOW - 10), 8)

    async def test_a_requeue_keeps_the_days_dated_calls(self) -> None:
        await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM)
        await self.store.update(1, self.one.id, 100, spent=4)
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET state = 'failed'")  # only a failed one
        self.assertTrue(await self.store.requeue(1, self.one.id, 100))
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.spent, 0)
        self.assertEqual(await self.store.calls_since(1, self.one.id, NOW - 10), 4)

    async def test_it_goes_in_a_backup_and_comes_back_as_a_draft_never_posted(self) -> None:
        await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM)
        await self.store.update(
            1, self.one.id, 100, state="ready", title="T", summary="《Calm》 It began.",
            housekeeping=["a"], music=["m"], game_day=3,
        )  # fmt: skip
        await self.store.enqueue(1, self.one.id, 200, dm_user_id=fx.DM)  # not finished: not saved
        section = RecapSection()
        async with self.db.guild(1) as conn:
            rows = await section.dump(conn, 1, self.one.id)
        self.assertEqual([r["session_at"] for r in rows], [100])
        async with self.db.guild(1) as conn:
            await section.clear(conn, 1, self.one.id)
        self.assertIsNone(await self.store.get(1, self.one.id, 100))
        async with self.db.guild(1) as conn:
            await section.load(conn, 1, self.one.id, rows)
        back = await self.store.get(1, self.one.id, 100)
        assert back is not None
        self.assertEqual((back.state, back.status, back.title), ("ready", "draft", "T"))

    async def test_a_damaged_backup_is_refused(self) -> None:
        section = RecapSection()
        good = {
            "session_at": 1, "title": "T", "summary": "S", "housekeeping": [], "music": [],
            "game_day": None,
        }  # fmt: skip
        section.check([good])
        for bad in (
            {**good, "extra": 1},
            {**good, "session_at": 0},
            {**good, "title": ""},
            {**good, "summary": "x" * (STORY_COLUMN_MAX + 1)},
            {**good, "housekeeping": "no"},
            {**good, "game_day": -1},
            "nope",
        ):
            with self.assertRaises(CampaignError):
                section.check([bad])
        with self.assertRaises(CampaignError):
            section.check([good] * 51)

    async def test_it_goes_with_the_campaign(self) -> None:
        await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM)
        self.campaigns.register_section(RecapSection())
        await self.campaigns.delete(1, self.one.id)
        self.assertIsNone(await self.store.get(1, self.one.id, 100))


class Told:
    def __init__(self) -> None:
        self.jobs: list[Job] = []

    async def __call__(self, gid: int, cid: str, job: Job) -> None:
        self.jobs.append(job)


class WorkerHarness(Base):
    """The worker with canned answers. The fast client answers first tries, the careful one
    retries; each is given its answers in order."""

    async def setUpWorker(
        self,
        fast: fx.Canned,
        careful: fx.Canned | None = None,
        *,
        secret: Collection[str] = (),
        consenting: Any = None,
        allowed: bool = True,
        inputs: bool = True,
        names: Collection[str] = fx.NAMES,
        window_chars: int = 40_000,
        hours: float = 3.5,
        heard: str = "",
        campaign_name: str = "",
        lines: Any = None,
        earlier: Any = None,
        previous: str = "",
        story: Any = None,
        critics: bool = False,
        final_read: bool = False,
    ) -> None:
        self.fast = fast
        self.careful = careful or fx.Canned()
        self.told = Told()
        self.agreed = consenting or fx.everyone
        self.allowed = allowed
        self.secret = list(secret)

        async def load(gid: int, cid: str, job: Job) -> pl.Inputs | None:
            if not inputs:
                return None
            return pl.Inputs(
                lines=lines
                or ([*fx.lines(), Line(9000, fx.DM, heard, heard)] if heard else fx.lines()),
                dm_ids={fx.DM},
                allowed_names=list(names),
                secret_names=self.secret,
                previous=previous,
                earlier=earlier,
                day=4,
                window_chars=window_chars,
                hours=hours,  # the canned recap is 353 words: a 3.5-hour session's (#2013, #2368)
                campaign_name=campaign_name,
            )

        async def ok(gid: int, cid: str, job: Job) -> bool:
            return self.allowed

        self.worker = pl.RecapWorker(
            self.store,
            self.fast,
            self.careful,
            load=load,
            consenting=lambda gid: self.agreed,
            allowed=ok,
            notify=self.told,
            clock=self.clock,
            story=story,
            critics=critics,
            final_read=final_read,
        )
        await self.store.enqueue(1, self.one.id, 100, dm_user_id=fx.DM)

    async def go(self) -> Job:
        job = await self.worker.run(1, self.one.id, 100)
        assert job is not None
        return job

    def story_of(self, *memory: sg.Memory) -> Any:
        async def story(gid: int, cid: str, job: Job, seeds: Any) -> list[sg.Memory]:
            self.seeds = list(seeds)
            return list(memory)

        return story


class WaitingForNames(WorkerHarness):
    """#2628 review: a recap held for the name pass says so, never waits in silence, and goes
    on when the pass is done or after 30 minutes."""

    async def start(self, ready: list[bool]) -> None:
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER)
        )

        async def names_ready(gid: int, cid: str, job: Job) -> bool:
            return ready[0]

        self.worker._names_ready = names_ready

    async def test_it_waits_with_a_note_that_moves_and_then_goes_on(self) -> None:
        ready = [False]
        await self.start(ready)
        job = await self.go()
        self.assertEqual((job.state, job.calls, job.last_error), ("queued", 0, pl.CHECKING_NAMES))
        self.assertEqual(job.next_at, int(self.clock.now) + pl.NAMES_RETRY_S)
        self.assertEqual(self.fast.sent, [])  # no AI call while it waits
        first = job.updated_at
        self.clock.now += pl.NAMES_RETRY_S + 1
        job = await self.go()
        self.assertEqual(job.last_error, pl.CHECKING_NAMES)
        self.assertGreater(job.updated_at, first)  # each look moves the time the page shows
        ready[0] = True
        self.clock.now += pl.NAMES_RETRY_S + 1
        job = await self.go()
        self.assertEqual((job.state, job.last_error), ("ready", ""))  # the note went with the wait

    async def test_it_goes_on_without_the_pass_after_thirty_minutes(self) -> None:
        await self.start([False])
        job = await self.go()
        self.assertEqual(job.last_error, pl.CHECKING_NAMES)
        self.clock.now += pl.NAMES_WAIT_S + 1
        job = await self.go()
        self.assertEqual((job.state, job.last_error), ("ready", ""))

    async def test_a_check_that_cannot_be_read_never_holds_it_back(self) -> None:
        await self.start([False])

        async def broken(gid: int, cid: str, job: Job) -> bool:
            raise RuntimeError("the database blinked")

        self.worker._names_ready = broken
        with self.assertLogs("dmbot.recaps.pipeline", "ERROR"):
            job = await self.go()
        self.assertEqual(job.state, "ready")


class Working(WorkerHarness):
    """The worker with canned answers (see `WorkerHarness`)."""

    def happy(self) -> fx.Canned:
        return fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER)

    async def test_a_ready_recap_opens_and_closes_with_the_fixed_lines_outside_the_count(
        self,
    ) -> None:
        await self.setUpWorker(self.happy(), campaign_name="The Ember Road")
        job = await self.go()
        self.assertEqual(job.state, "ready")
        paragraphs = job.summary.split("\n\n")
        self.assertEqual(paragraphs[0], "Previously, on The Ember Road…")
        self.assertEqual(
            paragraphs[-1], "And so we continue, the epic adventure of The Ember Road."
        )
        self.assertEqual(fixed_lines.without(job.summary), fx.SUMMARY)  # the story is untouched
        self.assertEqual(job.original, job.summary)  # the DM's edit is compared with what they saw
        self.assertEqual(job.grade["tension"], 4)  # graded as the writer wrote it, before the lines

    async def test_a_secret_campaign_name_is_left_out_of_the_lines(self) -> None:
        await self.setUpWorker(
            self.happy(), campaign_name="The Ember Road", secret=["The Ember Road"]
        )
        job = await self.go()
        self.assertNotIn("Ember", job.summary)
        self.assertTrue(job.summary.startswith("Previously…"))
        self.assertTrue(job.summary.endswith("the epic adventure."))

    async def test_a_good_session_goes_all_the_way_and_the_dm_is_told_once(self) -> None:
        await self.setUpWorker(self.happy())
        job = await self.go()
        self.assertEqual((job.state, job.status), ("ready", "draft"))
        self.assertEqual(job.title, "The Silver Lock")
        self.assertEqual(job.housekeeping, ("Mira drew her blade.",))
        self.assertEqual(job.summary, job.original)
        self.assertEqual(job.game_day, 4)
        self.assertEqual(job.speakers, (fx.DM, fx.ALICE, fx.BOB))
        self.assertEqual(job.calls, 5)  # beats, prose, facts, grade, finish
        self.assertEqual(job.grade["tension"], 4)
        self.assertEqual(len(job.beats), 5)
        self.assertEqual([j.state for j in self.told.jobs], ["ready"])
        again = await self.worker.run(1, self.one.id, 100)  # nothing more to do
        assert again is None or again.finished
        self.assertEqual(len(self.told.jobs), 1)

    async def test_the_prose_is_written_from_the_beats_with_the_transcript_beside_them(
        self,
    ) -> None:
        await self.setUpWorker(self.happy())
        await self.go()
        prose_message = self.fast.sent[1]
        self.assertIn("<beats>", prose_message)  # the foundation (#2954)
        self.assertIn("<transcript>", prose_message)  # context, to explain who and what
        self.assertIn("<transcript>", self.fast.sent[0])

    async def test_a_restart_resumes_at_the_stage_it_stopped_in(self) -> None:
        # The first process finishes the beats and "dies" (its AI errors) in the prose stage.
        down = fx.Canned(fx.BEATS_ANSWER, AIError("down"))
        await self.setUpWorker(down)
        job = await self.go()
        self.assertEqual(job.state, "checked")
        self.assertEqual(job.errors, 1)
        self.assertGreater(job.next_at, NOW)
        # A new process, a little later, does only the rest: no second beats call.
        self.clock.now += 120
        await self.setUpWorkerAgain(fx.Canned(fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER))
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertEqual(len(self.fast.sent), 3)

    async def setUpWorkerAgain(self, fast: fx.Canned) -> None:
        told = self.told
        await self.setUpWorker(fast)
        self.told = told

    async def test_a_call_that_errors_waits_longer_each_time_and_fails_on_the_third(self) -> None:
        await self.setUpWorker(fx.Canned(AIError("x"), AIError("x"), AIError("x")))
        job = await self.go()
        self.assertEqual((job.state, job.errors, job.next_at - NOW), ("queued", 1, 60))
        self.assertIsNone(await self.worker.run(1, self.one.id, 100))  # not due yet
        self.clock.now += 61
        job = await self.go()
        self.assertEqual((job.errors, job.next_at - int(self.clock.now)), (2, 240))
        self.clock.now += 241
        job = await self.go()
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.last_error, pl.SERVICE)
        self.assertEqual([j.state for j in self.told.jobs], ["failed"])

    async def test_running_out_of_credit_fails_at_once_and_plainly(self) -> None:
        await self.setUpWorker(fx.Canned(AIOutOfFunds("no credit")))
        job = await self.go()
        self.assertEqual((job.state, job.last_error), ("failed", pl.FUNDS))
        self.assertNotIn("no credit", job.last_error)

    async def test_a_beat_sheet_that_fails_its_checks_is_asked_for_again_on_the_careful_tier(
        self,
    ) -> None:
        bad = '{"beats":[{"what":"A dragon came","who":[],"lines":[99],"kind":"event"}]}'
        await self.setUpWorker(fx.Canned(bad), fx.Canned(fx.BEATS_ANSWER))
        self.fast.answers += [fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER]
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertEqual(len(self.careful.sent), 1)
        self.assertIn("rejected", self.careful.sent[0])
        self.assertIn("only 0 usable", self.careful.sent[0])

    async def test_two_bad_beat_sheets_fail_the_job_without_inventing_a_story(self) -> None:
        bad = '{"beats":[{"what":"A dragon came","who":[],"lines":[99],"kind":"event"}]}'
        await self.setUpWorker(fx.Canned(bad), fx.Canned(bad))
        job = await self.go()
        self.assertEqual(job.state, "failed")
        self.assertIn(job.last_error, (pl.FAILED_CHECKS, pl.TOO_LITTLE))
        self.assertEqual(len(self.told.jobs), 1)

    async def test_prose_that_invents_a_name_is_rewritten_once_told_what_is_wrong(self) -> None:
        invented = fx.PROSE_ANSWER.replace("Nothing answered.", "Lord Varkus laughed.")
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(fx.PROSE_ANSWER),
        )
        self.fast.answers.insert(1, invented)
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertIn("Varkus", self.careful.sent[0])
        self.assertNotIn("Varkus", job.summary)

    async def test_prose_that_fails_three_times_fails_the_job(self) -> None:
        invented = fx.PROSE_ANSWER.replace("Nothing answered.", "Lord Varkus laughed.")
        await self.setUpWorker(fx.Canned(fx.BEATS_ANSWER, invented), fx.Canned(invented, invented))
        job = await self.go()
        self.assertEqual(job.state, "failed")
        self.assertTrue(job.last_error.startswith(pl.FAILED_CHECKS), job.last_error)
        self.assertIn("(check: ", job.last_error)
        self.assertEqual(job.summary, "")  # a failed draft is never kept

    async def test_a_recap_much_longer_than_the_target_is_used_whole_at_once(self) -> None:
        # Length is a target in the instructions, never a check (#2597): a 1-hour session aims at
        # about 150 words, and a 209-word answer is used as it came, with no retry and no note.
        long = fx.prose_answer(
            "《Tense》 " + " ".join(["calm"] * 208) + " Oskar", "1. Low strings."
        )
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, long, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(),
            hours=1.0,
        )
        notes: list[str] = []
        self.worker._on_note = notes.append
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertIn(" ".join(["calm"] * 20), job.summary)
        self.assertGreater(len(job.summary.split()), 200)
        self.assertEqual(self.careful.sent, [])  # no retry for its length
        self.assertEqual(notes, [])

    async def test_a_third_prose_try_happens_only_after_two_failures(self) -> None:
        invented = fx.PROSE_ANSWER.replace("Nothing answered.", "Lord Varkus laughed.")
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, invented, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(invented, fx.PROSE_ANSWER),
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertEqual(len(self.careful.sent), 2)  # the second and the third try
        self.assertIn("Varkus", self.careful.sent[1])  # told which name, on the retry only
        # the retry carries the rejected draft and the target, to revise (#2026)
        self.assertIn("<previous_draft>", self.careful.sent[1])
        self.assertIn("Lord Varkus laughed.", self.careful.sent[1])
        self.assertIn("about", self.careful.sent[1])
        self.assertNotIn("<previous_draft>", self.fast.sent[1])  # the first try has none

    async def test_the_prose_and_title_calls_ask_for_json_and_the_others_do_not(self) -> None:
        """#2039: the two text-form stages use structured outputs; the beat sheet and the grader
        are called as before."""
        from dmbot.recaps import draft as rd

        await self.setUpWorker(self.happy())
        job = await self.go()
        self.assertEqual(job.state, "ready")
        # fast client: beats, prose, grade, title and housekeeping
        self.assertEqual(self.fast.schemas, [None, rd.prose_schema(), None, rd.finish_schema()])
        self.assertEqual(job.title, "The Silver Lock")
        self.assertEqual(job.housekeeping, ("Mira drew her blade.",))

    async def test_a_cut_off_answer_is_retried_with_more_room(self) -> None:
        """#2039: a structured answer that hit the limit is not valid JSON; the retry says so and
        has half as much room again."""
        from dmbot.ai import Reply

        cut = Reply(fx.PROSE_ANSWER[:200], True, 10, 10, "m")
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, cut, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(fx.PROSE_ANSWER),
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertIn("cut off", self.careful.sent[0])
        self.assertGreater(self.careful.tokens[0], self.fast.tokens[1])
        self.assertEqual(self.careful.tokens[0], self.fast.tokens[1] * 3 // 2)

    async def test_a_declined_answer_is_asked_for_again_and_the_title_too(self) -> None:
        from dmbot.ai import Reply

        declined = Reply("", False, 10, 10, "m", refused=True)
        cut_title = Reply(fx.FINISH_ANSWER[:30], True, 10, 10, "m")
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, declined, fx.GRADE_ANSWER, cut_title),
            fx.Canned(fx.PROSE_ANSWER, fx.FINISH_ANSWER),
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertIn("declined", self.careful.sent[0])
        self.assertIn("cut off", self.careful.sent[1])
        self.assertEqual(self.careful.tokens[1], 900 * 3 // 2)

    async def test_an_answer_that_is_not_the_json_is_retried_with_the_form(self) -> None:
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, "Sorry, I cannot.", fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(fx.PROSE_ANSWER),
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertIn("sections JSON", self.careful.sent[0])

    async def test_a_name_said_at_the_table_that_no_list_has_is_kept_and_asked_about(self) -> None:
        """#2628: names are settled before the writer starts (PlotBot's name pass), so the recap
        no longer respells them. "Descending Stair" was said in the lines and no list has it, so
        it stays and is queued for the DM; that is not a reason to retry."""
        text = fx.PROSE_ANSWER.replace(
            "Nothing answered.", "Mira waited by the Descending Stair. Nothing answered."
        )
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, text, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            names=[*fx.NAMES, "Caer-Dineval"],
            heard="a stair descends into darkness; the Descending Stair",
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertIn("Descending Stair", job.summary)
        self.assertEqual(job.to_confirm, ("Descending Stair",))
        # Try again starts the notice over, so the new recap's names are queued too
        await self.store.set_names_state(1, self.one.id, 100, 1, only_from=0)
        await self.store.update(1, self.one.id, 100, state="queued", emailed_at=1)
        job = await self.go()  # the restart: a fresh recap, with the notice started over
        self.assertEqual((job.state, job.to_confirm, job.names_state), ("queued", (), 0))
        self.assertEqual(len(self.careful.sent), 0)  # no retry

    async def test_a_secret_name_never_reaches_the_prose_stage(self) -> None:
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER),
            fx.Canned(fx.PROSE_ANSWER, fx.PROSE_ANSWER),
            secret=["Oskar"],
            names=["Mira", "Greywater Keep"],  # the bot never offers a secret as a known name
        )
        job = await self.go()
        # The beats that name Oskar were cut, so the prose is told none of them...
        self.assertNotIn("Oskar", self.fast.sent[1])
        # ...and a draft that names him anyway is refused, three times, and nothing is kept.
        self.assertEqual((job.state, job.summary), ("failed", ""))
        self.assertIn("secret", self.careful.sent[0])
        # a rejected draft that names a secret is never sent back (#2026)
        for sent in self.careful.sent:
            self.assertNotIn("Oskar", sent)
            self.assertNotIn("<previous_draft>", sent)

    async def test_a_name_close_to_a_secret_one_never_reaches_a_ready_recap(self) -> None:
        """#2591, kept after #2628 stopped the recap respelling names: the writer says "Silas
        Vain" and the DM keeps "Silas Vane" secret. The draft is refused, nothing is kept, and the
        retry is not told the spelling."""
        text = fx.PROSE_ANSWER.replace("Nothing answered.", "Silas Vain waited. Nothing answered.")
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, text),
            fx.Canned(text, text),
            secret=["Silas Vane"],
            names=["Mira", "Greywater Keep"],
            heard="Silas Vain waited by the door.",
        )
        job = await self.go()
        self.assertNotEqual(job.state, "ready")
        self.assertNotIn("Silas Vain", job.summary)
        self.assertNotIn("Silas Vane", job.summary)
        for sent in self.careful.sent:
            self.assertNotIn("Silas Vain", sent)  # a rejected draft that near-names a secret
            self.assertNotIn("Silas Vane", sent)  # is never sent back, and no spelling is put in

    async def test_too_few_beats_left_after_the_cuts_fails_plainly(self) -> None:
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER), fx.Canned(fx.BEATS_ANSWER), secret=["Mira"]
        )
        job = await self.go()
        self.assertEqual((job.state, job.last_error), ("failed", pl.TOO_LITTLE))

    async def test_a_weak_grade_gets_one_rewrite_then_the_better_draft_is_kept(self) -> None:
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.WEAK_GRADE),
            fx.Canned(fx.PROSE_ANSWER),
        )
        self.fast.answers += [fx.WEAK_GRADE, fx.FINISH_ANSWER]
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertIn("<previous_draft>", self.careful.sent[0])  # the weak draft, to revise (#2026)
        self.assertIn("keep the stakes", self.careful.sent[0])
        self.assertEqual(job.grade["tension"], 2)  # recorded as it was, never hidden

    async def test_a_revision_that_fails_the_checks_leaves_the_first_draft_standing(self) -> None:
        # test 7, run7-off: the first draft passed but graded weak; its revision came back far too
        # short and the next try was cut off, and the whole recap failed (#2008)
        short = fx.prose_answer(
            "《Tense》 Lord Varkus waited.", "1. Low strings."
        )  # an invented name
        cut = Reply("{", cut=True)
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.WEAK_GRADE),
            fx.Canned(short, cut),
        )
        self.fast.answers += [fx.FINISH_ANSWER]
        notes: list[str] = []
        self.worker._on_note = notes.append
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertEqual(job.grade["tension"], 2)  # the first draft's own grade
        self.assertEqual(
            fixed_lines.without(job.summary), fx.SUMMARY
        )  # the first draft, not the revision
        self.assertEqual(self.worker._fallback, {})
        self.assertEqual(len(self.careful.sent), 1)  # one revision tried, not three
        self.assertTrue(any("first draft stands" in n for n in notes))

    async def test_a_cut_off_revision_leaves_the_first_draft_standing_too(self) -> None:
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.WEAK_GRADE),
            fx.Canned(Reply("{", cut=True)),
        )
        self.fast.answers += [fx.FINISH_ANSWER]
        job = await self.go()
        self.assertEqual((job.state, job.grade["tension"]), ("ready", 2))
        self.assertEqual(len(self.careful.sent), 1)

    async def test_a_declined_revision_leaves_the_first_draft_standing_too(self) -> None:
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.WEAK_GRADE),
            fx.Canned(Reply("", cut=False, refused=True)),
        )
        self.fast.answers += [fx.FINISH_ANSWER]
        job = await self.go()
        self.assertEqual((job.state, fixed_lines.without(job.summary)), ("ready", fx.SUMMARY))

    async def test_a_name_made_secret_between_the_tries_is_not_kept_in_the_first_draft(
        self,
    ) -> None:
        test = self

        class SecretAppears(fx.Canned):
            async def complete(self, *args: Any, **kwargs: Any) -> Reply:
                if args[0] == rd.GRADE_SYSTEM:  # between the first try and its revision
                    test.secret = ["Silver Lock"]  # the DM keeps it secret
                return await super().complete(*args, **kwargs)

        short = fx.prose_answer("《Tense》 Lord Varkus waited.", "1. Low strings.")
        await self.setUpWorker(
            SecretAppears(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.WEAK_GRADE), fx.Canned(short)
        )
        self.assertIn("silver lock", fx.SUMMARY)  # (the canned recap does say it)
        job = await self.go()
        self.assertNotEqual((job.state, job.summary), ("ready", fx.SUMMARY))
        self.assertNotIn("silver lock", job.summary)

    async def test_without_a_passing_first_draft_the_failing_tries_still_fail_the_job(self) -> None:
        short = fx.prose_answer("《Tense》 Lord Varkus waited.", "1. Low strings.")
        await self.setUpWorker(fx.Canned(fx.BEATS_ANSWER, short), fx.Canned(short, short))
        job = await self.go()
        self.assertEqual(job.state, "failed")
        self.assertTrue(job.last_error.startswith(pl.FAILED_CHECKS), job.last_error)
        self.assertIn("(check: ", job.last_error)

    async def test_a_grader_that_fails_never_stops_a_recap_that_passed_its_checks(self) -> None:
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, AIError("grader down"), fx.FINISH_ANSWER)
        )
        job = await self.go()
        self.assertEqual((job.state, job.grade), ("ready", {}))

    async def test_a_player_who_stops_being_recorded_while_it_is_written_restarts_it(self) -> None:
        calls = {"n": 0}

        async def flaky() -> Collection[int]:
            calls["n"] += 1
            # Fine before the beats call; Bob has stopped by the time the answer is back; and the
            # second pass reads only Alice and the DM.
            if calls["n"] == 3:
                return {fx.DM, fx.ALICE}
            return {fx.DM, fx.ALICE, fx.BOB} if calls["n"] < 3 else {fx.DM, fx.ALICE}

        await self.setUpWorker(fx.Canned(fx.BEATS_ANSWER, fx.BEATS_ANSWER), consenting=flaky)
        job = await self.go()
        self.assertEqual(job.restarts, 1)
        self.assertEqual(job.summary, "")
        self.assertNotIn(fx.BOB, job.speakers)
        second = self.fast.sent[1]
        self.assertNotIn("Oskar lights a torch", second)  # Bob's words are not sent again

    async def revoked_after(self, calls: int) -> Callable[[], Awaitable[Collection[int]]]:
        """Bob is recorded until the AI has been asked `calls` times, and has stopped after."""

        async def flaky() -> Collection[int]:
            if len(self.fast.sent) < calls:
                return {fx.DM, fx.ALICE, fx.BOB}
            return {fx.DM, fx.ALICE}

        return flaky

    async def test_a_player_who_stops_while_the_summary_is_written_is_left_out_of_it(self) -> None:
        """Consent re-checked after an await (CLAUDE.md, Hard rules, Consent): Bob says stop
        between the AI being asked for the summary and its answer coming back. The summary was
        written from his words, so it is thrown away and the recap starts again without them."""
        fast = fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.BEATS_ANSWER)
        await self.setUpWorker(fast, consenting=await self.revoked_after(2))
        job = await self.go()
        self.assertEqual(job.restarts, 1)
        self.assertEqual(job.summary, "")  # nothing written from his words is kept
        self.assertNotIn(fx.BOB, job.speakers)
        self.assertNotIn("Oskar lights a torch", fast.sent[2])  # and not sent again
        # The next call starts the beat sheet again; it does not grade or title the discarded text.
        self.assertEqual(fast.systems[2], fast.systems[0])

    async def test_a_player_who_stops_while_the_title_is_written_is_left_out_of_it(self) -> None:
        """Consent re-checked after an await (CLAUDE.md, Hard rules, Consent): Bob says stop
        after the summary is graded, while the title is being written. Nothing of the draft is
        kept, so the recap that is posted cannot carry his words."""
        fast = fx.Canned(
            fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER, fx.BEATS_ANSWER
        )
        await self.setUpWorker(fast, consenting=await self.revoked_after(4))
        job = await self.go()
        self.assertEqual(job.restarts, 1)
        self.assertEqual((job.summary, job.title), ("", ""))
        self.assertNotIn(fx.BOB, job.speakers)
        self.assertEqual(fast.systems[4], fast.systems[0])  # a new pass, not a posted title

    async def test_nobody_recorded_means_no_call_and_a_plain_reason(self) -> None:
        async def nobody() -> Collection[int]:
            return set()

        await self.setUpWorker(fx.Canned(), consenting=nobody)
        job = await self.go()
        self.assertEqual((job.state, job.last_error, job.calls), ("failed", pl.NOTHING, 0))
        self.assertEqual(self.fast.sent, [])

    async def test_a_plan_that_lacks_recaps_or_a_switch_that_is_off_stops_it_before_any_call(
        self,
    ) -> None:
        await self.setUpWorker(fx.Canned(), allowed=False)
        job = await self.go()
        self.assertEqual(job.state, "failed")
        self.assertEqual(self.fast.sent, [])

    async def test_names_that_cannot_be_read_mean_a_wait_not_a_guess(self) -> None:
        await self.setUpWorker(fx.Canned(), inputs=False)
        job = await self.go()
        self.assertEqual((job.state, job.errors), ("queued", 1))
        self.assertEqual(self.fast.sent, [])

    async def test_a_job_has_a_ceiling_on_calls(self) -> None:
        await self.setUpWorker(fx.Canned())
        await self.store.update(1, self.one.id, 100, calls=pl.MAX_CALLS)
        job = await self.go()
        self.assertEqual((job.state, job.last_error), ("failed", pl.CAP))

    async def test_a_campaign_has_a_ceiling_for_the_day(self) -> None:
        await self.setUpWorker(fx.Canned())
        await self.store.enqueue(1, self.one.id, 50, dm_user_id=fx.DM)
        await self.store.update(1, self.one.id, 50, state="ready", spent=pl.DAILY_CALLS)
        job = await self.go()
        self.assertEqual((job.state, job.last_error), ("failed", pl.DAILY))

    async def test_a_job_the_dm_has_decided_is_not_worked(self) -> None:
        await self.setUpWorker(fx.Canned())
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET status = 'dropped'")
        self.assertIsNone(await self.worker.run(1, self.one.id, 100))
        self.assertEqual(self.fast.sent, [])

    async def test_try_again_makes_a_fresh_recap(self) -> None:
        await self.setUpWorker(self.happy())
        await self.go()
        # The page's "Try again": back to queued, one try used.
        async with self.db.guild(1) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET tries = 1, state = 'queued', beats = '[]', attempt = 0,"
                " errors = 0, next_at = 0"
            )
        self.fast.answers += [fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER]
        job = await self.go()
        self.assertEqual((job.state, job.tries, job.calls), ("ready", 1, 5))
        self.assertEqual(len(self.told.jobs), 2)  # told again

    async def test_a_decided_recap_is_never_the_bridge_for_a_dropped_one(self) -> None:
        await self.setUpWorker(self.happy())
        await self.go()
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET status = 'dropped'")
        self.assertIsNone(await self.store.bridge(1, self.one.id, 10_000))

    async def test_try_again_keeps_the_days_tally(self) -> None:
        await self.setUpWorker(self.happy())
        await self.go()
        async with self.db.guild(1) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET tries = 1, state = 'queued', beats = '[]', attempt = 0,"
                " errors = 0, next_at = 0"
            )
        self.fast.answers += [fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER]
        job = await self.go()
        self.assertEqual((job.calls, job.spent), (5, 10))
        self.assertEqual(await self.store.calls_since(1, self.one.id, NOW - 10), 10)

    async def test_the_grader_stops_at_the_jobs_ceiling(self) -> None:
        await self.setUpWorker(fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER))
        await self.store.update(1, self.one.id, 100, calls=pl.MAX_CALLS - 2)
        await self.go()
        self.assertEqual(len(self.fast.sent), 2)  # beats and prose; no grader, no finish call

    async def test_a_grader_that_runs_out_of_funds_fails_the_job_at_once(self) -> None:
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, AIOutOfFunds("none left"))
        )
        job = await self.go()
        self.assertEqual((job.state, job.last_error), ("failed", pl.FUNDS))

    async def test_a_long_session_is_read_in_overlapping_windows_from_start_to_end(self) -> None:
        from dmbot.recaps import beats as bt

        rows = [(str(n), "Player", "line " + "x" * 300) for n in range(1, 201)]
        parts = bt.windows(rows)
        self.assertGreater(len(parts), 1)
        self.assertEqual(parts[0][0], rows[0])  # the start is read...
        self.assertEqual(parts[-1][-1], rows[-1])  # ...and so is the end
        seen = {r[0] for p in parts for r in p}
        self.assertEqual(seen, {r[0] for r in rows})  # every line is in some window
        for a, b in pairwise(parts):
            self.assertTrue({r[0] for r in a} & {r[0] for r in b})  # neighbours overlap
        self.assertEqual(bt.windows(rows[:3]), [rows[:3]])  # a short session is one window
        huge = [(str(n), "Player", "y" * 2000) for n in range(1, 2000)]
        self.assertLessEqual(
            len(bt.windows(huge)), bt.MAX_WINDOWS
        )  # bigger windows, not more calls
        self.assertEqual(bt.windows([]), [[]])

    async def test_each_window_is_one_call_and_the_beats_are_merged(self) -> None:
        from dmbot.memory.extractor import readable
        from dmbot.recaps import beats as bt

        size = 150
        rows = [(str(s.number), "x", s.text) for s in readable(fx.lines())]
        windows = len(bt.windows(rows, size))
        self.assertGreaterEqual(windows, 3)
        answers = [fx.BEATS_ANSWER] * windows
        await self.setUpWorker(
            fx.Canned(*answers, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            window_chars=size,
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertEqual((job.window_count, job.window_done), (windows, windows))
        self.assertEqual(len(job.beats), 5)  # the same beats found twice are one beat
        self.assertEqual(job.calls, windows + 4)
        first, last = self.fast.sent[0], self.fast.sent[windows - 1]
        self.assertIn("part 1 of", self.fast.systems[0])
        self.assertIn(f"part {windows} of {windows}", self.fast.systems[windows - 1])
        self.assertNotIn("silver lock and it clicks", first)  # a window holds only its part
        self.assertIn("something breathes below", last)  # and the last one reaches the end

    async def test_a_restart_in_the_middle_of_a_long_session_carries_on_at_the_next_window(
        self,
    ) -> None:
        await self.setUpWorker(fx.Canned(fx.BEATS_ANSWER, AIError("down")), window_chars=150)
        job = await self.go()
        self.assertEqual((job.state, job.window_done), ("queued", 1))
        self.clock.now += 120
        count = job.window_count
        told = self.told
        await self.setUpWorker(
            fx.Canned(
                *[fx.BEATS_ANSWER] * (count - 1), fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER
            ),
            window_chars=150,
        )
        self.told = told
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertIn("part 2 of", self.fast.systems[0])  # not the first window again

    async def test_try_again_clears_what_the_page_may_not_write(self) -> None:
        await self.setUpWorker(self.happy())
        await self.go()
        async with self.db.guild(1) as conn:  # the page sets only these two
            await conn.execute("UPDATE recap_jobs SET tries = 1, state = 'queued'")
        await self.store.update(1, self.one.id, 100, attempt=1, errors=2, next_at=NOW - 5)
        self.fast.answers += [fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER]
        job = await self.go()
        self.assertEqual((job.state, job.attempt, job.errors), ("ready", 0, 0))

    # --- the fact check (#2064) -------------------------------------------------------

    @staticmethod
    def sentence(fragment: str) -> int:
        """The number the fact check gives the canned recap's sentence holding `fragment`."""
        found = [i for i, t in enumerate(rd.sentences_of(fx.SUMMARY), 1) if fragment in t]
        assert len(found) == 1, found
        return found[0]

    async def flagged_then_clean(self, kind: str, fragment: str = "The torch burned") -> Job:
        """A first draft with one flagged sentence, a revision with none."""
        n = self.sentence(fragment)
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(fx.PROSE_ANSWER, facts=[fx.facts_answer([(n, kind)]), fx.CLEAN_FACTS]),
        )
        return await self.go()

    async def test_a_place_that_moved_is_flagged_and_revised_once(self) -> None:
        job = await self.flagged_then_clean("place")
        n = self.sentence("The torch burned")
        self.assertEqual(job.state, "ready")
        retry = self.careful.sent[0]
        self.assertIn(f"sentence {n} says something the beats do not say (place)", retry)
        self.assertIn("The torch burned down a little", retry)  # the draft's own text
        self.assertIn("<previous_draft>", retry)
        self.assertEqual(len(self.careful.fact_sent), 2)  # the first draft and its revision
        self.assertEqual(job.grade["faithful"], 5)  # the revision is clean
        self.assertEqual(fixed_lines.without(job.summary), fx.SUMMARY)

    async def test_an_added_direction_is_flagged_as_a_direction(self) -> None:
        await self.flagged_then_clean("direction")
        self.assertIn("(direction)", self.careful.sent[0])

    async def test_all_four_against_one_who_hid_his_is_a_contradiction(self) -> None:
        await self.flagged_then_clean("contradiction")
        self.assertIn("(contradiction)", self.careful.sent[0])

    async def test_the_fact_check_reads_the_beats_with_their_places_and_numbered_sentences(
        self,
    ) -> None:
        raw = dict(fx.BEATS["beats"][0], where="the keep's gate")
        beats = {"beats": [raw, *fx.BEATS["beats"][1:]]}
        import json

        await self.setUpWorker(
            fx.Canned(json.dumps(beats), fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER)
        )
        await self.go()
        asked = self.careful.fact_sent[0]
        self.assertIn("Where: the keep's gate.", asked)
        self.assertIn("[1] Dusk settled", asked)
        self.assertIn("Where: the keep's gate.", self.fast.sent[1])  # the writer sees it too

    async def test_a_sentence_still_flagged_after_the_revision_is_cut_in_code(self) -> None:
        n = self.sentence("The torch burned")
        flag = fx.facts_answer([(n, "invented")])
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(fx.PROSE_ANSWER, facts=[flag, flag]),
        )
        notes: list[str] = []
        self.worker._on_note = notes.append
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertNotIn("The torch burned down a little", job.summary)
        self.assertIn("Mira counted her breaths", job.summary)  # the rest stands
        self.assertEqual(len(self.careful.sent), 1)  # one revision, never two
        self.assertEqual(job.grade["faithful"], 4)  # one flag: not below the bar
        self.assertTrue(any("Cut 1 sentence" in x for x in notes))

    async def test_a_flagged_ending_is_cut_and_the_recap_still_stands(self) -> None:
        """#2741: the flagged sentence may be the one that ends the session. The recap loses it,
        and is not lost with it."""
        last = len(rd.sentences_of(fx.SUMMARY))
        flag = fx.facts_answer([(last, "invented")])
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(fx.PROSE_ANSWER, facts=[flag, flag]),
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertNotIn("Neither of them moved", job.summary)
        self.assertIn("Mira lifted her blade", job.summary)  # the rest stands
        self.assertEqual(job.last_error, "")

    async def test_several_flagged_sentences_cost_those_sentences_not_the_recap(self) -> None:
        wanted = [self.sentence(f) for f in ("The torch burned", "Time passed", "Nothing answered")]
        flag = fx.facts_answer([(n, "invented") for n in wanted])
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(fx.PROSE_ANSWER, facts=[flag, flag]),
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        for gone in ("The torch burned", "Time passed", "Nothing answered"):
            self.assertNotIn(gone, job.summary)
        self.assertIn("Mira counted her breaths", job.summary)
        self.assertIn("Mira lifted her blade", job.summary)

    async def test_a_cut_that_takes_the_ending_off_leaves_a_note_for_the_dm(self) -> None:
        """#2741: kept without its ending, the recap carries the note that says so."""
        from unittest.mock import patch

        last = len(rd.sentences_of(fx.SUMMARY))
        flag = fx.facts_answer([(last, "invented"), (last - 1, "invented")])
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(fx.PROSE_ANSWER, facts=[flag, flag]),
        )
        calls = iter([True, True])  # the two drafts end well; the draft left by the cut does not
        with patch.object(rd, "ends_on", side_effect=lambda *a, **k: next(calls, False)):
            job = await self.go()
        self.assertEqual((job.state, job.last_error), ("ready", ""))
        self.assertIn(pl.ENDING_CUT_NOTE, cr.loop_from_json(job.critics).notes)

    async def test_a_fact_check_that_cannot_be_had_marks_the_recap_not_fact_checked(self) -> None:
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(fx.PROSE_ANSWER, facts=["not json at all"]),
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")  # it never stops a recap that passed every check
        self.assertIn(pl.NOT_FACT_CHECKED_NOTE, cr.loop_from_json(job.critics).notes)

    async def test_a_recap_that_was_fact_checked_carries_no_such_note(self) -> None:
        await self.setUpWorker(self.happy())
        job = await self.go()
        self.assertEqual(cr.loop_from_json(job.critics).notes, ())

    async def test_a_hard_failure_after_a_cut_gives_the_dms_try_back_with_the_code(self) -> None:
        import sys
        from unittest.mock import patch

        n = self.sentence("The torch burned")
        flag = fx.facts_answer([(n, "invented")])
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(fx.PROSE_ANSWER, facts=[flag, flag]),
        )
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET tries = 1")
        real = rd.check_prose

        def secret_after_the_cut(*a: Any, **k: Any) -> list[str]:
            if sys._getframe(1).f_code.co_name == "_cut":
                return ["it names something the DM keeps secret: leave it out"]
            return list(real(*a, **k))

        with patch.object(rd, "check_prose", secret_after_the_cut):
            job = await self.go()
        self.assertEqual(job.state, "failed")
        self.assertIn("(check: secret)", job.last_error)
        self.assertEqual(job.tries, 0)  # our side's failure: the DM keeps their Try again

    async def test_a_speaker_who_stopped_during_the_fact_check_starts_the_recap_over(self) -> None:
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(fx.PROSE_ANSWER, facts=["not json at all"]),
        )
        real = self.careful.complete

        async def none_agree() -> Any:
            return frozenset()

        async def leaves_during_the_fact_check(*args: Any, **kw: Any) -> Any:
            answer = await real(*args, **kw)
            if args[0] in (rd.FACTS_SYSTEM, rd.FACTS_SYSTEM_TRANSCRIPT):
                self.agreed = none_agree  # a player stops while the check is being made
            return answer

        self.careful.complete = leaves_during_the_fact_check  # type: ignore[method-assign]
        job = await self.go()
        self.assertNotEqual(job.state, "ready")  # begun again, not stood "not fact-checked"
        self.assertNotIn(pl.NOT_FACT_CHECKED_NOTE, json.dumps(job.critics))

    async def test_a_revision_that_lost_its_ending_is_still_refused(self) -> None:
        """The critics' revision path: a better draft that has its ending is not replaced."""
        from unittest.mock import patch

        await self.setUpWorker(self.happy())
        prose = rd.Prose(fx.SUMMARY, ("1. Low strings.",) * len(CUE.findall(fx.SUMMARY)))
        flags = rd.Facts(((self.sentence("The torch burned"), "invented"),))
        inputs = pl.Inputs(
            lines=fx.lines(), dm_ids={fx.DM}, allowed_names=[], secret_names=[], previous="", day=4
        )
        ending = ["it must end exactly where the session stopped: the last beat"]
        with patch.object(rd, "check_prose", return_value=ending):
            cut, why = await self.worker._cut(
                prose, flags, [], inputs, "", rd.length_for(2.0), keep_unended=False
            )
        self.assertEqual((cut, why), (None, "ending"))

    def test_only_the_ending_may_be_left_after_a_cut(self) -> None:
        self.assertEqual(pl.hard_after_cut(["it must end exactly where the session stopped"]), [])
        secret = "it names something the DM keeps secret: leave it out"
        self.assertEqual(
            pl.hard_after_cut(["it must end exactly where the session stopped", secret]), [secret]
        )

    async def test_the_cut_keeps_every_check_but_the_ending_and_says_which_one_failed(self) -> None:
        from unittest.mock import patch

        await self.setUpWorker(self.happy())
        prose = rd.Prose(fx.SUMMARY, ("1. Low strings.",) * len(CUE.findall(fx.SUMMARY)))
        flags = rd.Facts(((self.sentence("The torch burned"), "invented"),))
        inputs = pl.Inputs(
            lines=fx.lines(), dm_ids={fx.DM}, allowed_names=[], secret_names=[], previous="", day=4
        )
        args: tuple[Any, ...] = (prose, flags, [], inputs, "", rd.length_for(2.0))
        ending = ["it must end exactly where the session stopped: the last beat"]
        with patch.object(rd, "check_prose", return_value=ending):
            cut, why = await self.worker._cut(*args)
        self.assertIsNotNone(cut)  # the ending alone does not end a recap
        self.assertEqual(why, "")
        secret = ["it names something the DM keeps secret: leave it out"]
        with patch.object(rd, "check_prose", return_value=[*ending, *secret]):
            cut, why = await self.worker._cut(*args)
        self.assertIsNone(cut)
        self.assertEqual(why, "secret")  # a fixed word, never the text

    async def test_a_revision_that_fails_its_checks_leaves_the_first_draft_with_cuts(self) -> None:
        n = self.sentence("The torch burned")
        short = fx.prose_answer("《Tense》 Lord Varkus waited.", "1. Low strings.")
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(short, facts=[fx.facts_answer([(n, "place")])]),
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertNotIn("The torch burned down a little", job.summary)
        self.assertIn("Mira lifted her blade", job.summary)
        self.assertEqual(self.worker._fallback, {})
        self.assertEqual(self.worker._flags, {})

    async def test_beats_the_recap_leaves_out_are_named_by_number_in_the_revision(self) -> None:
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(fx.PROSE_ANSWER, facts=[fx.facts_answer(missing=[2, 99]), fx.CLEAN_FACTS]),
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertIn("leaves out beat(s) 2, which change the story", self.careful.sent[0])
        self.assertNotIn("99", self.careful.sent[0])  # a number that names no beat is dropped

    async def test_a_fact_check_that_cannot_be_read_never_stops_a_recap(self) -> None:
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(facts=["not the answer asked for"]),
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertNotIn("faithful", job.grade)

    async def test_a_recap_below_the_faithful_bar_is_below_the_bar(self) -> None:
        self.assertEqual(
            rd.weak({**GOOD_GRADE, "faithful": 3}),
            ["say only what the beats say, in the place they say it"],
        )
        self.assertEqual(rd.weak({**GOOD_GRADE, "faithful": 4}), [])

    async def test_a_revision_whose_check_cannot_be_read_is_not_shipped_unchecked(self) -> None:
        n = self.sentence("The torch burned")
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(fx.PROSE_ANSWER, facts=[fx.facts_answer([(n, "place")]), "not json"]),
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertNotIn("The torch burned down a little", job.summary)  # first draft, cut

    async def test_the_fact_check_leaves_the_last_call_for_the_title(self) -> None:
        await self.setUpWorker(self.happy())
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        await self.store.update(1, self.one.id, 100, calls=pl.MAX_CALLS - 1)
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        sheet = [rd.Beat(1, "x", (), (1,), "event")]  # type: ignore[attr-defined]
        result = await self.worker._facts(1, self.one.id, job, sheet, rd.Prose("A b.", ()))
        self.assertEqual(result, (None, False))
        self.assertEqual(self.worker._careful.fact_sent, [])  # type: ignore[attr-defined]

    async def test_the_test_report_gets_no_sentence_of_the_recap(self) -> None:
        n = self.sentence("The torch burned")
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(fx.PROSE_ANSWER, facts=[fx.facts_answer([(n, "place")]), fx.CLEAN_FACTS]),
        )
        shown: list[str] = []
        self.worker._on_problems = lambda state, attempt, problems: shown.extend(problems)
        await self.go()
        self.assertEqual(shown, [f"sentence {n} says something the beats do not say (place)"])

    # --- compass directions caught in code (#2067) -----------------------------------

    DIRECTION_SUMMARY = fx.SUMMARY.replace(
        "The torch burned down a little.", "They rode north to the ferry."
    )

    def with_direction(self, *answers: str, facts: list[str]) -> tuple[fx.Canned, fx.Canned]:
        prose = fx.prose_answer(self.DIRECTION_SUMMARY)
        return (
            fx.Canned(fx.BEATS_ANSWER, prose, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(prose, *answers, facts=facts),
        )

    def direction_sentence(self) -> int:
        return next(
            i for i, t in enumerate(rd.sentences_of(self.DIRECTION_SUMMARY), 1) if "ferry" in t
        )

    async def test_a_direction_alone_causes_the_revision_and_then_the_cut(self) -> None:
        fast, careful = self.with_direction(facts=[fx.CLEAN_FACTS, fx.CLEAN_FACTS])
        await self.setUpWorker(fast, careful)
        job = await self.go()
        n = self.direction_sentence()
        self.assertEqual(job.state, "ready")
        self.assertIn(
            f"sentence {n} says something the beats do not say (direction)", careful.sent[0]
        )
        self.assertEqual(len(careful.fact_sent), 2)  # the draft and its one revision
        self.assertNotIn("north", job.summary.lower())
        self.assertIn("Mira counted her breaths", job.summary)

    async def test_the_code_flags_hold_when_the_fact_check_cannot_be_had(self) -> None:
        fast, careful = self.with_direction(facts=["not json", "not json"])
        await self.setUpWorker(fast, careful)
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertNotIn("north", job.summary.lower())
        self.assertEqual(len(careful.sent), 1)  # one revision, never two

    async def test_the_same_sentence_flagged_by_both_is_one_problem_line(self) -> None:
        n = self.direction_sentence()
        flag = fx.facts_answer([(n, "invented")])
        fast, careful = self.with_direction(facts=[flag, fx.CLEAN_FACTS])
        await self.setUpWorker(fast, careful)
        await self.go()
        retry = careful.sent[0]
        self.assertEqual(retry.count(f"sentence {n} says"), 1)
        self.assertIn("(invented)", retry)  # the AI's kind stands

    async def test_a_recap_with_no_direction_makes_the_same_calls_as_before(self) -> None:
        await self.setUpWorker(self.happy())
        job = await self.go()
        self.assertEqual(job.calls, 5)
        self.assertEqual(len(self.careful.fact_sent), 1)

    async def test_a_revision_whose_ai_check_failed_is_not_shipped_because_code_found_a_flag(
        self,
    ) -> None:
        n = self.sentence("The torch burned")
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(
                fx.prose_answer(self.DIRECTION_SUMMARY),
                facts=[fx.facts_answer([(n, "place")]), "not json"],
            ),
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertNotIn("ferry", job.summary)  # the revision was not shipped
        self.assertNotIn("The torch burned down a little", job.summary)  # first draft, cut

    # --- the split of the opening (#2253) ----------------------------------------------------

    def story(self) -> list[Line]:
        return st.reading([st.TALK, st.READ_ALOUD, [t for _, t in fx.LINES]])

    def fast_answers(self, split: str, beats: str) -> fx.Canned:
        return fx.Canned(split, beats, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER)

    async def test_the_retelling_is_context_and_never_a_beat(self) -> None:
        offset = len(st.TALK) + len(st.READ_ALOUD)
        # (the third beat is made only from the retelling, line 3: it is cut by the beat check)
        recap_only = {
            "what": "Goblins ambushed the caravan",
            "who": [],
            "lines": [4],
            "kind": "event",
        }
        fast = self.fast_answers(st.SPLIT_ANSWER, st.beats_after(offset, [recap_only]))
        await self.setUpWorker(
            fast, lines=self.story(), earlier=st.EARLIER, previous="", names=[*fx.NAMES]
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        split = sp.from_json(job.split)
        assert split is not None
        self.assertEqual([p.label for p in split.parts], ["talk", "recap", "play"])
        self.assertEqual(job.beats and len(job.beats), 5)  # the recap-only beat is cut
        self.assertNotIn("Goblins ambushed", json.dumps(job.beats))
        # the split call was the first, on the fast tier, asked for JSON of its own shape
        self.assertIn("<opening>", fast.sent[0])
        self.assertEqual(fast.schemas[0], sp.SCHEMA)
        # the beats' call: the play as the transcript, the retelling apart; the talk nowhere
        beats_call = fast.sent[1]
        self.assertIn("<last_time_recap>", beats_call)
        self.assertNotIn("pizza", beats_call)
        transcript = beats_call.split("<last_time_recap>")[0]
        self.assertNotIn("Redfall", transcript)
        self.assertIn("Greywater Keep at dusk", transcript)
        self.assertIn("<last_time_recap>", fast.sent[2])  # and the writer has it as context

    async def test_a_session_with_no_earlier_session_makes_no_split_call(self) -> None:
        fast = fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER)
        await self.setUpWorker(fast)  # earlier=None: the first session, or the first imported
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertNotIn("<opening>", fast.sent[0])
        split = sp.from_json(job.split)
        assert split is not None
        self.assertEqual(([p.label for p in split.parts], split.method), (["play"], "none"))

    async def test_an_answer_that_cannot_be_read_goes_on_as_all_play(self) -> None:
        fast = self.fast_answers("I cannot tell", st.beats_after(0))
        await self.setUpWorker(
            fast, lines=st.reading([[t for _, t in fx.LINES]]), earlier=st.EARLIER
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        split = sp.from_json(job.split)
        assert split is not None
        self.assertEqual(([p.label for p in split.parts], split.method), (["play"], "fallback"))

    async def test_an_unsure_retelling_is_played_and_marked(self) -> None:
        answer = json.dumps({"parts": [{"label": "recap", "last": 1}]})
        fast = self.fast_answers(answer, st.beats_after(0))
        await self.setUpWorker(
            fast, lines=st.reading([[t for _, t in fx.LINES]]), earlier=st.EARLIER
        )
        job = await self.go()
        split = sp.from_json(job.split)
        assert split is not None
        self.assertTrue(split.unsure)
        self.assertEqual(job.state, "ready")
        self.assertNotIn("<last_time_recap>", fast.sent[1])  # played, not set apart

    async def test_a_player_who_stops_being_recorded_during_the_split_starts_it_again(self) -> None:
        agreed = {fx.DM, fx.ALICE, fx.BOB}

        async def consenting() -> Any:
            return set(agreed)

        class Leaving(fx.Canned):
            async def complete(self, *a: Any, **k: Any) -> Any:
                agreed.discard(fx.ALICE)  # while the AI answers
                return await super().complete(*a, **k)

        fast = Leaving(st.SPLIT_ANSWER)
        await self.setUpWorker(fast, lines=self.story(), earlier=st.EARLIER, consenting=consenting)
        job = await self.go()
        self.assertEqual((job.state, job.restarts), ("queued", 1))
        self.assertIsNone(job.split)  # begun again, without them
        self.assertIsInstance(job, Job)

    # --- PlotBot marks what matters (#2254) --------------------------------------------------

    def amulet_lines(self) -> list[Line]:
        said = "Oskar wears an amulet with a shard of obsidian."
        extra = Line(8000, fx.DM, said, said)
        return [*fx.lines(), extra]

    def sig_answer(self, why: str, ties: list[str]) -> str:
        detail = {
            "line": 8,
            "what": "Oskar wears an amulet with a shard of obsidian",
            "why": why,
            "ties": ties,
        }
        return json.dumps({"notes": [], "details": [detail]})

    async def test_a_left_out_detail_that_ties_to_a_confirmed_thread_reaches_the_writer(
        self,
    ) -> None:
        fast = self.happy()
        careful = fx.Canned(
            self.sig_answer("The obsidian faction hunts the patron.", [st_sig.KNOWN.id])
        )
        await self.setUpWorker(
            fast, careful, lines=self.amulet_lines(), story=self.story_of(st_sig.KNOWN)
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        beats = bt.from_json(job.beats)
        detail = next(b for b in beats if 8 in b.lines)
        self.assertEqual(detail.kind, "state")
        self.assertIn("obsidian faction", detail.note)
        writer = fast.sent[1]
        # the note is for the writer alone: the fact check, grader and title step never see it
        others = [*fast.sent[2:], *fast.fact_sent]
        self.assertTrue(others)
        self.assertFalse(any("Matters:" in m for m in others))
        self.assertIn("amulet with a shard of obsidian", writer)
        self.assertIn("Matters: The obsidian faction hunts the patron.", writer)
        self.assertIn("Mira", self.seeds)  # the memory was asked about the beats' names
        # PlotBot's own call read the memory with each item marked for who may hear it
        self.assertIn("[players know this]", careful.sent[0])

    async def test_a_secrets_meaning_is_kept_but_never_explained_or_named(self) -> None:
        fast = self.happy()
        careful = fx.Canned(
            self.sig_answer("It marks the Obsidian Hand, who serve Vecnor.", [st_sig.SECRET.id])
        )
        await self.setUpWorker(
            fast,
            careful,
            lines=self.amulet_lines(),
            story=self.story_of(st_sig.SECRET),
            secret=["Vecnor", "Obsidian Hand"],
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        detail = next(b for b in bt.from_json(job.beats) if 8 in b.lines)
        self.assertTrue(detail.keep)
        self.assertEqual(detail.note, "")
        writer = fast.sent[1]
        self.assertIn("keep this detail; do not explain it", writer)
        # nothing of the secret reached the writer, the grader, the title step or the recap
        everything = "\n".join([*fast.sent, *fast.fact_sent, job.summary, *job.housekeeping])
        for word in ("Vecnor", "Hand", "serve"):
            self.assertNotIn(word, everything)
        self.assertIn("[DM only]", careful.sent[0])  # PlotBot itself may read it

    async def test_no_memory_and_no_recap_means_no_call(self) -> None:
        fast, careful = self.happy(), fx.Canned()
        await self.setUpWorker(fast, careful, story=self.story_of())
        job = await self.go()
        self.assertEqual((job.state, careful.sent), ("ready", []))

    async def test_with_no_memory_it_leans_on_the_accepted_recap(self) -> None:
        fast = self.happy()
        note = {"beat": 4, "why": "Mira searched the keep.", "ties": ["recap"]}
        careful = fx.Canned(json.dumps({"notes": [note], "details": []}))
        await self.setUpWorker(
            fast, careful, previous="Mira searched the keep.", story=self.story_of()
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertIn("<previous>", careful.sent[0])
        self.assertTrue(any(b.matters for b in bt.from_json(job.beats)))

    async def test_a_cited_line_outside_play_is_cut_and_the_recap_goes_on(self) -> None:
        fast = self.happy()
        careful = fx.Canned(self.sig_answer("Matters.", [st_sig.KNOWN.id]).replace("8", "99"))
        await self.setUpWorker(
            fast, careful, lines=self.amulet_lines(), story=self.story_of(st_sig.KNOWN)
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertFalse(any(8 in b.lines for b in bt.from_json(job.beats)))

    async def test_an_unreadable_answer_goes_on_without_notes(self) -> None:
        fast = self.happy()
        await self.setUpWorker(fast, fx.Canned("not json"), story=self.story_of(st_sig.KNOWN))
        self.assertEqual((await self.go()).state, "ready")

    async def test_a_memory_that_cannot_be_read_goes_on_without_notes(self) -> None:
        async def broken(*a: Any) -> Any:
            raise RuntimeError("memory down")

        fast2 = self.happy()
        await self.setUpWorker(fast2, fx.Canned(), story=broken)
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertFalse(any(b.matters for b in bt.from_json(job.beats)))

    async def test_a_player_who_stops_being_recorded_during_the_notes_starts_again(self) -> None:
        agreed = {fx.DM, fx.ALICE, fx.BOB}

        async def consenting() -> Any:
            return set(agreed)

        class Leaving(fx.Canned):
            async def complete(self, *a: Any, **k: Any) -> Any:
                agreed.discard(fx.BOB)
                return await super().complete(*a, **k)

        await self.setUpWorker(
            self.happy(),
            Leaving(self.sig_answer("x", [st_sig.KNOWN.id])),
            story=self.story_of(st_sig.KNOWN),
            consenting=consenting,
        )
        job = await self.go()
        self.assertEqual(job.restarts, 1)

    async def test_a_speaker_of_the_spoken_recap_who_leaves_during_the_notes_restarts_it(
        self,
    ) -> None:
        agreed = {fx.DM, fx.ALICE, fx.BOB}

        async def consenting() -> Any:
            return set(agreed)

        class Leaving(fx.Canned):
            async def complete(self, *a: Any, **k: Any) -> Any:
                agreed.discard(fx.ALICE)
                return await super().complete(*a, **k)

        # Alice speaks only in the spoken recap (lines 1-2), never in play
        said = st.READ_ALOUD[:2]
        recap = [Line(n * 1000, fx.ALICE, t, t) for n, t in enumerate(said, start=1)]
        play = [Line(n * 1000, user, t, t) for n, (user, t) in enumerate(fx.LINES, start=3)]
        lines = [*recap, *play]
        fast = fx.Canned(
            json.dumps({"parts": [{"label": "recap", "last": 2}]}),
            st.beats_after(2),
            fx.PROSE_ANSWER,
            fx.GRADE_ANSWER,
            fx.FINISH_ANSWER,
        )
        await self.setUpWorker(
            fast,
            Leaving(self.sig_answer("x", [st_sig.KNOWN.id])),
            lines=lines,
            earlier=st.EARLIER,
            story=self.story_of(st_sig.KNOWN),
            consenting=consenting,
        )
        job = await self.go()
        self.assertEqual(job.restarts, 1)

    async def test_the_notes_are_skipped_when_the_calls_are_nearly_used(self) -> None:
        careful = fx.Canned()
        await self.setUpWorker(self.happy(), careful, story=self.story_of(st_sig.KNOWN))
        with patch.object(pl, "SIG_RESERVE", 1000):
            job = await self.go()
        self.assertEqual((job.state, careful.sent), ("ready", []))

    async def test_split_errors_do_not_use_up_the_recaps_error_budget(self) -> None:
        # The split call errors twice, the beats call once: the recap still ends ready, because
        # the split's errors are cleared when it is stored (it is optional after two).
        split_down = AIError("down")
        fast = fx.Canned(
            split_down, split_down, AIError("down"), st.beats_after(0), fx.PROSE_ANSWER,
            fx.GRADE_ANSWER, fx.FINISH_ANSWER,
        )  # fmt: skip
        await self.setUpWorker(
            fast, lines=st.reading([[t for _, t in fx.LINES]]), earlier=st.EARLIER
        )
        job = None
        for _ in range(6):
            job = await self.worker.run(1, self.one.id, 100)
            self.clock.now += 1000
            if job is not None and job.finished:
                break
        assert job is not None
        self.assertEqual(job.state, "ready")
        split = sp.from_json(job.split)
        assert split is not None
        self.assertEqual(split.method, "fallback")  # done without the AI after two errors

    async def test_the_catch_up_goes_to_the_housekeeping_alone(self) -> None:
        catchup = "Reminder: we travel by the old Zebulon road next week."
        talk = st.TALK[0]
        play = [t for _, t in fx.LINES]
        lines = st.reading([[talk], [catchup], st.READ_ALOUD, play])
        answer = json.dumps(
            {"parts": [{"label": "talk", "last": 1}, {"label": "catchup", "last": 2},
                       {"label": "recap", "last": 2 + len(st.READ_ALOUD)}]}
        )  # fmt: skip
        offset = 2 + len(st.READ_ALOUD)
        fast = fx.Canned(
            answer, st.beats_after(offset), fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER
        )
        await self.setUpWorker(fast, lines=lines, earlier=st.EARLIER)
        job = await self.go()
        self.assertEqual(job.state, "ready")
        beats_call, writer, *_, housekeeping = fast.sent[1], fast.sent[2], *fast.sent[3:]
        self.assertNotIn("Zebulon", beats_call)
        self.assertNotIn("Zebulon", writer)
        self.assertIn("Zebulon", housekeeping)
        self.assertIn("<catchup>", housekeeping)
        self.assertNotIn("pizza", "\n".join(fast.sent[1:]))  # and the talk went nowhere


class RequireCurrent(Base):
    """`decide(..., "post", require_current=True)` posts only the text the DM approved, in the
    same statement as the post (#2545): an edit after approving, or a draft never approved, posts
    nothing."""

    async def sql(self, statement: str, *args: Any) -> None:
        async with self.db.guild(1) as conn:
            await conn.execute(statement, args)

    async def draft(self, campaign: str, at: int, title: str, summary: str) -> None:
        await self.store.enqueue(1, campaign, at, dm_user_id=fx.DM)
        await self.store.update(1, campaign, at, state="ready", title=title, summary=summary)

    async def approve(self, campaign: str, at: int, title: str, summary: str) -> None:
        await self.sql(
            "UPDATE recap_jobs SET approved_hash = %s WHERE guild_id = 1"
            " AND campaign_id = %s AND session_at = %s",
            text_hash(title, summary),
            campaign,
            at,
        )

    async def test_an_approval_of_the_text_as_it_is_now_posts(self) -> None:
        await self.draft(self.one.id, 100, "The vault", "Tessaly opens it.")
        await self.approve(self.one.id, 100, "The vault", "Tessaly opens it.")
        self.assertTrue(await self.store.decide(1, self.one.id, 100, "post", require_current=True))
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.status, "posting")

    async def test_an_edit_after_approving_posts_nothing(self) -> None:
        await self.draft(self.one.id, 100, "The vault", "Tessaly opens it.")
        await self.approve(self.one.id, 100, "The vault", "Tessaly opens it.")
        await self.store.update(1, self.one.id, 100, summary="Tessaly never opens it.")
        self.assertFalse(await self.store.decide(1, self.one.id, 100, "post", require_current=True))
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.status, "draft")

    async def test_a_draft_never_approved_posts_nothing(self) -> None:
        await self.draft(self.one.id, 100, "The vault", "Tessaly opens it.")
        self.assertFalse(await self.store.decide(1, self.one.id, 100, "post", require_current=True))
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.status, "draft")

    async def test_without_the_flag_the_same_edited_draft_still_posts(self) -> None:
        await self.draft(self.one.id, 100, "The vault", "Tessaly opens it.")
        await self.approve(self.one.id, 100, "The vault", "Tessaly opens it.")
        await self.store.update(1, self.one.id, 100, summary="Tessaly never opens it.")
        self.assertTrue(await self.store.decide(1, self.one.id, 100, "post"))

    async def test_another_campaigns_draft_is_untouched(self) -> None:
        await self.draft(self.one.id, 100, "The vault", "Tessaly opens it.")
        await self.approve(self.one.id, 100, "The vault", "Tessaly opens it.")
        await self.draft(self.two.id, 100, "The vault", "Tessaly opens it.")
        # Approved too, so the text check alone would not stop it: only the campaign filter does.
        await self.approve(self.two.id, 100, "The vault", "Tessaly opens it.")
        self.assertTrue(await self.store.decide(1, self.one.id, 100, "post", require_current=True))
        job = await self.store.get(1, self.two.id, 100)
        assert job is not None
        self.assertEqual(job.status, "draft")
