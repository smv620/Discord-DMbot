"""The critics loop in the worker (#2255) against a real database: a recap is read by the motive
check and PlotBot, revised with their changes at once, and read again, at most four times."""

from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.ai import AIError, Reply
from dmbot.recaps import critics as cr
from dmbot.recaps import draft as rd
from dmbot.recaps import motives as mo
from dmbot.recaps import pipeline as pl
from dmbot.recaps import significance as sg
from dmbot.recaps.store import Job
from dmbot.transcript.models import Line
from tests import recap_fixtures as fx
from tests.test_recap_jobs import WorkerHarness

CLEAN = fx.SUMMARY.replace("to keep her hands from shaking", "as her hands shook")  # no purpose
MOTIVE = "Mira wanted to avenge her brother, who had fallen in the keep long ago."
WITH_MOTIVE = CLEAN.replace(
    "Mira drew her blade without a word,", f"{MOTIVE} Mira drew her blade without a word,", 1
)
MOTIVE_AT = rd.sentences_of(WITH_MOTIVE).index(MOTIVE) + 1
MEMORY = sg.Memory("f1" * 16, "The silver lock belongs to the Order of the Lantern.", True)
NO_NOTES = json.dumps({"notes": [], "details": []})
AGREE = json.dumps({"changes": []})


def change(sentence: int, kind: str, text: str = "x", *ties: str) -> str:
    item = {"sentence": sentence, "kind": kind, "change": text, "ties": list(ties)}
    return json.dumps({"changes": [item]})


class Smart(fx.Canned):
    """Canned answers, with the motive check's calls answered from the sentences they carry: by
    default "the sentences the word list knows state a reason"; `says` gives
    (sentence number -> (reason, lines)) for a reading, taken in order, or `raw` an answer as
    it is."""

    def __init__(self, *answers: str | Exception | Reply) -> None:
        super().__init__(*answers)
        self.says: list[dict[int, tuple[bool, list[int]]]] = []
        self.raw: list[str] = []
        self.always: str | None = None  # an answer to every motive call, once `raw` is spent
        self.down = False  # every motive call fails
        self.motive_sent: list[str] = []

    async def complete(self, system: str, text: str, **kw: Any) -> Reply:
        if system != mo.SYSTEM:
            return await super().complete(system, text, **kw)
        self.motive_sent.append(text)
        if self.down:
            raise AIError("down")
        if self.raw:
            return Reply(self.raw.pop(0), False, 10, 10, "m")
        if self.always is not None:
            return Reply(self.always, False, 10, 10, "m")
        said = self.says.pop(0) if self.says else {}
        block = text.split("<sentences>")[1].split("</sentences>")[0]
        asked = re.findall(r"^\[(\d+)\] (.*)$", block, re.MULTILINE)
        # by default the call agrees with the word list: a sentence that puts a purpose is a reason
        checks = [
            {
                "sentence": int(n),
                "reason": said.get(int(n), (mo.states_purpose(t), []))[0],
                "lines": said.get(int(n), (False, []))[1],
                "recap": False,
            }
            for n, t in asked
        ]
        return Reply(json.dumps({"checks": checks}), False, 10, 10, "m")


class Criticising(WorkerHarness):
    def fast_for(self, summary: str = CLEAN, *, grades: int = 1) -> Smart:
        prose = fx.prose_answer(summary)
        return Smart(fx.BEATS_ANSWER, prose, *([fx.GRADE_ANSWER] * grades), fx.FINISH_ANSWER)

    async def settle(self, job: Job, times: int = 6) -> Job:
        """Go on after the waits, as the live worker does."""
        for _ in range(times):
            if job.finished:
                break
            self.clock.now += 1000
            job = await self.go()
        return job

    async def test_off_by_default_no_critic_is_asked(self) -> None:
        fast = self.fast_for(WITH_MOTIVE)
        await self.setUpWorker(fast, fx.Canned())
        job = await self.go()
        self.assertEqual((job.state, fast.motive_sent, job.critics), ("ready", [], None))
        self.assertIn("avenge", job.summary)  # nothing read it

    async def test_a_draft_everyone_passes_goes_straight_on(self) -> None:
        fast = self.fast_for()
        await self.setUpWorker(fast, fx.Canned(), critics=True)
        job = await self.go()
        loop = cr.loop_from_json(job.critics)
        self.assertEqual((job.state, loop.done, loop.round, loop.notes), ("ready", True, 1, ()))
        self.assertEqual(len(fast.motive_sent), 1)  # every sentence was asked about, once
        self.assertEqual(job.calls, 6)  # the five of before and the motive check

    async def test_an_invented_motive_is_caught_and_fixed_in_the_second_round(self) -> None:
        fast = self.fast_for(WITH_MOTIVE, grades=2)
        careful = fx.Canned(fx.prose_answer(CLEAN))  # the revision
        await self.setUpWorker(fast, careful, critics=True)
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertNotIn("avenge", job.summary)
        self.assertEqual(len(fast.motive_sent), 2)  # the first draft, and the revision
        told = careful.sent[0]  # the writer gets the draft and what nobody said
        self.assertIn("<previous_draft>", told)
        self.assertIn("avenge her brother", told)
        self.assertIn("say only what the characters did", told)
        loop = cr.loop_from_json(job.critics)
        self.assertEqual((loop.done, loop.round, loop.notes, loop.cut), (True, 2, (), 0))

    async def test_a_motive_the_word_list_does_not_know_is_caught_by_the_call(self) -> None:
        text = "Mira stayed at the door, loyal to the last."
        draft = CLEAN.replace(
            "Mira drew her blade without a word,", f"{text} Mira drew her blade,", 1
        )
        at = rd.sentences_of(draft).index(text) + 1
        self.assertFalse(mo.states_purpose(text))
        fast = self.fast_for(draft, grades=2)
        fast.says = [{at: (True, [])}]  # the call says it states a reason, and nobody said it
        await self.setUpWorker(fast, fx.Canned(fx.prose_answer(CLEAN)), critics=True)
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertNotIn("loyal", job.summary)

    async def test_a_motive_somebody_said_is_not_a_failure(self) -> None:
        said = "Mira wants to avenge her brother, he fell at the old keep."
        lines = [*fx.lines(), Line(9000, fx.ALICE, said, said)]
        fast = self.fast_for(WITH_MOTIVE)
        await self.setUpWorker(fast, fx.Canned(), critics=True, lines=lines)
        fast.says = [{MOTIVE_AT: (True, [8])}]
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertIn("avenge", job.summary)  # kept: a player said it
        self.assertEqual(cr.loop_from_json(job.critics).round, 1)

    async def test_every_sentence_is_asked_about_even_when_there_are_more_than_one_call_holds(
        self,
    ) -> None:
        fast = self.fast_for()
        await self.setUpWorker(fast, fx.Canned(), critics=True)
        with patch.object(mo, "MAX_ASKED", 10):
            job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertEqual(len(fast.motive_sent), 3)  # 22 sentences, 10 at a time
        asked = [int(n) for t in fast.motive_sent for n in re.findall(r"^\[(\d+)\] ", t, re.M)]
        self.assertEqual(sorted(set(asked)), list(range(1, 23)))

    async def test_an_unreadable_motive_answer_is_never_agreement_the_recap_waits_for_its_check(
        self,
    ) -> None:
        fast = self.fast_for(WITH_MOTIVE, grades=2)
        fast.raw = ["not json", "still not json"]
        await self.setUpWorker(fast, fx.Canned(fx.prose_answer(CLEAN)), critics=True)
        job = await self.settle(await self.go(), 2)
        # not shipped (and not cut by a word list), not failed: it waits for one last check
        self.assertEqual((job.state, job.last_error), ("prose", pl.CHECK_WAIT))
        self.assertIn("avenge", job.summary)
        self.assertFalse(cr.loop_from_json(job.critics).done)
        self.assertEqual(len(fast.motive_sent), 2)  # asked again, never taken as agreement
        self.assertGreater(job.next_at, int(self.clock.now))
        # the service is back: the check reads the draft, the invented motive is fixed
        self.clock.now = job.next_at + 1
        job = await self.settle(await self.go(), 4)
        self.assertEqual((job.state, job.last_error), ("ready", ""))
        self.assertNotIn("avenge", job.summary)

    async def test_what_plotbot_says_matters_reaches_the_writer_and_the_revision_is_read(
        self,
    ) -> None:
        fast = self.fast_for(grades=2)
        careful = fx.Canned(
            NO_NOTES,  # the notes before the writing
            change(0, "missed", "say the silver lock belongs to the order", MEMORY.id),
            fx.prose_answer(CLEAN),  # the revision
            AGREE,  # the second reading
        )
        await self.setUpWorker(fast, careful, critics=True, story=self.story_of(MEMORY))
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertIn("say the silver lock belongs to the order", careful.sent[2])
        loop = cr.loop_from_json(job.critics)
        self.assertEqual((loop.done, loop.round, loop.notes), (True, 2, ()))

    async def test_an_unreadable_plotbot_answer_is_never_agreement(self) -> None:
        fast = self.fast_for()
        careful = fx.Canned(NO_NOTES, "not json", "not json")
        await self.setUpWorker(fast, careful, critics=True, story=self.story_of(MEMORY))
        job = await self.settle(await self.go())
        self.assertEqual(job.state, "ready")
        self.assertEqual(len(careful.sent), 3)  # the notes, and PlotBot asked twice
        loop = cr.loop_from_json(job.critics)
        self.assertEqual((loop.done, loop.round), (True, 0))  # no reading ever counted

    async def test_critics_that_never_agree_stop_at_four_readings_and_leave_notes(self) -> None:
        fast = self.fast_for(grades=4)
        ask = change(0, "weight", "elaborate considerably", MEMORY.id)
        rev = fx.prose_answer(CLEAN)
        careful = fx.Canned(NO_NOTES, ask, rev, ask, rev, ask, rev, ask)
        await self.setUpWorker(fast, careful, critics=True, story=self.story_of(MEMORY))
        job = await self.go()
        loop = cr.loop_from_json(job.critics)
        self.assertEqual((job.state, loop.done, loop.round), ("ready", True, cr.MAX_ROUNDS))
        self.assertEqual(len(loop.notes), 1)
        # for the DM's recap page: a thing to check, not an order
        self.assertEqual(loop.notes[0], cr.NOTES["weight"])
        self.assertEqual(len(careful.answers), 0)  # no fifth reading was paid for
        self.assertLessEqual(job.calls, pl.MAX_CALLS)

    async def test_a_secret_plotbot_finds_is_never_shipped(self) -> None:
        fast = self.fast_for(grades=4)
        ask = change(2, "secret", "it names the baron", MEMORY.id)
        rev = fx.prose_answer(CLEAN)
        careful = fx.Canned(NO_NOTES, ask, rev, ask, rev, ask, rev, ask)
        await self.setUpWorker(fast, careful, critics=True, story=self.story_of(MEMORY))
        sentences = rd.sentences_of(CLEAN)
        job = await self.go()
        self.assertEqual(job.state, "ready")
        loop = cr.loop_from_json(job.critics)
        self.assertEqual(loop.cut, 1)  # the sentence is cut in code
        self.assertNotIn(sentences[1], job.summary)
        self.assertIn(sentences[0], job.summary)
        self.assertNotIn("baron", json.dumps(job.critics))
        self.assertNotIn("baron", careful.sent[2])  # and the writer was never told what it hid

    async def test_a_secret_that_names_no_sentence_cannot_be_cut_so_the_job_fails(self) -> None:
        fast = self.fast_for(grades=4)
        ask = change(0, "secret", "it names the baron", MEMORY.id)
        rev = fx.prose_answer(CLEAN)
        careful = fx.Canned(NO_NOTES, ask, rev, ask, rev, ask, rev, ask)
        await self.setUpWorker(fast, careful, critics=True, story=self.story_of(MEMORY))
        job = await self.go()
        self.assertEqual((job.state, job.last_error), ("failed", pl.FAILED_CHECKS))

    async def test_a_revision_nobody_has_read_never_takes_the_place_of_one_plotbot_read(
        self,
    ) -> None:
        # Draft 1: a motive nobody said, and a secret PlotBot found. Its revision still has the
        # motive, and PlotBot then fails twice: draft 1 (read, worse) stands, cut, not the revision.
        second = WITH_MOTIVE.replace("Dusk settled", "Evening settled", 1)
        fast = self.fast_for(WITH_MOTIVE, grades=2)
        ask = change(1, "secret", "it names the baron", MEMORY.id)
        careful = fx.Canned(
            NO_NOTES, ask, fx.prose_answer(second), AIError("down"), AIError("down")
        )
        await self.setUpWorker(fast, careful, critics=True, story=self.story_of(MEMORY))
        job = await self.settle(await self.go())
        self.assertEqual(job.state, "ready")
        loop = cr.loop_from_json(job.critics)
        assert loop.best is not None
        self.assertEqual((loop.best.summary, loop.best.hard), (WITH_MOTIVE, 2))
        self.assertNotIn("Evening", job.summary)
        self.assertNotIn("avenge", job.summary)  # both of draft 1's hard sentences are cut
        self.assertEqual(loop.cut, 2)

    async def test_a_restart_between_readings_resumes_without_paying_again(self) -> None:
        fast = self.fast_for(WITH_MOTIVE, grades=2)
        careful = fx.Canned(AIError("down"))  # the revision's write fails once
        await self.setUpWorker(fast, careful, critics=True)
        job = await self.go()
        loop = cr.loop_from_json(job.critics)
        self.assertEqual((job.state, loop.round, job.errors), ("prose", 1, 1))
        self.assertEqual(len(fast.motive_sent), 1)
        # A new process, a little later: the first reading is not paid for again.
        self.clock.now += 300
        second = Smart(fx.GRADE_ANSWER, fx.FINISH_ANSWER)
        fresh = fx.Canned(fx.prose_answer(CLEAN))
        self.worker._fast, self.worker._careful = second, fresh
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertEqual(len(second.motive_sent), 1)  # only the revision is read
        self.assertNotIn("avenge", second.motive_sent[0])
        self.assertEqual(len(fresh.sent), 1)  # one revision
        self.assertNotIn("avenge", job.summary)

    async def test_a_failed_plotbot_call_does_not_make_the_next_try_pay_for_the_motive_check(
        self,
    ) -> None:
        fast = self.fast_for(WITH_MOTIVE, grades=2)
        careful = fx.Canned(NO_NOTES, AIError("down"))
        await self.setUpWorker(fast, careful, critics=True, story=self.story_of(MEMORY))
        job = await self.go()
        self.assertIsNotNone(cr.loop_from_json(job.critics).motives)
        self.clock.now += 300
        careful.answers[:] = [change(0, "missed"), fx.prose_answer(CLEAN), AGREE]
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertEqual(len(fast.motive_sent), 2)  # the first draft once, the revision once

    async def test_plotbot_failing_after_the_motive_check_still_cuts_a_motive_nobody_said(
        self,
    ) -> None:
        fast = self.fast_for(WITH_MOTIVE)
        careful = fx.Canned(NO_NOTES, AIError("down"), AIError("down"))
        await self.setUpWorker(fast, careful, critics=True, story=self.story_of(MEMORY))
        job = await self.settle(await self.go())
        self.assertEqual((job.state, job.last_error), ("ready", ""))
        self.assertNotIn("avenge", job.summary)  # found by the motive check, cut, never shipped
        self.assertEqual(len(fast.motive_sent), 1)  # and never paid for twice
        self.assertEqual(cr.loop_from_json(job.critics).cut, 1)

    async def test_critics_that_never_agree_about_a_motive_cut_it_and_stop_at_four(self) -> None:
        fast = self.fast_for(WITH_MOTIVE, grades=4)
        careful = fx.Canned(*([fx.prose_answer(WITH_MOTIVE)] * 3))
        await self.setUpWorker(fast, careful, critics=True)
        job = await self.go()
        loop = cr.loop_from_json(job.critics)
        self.assertEqual((job.state, loop.round, loop.cut), ("ready", cr.MAX_ROUNDS, 1))
        self.assertNotIn("avenge", job.summary)
        self.assertEqual(len(fast.motive_sent), cr.MAX_ROUNDS)

    async def test_the_style_note_is_left_when_the_critics_stop_without_agreeing_too(self) -> None:
        down = AIError("down")  # the grader fails on the draft and on each revision
        fast = Smart(fx.BEATS_ANSWER, fx.prose_answer(WITH_MOTIVE), down, down, down, down)
        fast.answers.append(fx.FINISH_ANSWER)
        careful = fx.Canned(*([fx.prose_answer(WITH_MOTIVE)] * 3))
        await self.setUpWorker(fast, careful, critics=True)
        job = await self.settle(await self.go())
        loop = cr.loop_from_json(job.critics)
        self.assertEqual((job.state, loop.round), ("ready", cr.MAX_ROUNDS))  # no agreement
        self.assertIn(cr.STYLE_UNCHECKED, loop.notes)

    async def test_a_service_failing_after_the_draft_was_read_ships_it_with_only_the_calls_cuts(
        self,
    ) -> None:
        fast = self.fast_for(WITH_MOTIVE)
        careful = fx.Canned(AIError("down"), AIError("down"), AIError("down"))
        await self.setUpWorker(fast, careful, critics=True)
        job = await self.settle(await self.go(), 4)
        self.assertEqual((job.state, job.last_error), ("ready", ""))
        self.assertTrue(cr.loop_from_json(job.critics).done)
        self.assertNotIn("avenge", job.summary)  # the hard failure was cut, not shipped

    async def test_the_ceilings_hold_and_a_job_at_its_ceiling_is_neither_shipped_nor_cut(
        self,
    ) -> None:
        self.assertEqual((pl.MAX_CALLS, pl.DAILY_CALLS), (95, 280))
        fast = self.fast_for(WITH_MOTIVE)
        await self.setUpWorker(fast, fx.Canned(), critics=True)
        with patch.object(pl, "MAX_CALLS", 9):  # the loop keeps what the title step needs
            job = await self.go()
        # waiting would never change the job's own ceiling: it stops with the "limit" words
        self.assertEqual((job.state, job.last_error), ("failed", pl.CAP))
        self.assertLessEqual(job.calls, 9)
        self.assertEqual(fast.motive_sent, [])  # never read, so nothing was asked or cut

    async def test_when_the_day_is_used_up_the_recap_waits_then_ships_uncut_once_checked(
        self,
    ) -> None:
        plain = CLEAN.replace(
            "Mira drew her blade without a word,",
            "During the fight Oskar fell. They climbed out of the pit. Mira tried the door. "
            "Mira drew her blade without a word,",
            1,
        )
        fast = self.fast_for(plain)
        await self.setUpWorker(fast, fx.Canned(), critics=True)
        tally = AsyncMock(return_value=pl.DAILY_CALLS - 3)  # room for the title, no more
        with patch.object(self.store, "calls_since", tally):
            job = await self.go()
        self.assertEqual((job.state, job.last_error), ("prose", pl.CHECK_WAIT))  # waits
        self.assertEqual(fast.motive_sent, [])  # the loop did not begin
        self.assertGreaterEqual(job.next_at - int(self.clock.now), pl.CHECK_TALLY_RETRY_S)
        # the tally has gone down: the check answers "no reason" for each sentence, nothing is cut
        self.clock.now = job.next_at + 1
        job = await self.go()
        self.assertEqual((job.state, job.last_error), ("ready", ""))
        for kept in (
            "During the fight Oskar fell.",
            "They climbed out of the pit.",
            "tried the door",
        ):
            self.assertIn(kept, job.summary)
        self.assertEqual(cr.loop_from_json(job.critics).cut, 0)

    async def test_a_grader_that_failed_leaves_a_note_for_the_dm(self) -> None:
        fast = Smart(fx.BEATS_ANSWER, fx.prose_answer(CLEAN), AIError("down"), fx.FINISH_ANSWER)
        await self.setUpWorker(fast, fx.Canned(), critics=True)
        job = await self.settle(await self.go())
        self.assertEqual(job.state, "ready")
        self.assertIn(cr.STYLE_UNCHECKED, cr.loop_from_json(job.critics).notes)

    async def test_a_draft_with_a_purpose_in_it_is_not_shipped_or_cut_while_it_waits(self) -> None:
        fast = self.fast_for(WITH_MOTIVE)
        await self.setUpWorker(fast, fx.Canned(), critics=True)
        tally = AsyncMock(return_value=pl.DAILY_CALLS - 3)
        with patch.object(self.store, "calls_since", tally):
            job = await self.go()
        self.assertEqual((job.state, job.last_error), ("prose", pl.CHECK_WAIT))
        self.assertIn("avenge", job.summary)  # waiting for the call that can tell, not cut
        self.assertEqual(job.critics["round"], 0)  # the loop has not read anything

    async def drive(self, job: Job, cycles: int) -> Job:
        """Let the recap try again each time it is due, `cycles` times (an outage lasts)."""
        for _ in range(cycles):
            if job.finished:
                break
            self.clock.now = max(self.clock.now, job.next_at) + 1
            job = await self.worker.run(1, self.one.id, 100) or job
        return job

    async def test_an_outage_waits_without_failing_or_spending_the_jobs_calls(self) -> None:
        fast = self.fast_for(WITH_MOTIVE, grades=2)
        fast.down = True
        await self.setUpWorker(fast, fx.Canned(fx.prose_answer(CLEAN)), critics=True)
        told = AsyncMock()
        self.worker._unchecked = told
        job = await self.drive(await self.go(), 6)  # the first holds
        calls_at_first_hold, started = job.calls, self.clock.now
        self.assertEqual((job.state, job.last_error), ("prose", pl.CHECK_WAIT))
        first = cr.loop_from_json(job.critics)
        spent_then = job.spent
        # not told before three days: drive until just short of them
        while self.clock.now - started < 2.5 * 86400:
            job = await self.drive(job, 1)
        told.assert_not_awaited()
        self.assertEqual((job.state, job.last_error), ("prose", pl.CHECK_WAIT))  # still waiting
        self.assertEqual(job.calls, calls_at_first_hold)  # not the job's own calls: no CAP
        loop = cr.loop_from_json(job.critics)
        # the waiting tries had their own budget: two calls for each wait, no more
        self.assertLessEqual(job.spent - spent_then, cr.WAIT_CALLS * (loop.waits - first.waits))
        self.assertIn("avenge", job.summary)  # nothing cut, nothing shipped
        # the backoff: 15 minutes, 1 hour, 4 hours, then every 12 hours
        self.assertEqual([cr.wait_for(n) for n in range(5)], [900, 3600, 14400, 43200, 43200])
        # after three days unchecked the DM is told, once, in the fixed words
        while self.clock.now - started < 3.5 * 86400:
            job = await self.drive(job, 1)
        job = await self.drive(job, 6)
        told.assert_awaited_once()
        self.assertTrue(cr.loop_from_json(job.critics).told)
        self.assertIn("3 days", cr.UNCHECKED_TOLD)
        self.assertIn("you'll be told when it's ready", cr.UNCHECKED_TOLD)
        # the service answers: the check reads the draft, the invented motive is fixed, and the
        # waiting state is cleared
        fast.down = False
        job = await self.drive(job, 6)
        self.assertEqual((job.state, job.last_error), ("ready", ""))
        self.assertNotIn("avenge", job.summary)
        done = cr.loop_from_json(job.critics)
        self.assertEqual((done.waits, done.since, done.told), (0, 0, False))
        told.assert_awaited_once()

    async def test_a_long_recap_is_checked_in_every_waiting_try_too(self) -> None:
        fast = self.fast_for(WITH_MOTIVE, grades=2)
        fast.down = True
        await self.setUpWorker(fast, fx.Canned(fx.prose_answer(CLEAN)), critics=True)
        with patch.object(mo, "MAX_ASKED", 10):  # three batches for this recap
            job = await self.drive(await self.go(), 6)
            self.assertEqual((job.state, job.last_error), ("prose", pl.CHECK_WAIT))
            self.assertEqual(cr.wait_budget(3), 4)  # the batches and PlotBot
            fast.down = False
            before = len(fast.motive_sent)
            job = await self.drive(job, 6)
        self.assertEqual((job.state, job.last_error), ("ready", ""))
        self.assertGreaterEqual(len(fast.motive_sent) - before, 3)  # every batch was read
        self.assertNotIn("avenge", job.summary)

    async def test_answers_that_keep_coming_back_incomplete_wait_within_their_budget(self) -> None:
        fast = self.fast_for(WITH_MOTIVE, grades=2)
        fast.always = json.dumps({"checks": []})  # every sentence left out
        await self.setUpWorker(fast, fx.Canned(fx.prose_answer(CLEAN)), critics=True)
        job = await self.drive(await self.go(), 40)
        self.assertEqual((job.state, job.last_error), ("prose", pl.CHECK_WAIT))
        self.assertLess(job.calls, pl.MAX_CALLS)  # waiting never spends the job's own calls
        self.assertIn("avenge", job.summary)
        fast.always = None  # the call answers properly again
        job = await self.drive(job, 6)
        self.assertEqual(job.state, "ready")
        self.assertNotIn("avenge", job.summary)

    async def test_an_answer_that_leaves_the_motive_sentence_out_is_asked_again(self) -> None:
        fast = self.fast_for(WITH_MOTIVE, grades=2)
        left_out = [
            {"sentence": n, "reason": False, "lines": [], "recap": False}
            for n in range(1, len(rd.sentences_of(WITH_MOTIVE)) + 1)
            if n != MOTIVE_AT
        ]
        fast.raw = [json.dumps({"checks": left_out})]
        await self.setUpWorker(fast, fx.Canned(fx.prose_answer(CLEAN)), critics=True)
        job = await self.settle(await self.go(), 6)
        self.assertGreaterEqual(len(fast.motive_sent), 2)  # asked again, not taken as "no reason"
        self.assertNotIn("avenge", job.summary)  # and the invented motive did not ship

    async def test_a_speaker_who_stops_between_the_replies_begins_the_loop_again(self) -> None:
        agreed = {fx.DM, fx.ALICE, fx.BOB}

        async def consenting() -> set[int]:
            return set(agreed)

        class Leaving(fx.Canned):
            async def complete(self, system: str, text: str, **kw: Any) -> Reply:
                if system == cr.plot_system():
                    agreed.discard(fx.ALICE)  # while PlotBot answers
                return await super().complete(system, text, **kw)

        fast = self.fast_for(WITH_MOTIVE)
        careful = Leaving(NO_NOTES, AGREE)
        await self.setUpWorker(
            fast, careful, critics=True, story=self.story_of(MEMORY), consenting=consenting
        )
        job = await self.go()
        self.assertEqual((job.state, job.restarts, job.critics), ("queued", 1, None))

    async def test_a_formatter_that_fails_after_the_writer_does_not_make_the_writer_repaid(
        self,
    ) -> None:
        draft = rd.parse_prose(fx.prose_answer(WITH_MOTIVE))
        fixed = rd.parse_prose(fx.prose_answer(CLEAN))
        assert draft is not None and fixed is not None
        # (with a writer and a formatter the fast client does not write the prose)
        fast = Smart(fx.BEATS_ANSWER, fx.GRADE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER)
        await self.setUpWorker(fast, fx.Canned(), critics=True)
        writer = fx.Canned(rd.prose_text(draft), rd.prose_text(fixed))
        formatter = fx.Canned(fx.prose_answer(WITH_MOTIVE), AIError("down"), fx.prose_answer(CLEAN))
        self.worker._creative, self.worker._formatting = writer, formatter
        self.worker._two = True
        self.worker._draft_stage = "write"
        job = await self.settle(await self.go(), 3)
        self.assertEqual(job.state, "ready")
        self.assertEqual(len(writer.sent), 2)  # the first draft and ONE revision
        self.assertNotIn("avenge", job.summary)

    async def test_nothing_of_the_recap_is_logged_by_the_loop(self) -> None:
        fast = self.fast_for(WITH_MOTIVE, grades=2)
        await self.setUpWorker(fast, fx.Canned(fx.prose_answer(CLEAN)), critics=True)
        with self.assertLogs("dmbot.recaps.pipeline", level="INFO") as logs:
            await self.go()
        text = " ".join(logs.output)
        self.assertIn("critics reading 1: 1 hard, 0 soft", text)
        self.assertNotIn("avenge", text)
        self.assertNotIn("Mira", text)

    def test_the_job_type_carries_the_loop(self) -> None:
        self.assertIn("critics", Job.__dataclass_fields__)
