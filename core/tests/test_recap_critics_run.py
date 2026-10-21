"""The critics loop in the worker (#2255) against a real database: a recap is read by the motive
check and PlotBot, revised with their changes at once, and read again, at most four times."""

from __future__ import annotations

import json
from collections.abc import Sequence
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

MOTIVE = "Mira wanted to avenge her brother, who had fallen in the keep long ago."
WITH_MOTIVE = fx.SUMMARY.replace(
    "Mira drew her blade without a word,", f"{MOTIVE} Mira drew her blade without a word,", 1
)
NO_MOTIVE_ANSWERS = '{"checks": []}'
MEMORY = sg.Memory("f1" * 16, "The silver lock belongs to the Order of the Lantern.", True)
NO_NOTES = json.dumps({"notes": [], "details": []})
AGREE = json.dumps({"changes": []})


def change(sentence: int, kind: str, text: str = "x", *ties: str) -> str:
    item = {"sentence": sentence, "kind": kind, "change": text, "ties": list(ties)}
    return json.dumps({"changes": [item]})


class Smart(fx.Canned):
    """Canned answers, with the motive check's and PlotBot's calls taken from lists of their own
    so the tests need not know the order the stages make their calls in."""

    def __init__(self, *answers: str | Exception | Reply, motives: Sequence[str] = ()) -> None:
        super().__init__(*answers)
        self.motive_answers = list(motives)
        self.motive_sent: list[str] = []

    async def complete(self, system: str, text: str, **kw: Any) -> Reply:
        if system == mo.SYSTEM:
            self.motive_sent.append(text)
            answer = self.motive_answers.pop(0) if self.motive_answers else NO_MOTIVE_ANSWERS
            return Reply(answer, False, 10, 10, "m")
        return await super().complete(system, text, **kw)


class Plot(fx.Canned):
    """The careful client: PlotBot's notes first, then its reviews and the revisions, in the order
    the tests give them (the fact check is answered by `Canned` itself)."""


class Criticising(WorkerHarness):
    def fast_for(self, summary: str = fx.SUMMARY, *, grades: int = 1, motives: Any = ()) -> Smart:
        prose = fx.prose_answer(summary)
        return Smart(
            fx.BEATS_ANSWER, prose, *([fx.GRADE_ANSWER] * grades), fx.FINISH_ANSWER, motives=motives
        )

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
        self.assertEqual(fast.motive_sent, [])  # nothing in it states a reason or a plan
        self.assertEqual(job.calls, 5)  # no more than before

    async def test_an_invented_motive_is_caught_and_fixed_in_the_second_round(self) -> None:
        fast = self.fast_for(WITH_MOTIVE, grades=2)
        careful = fx.Canned(fx.PROSE_ANSWER)  # the revision
        await self.setUpWorker(fast, careful, critics=True)
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertNotIn("avenge", job.summary)
        self.assertEqual(fast.motive_sent.__len__(), 1)  # the revision states none
        told = careful.sent[0]  # the writer gets the draft and what nobody said
        self.assertIn("<previous_draft>", told)
        self.assertIn("avenge her brother", told)
        self.assertIn("say only what the characters did", told)
        loop = cr.loop_from_json(job.critics)
        self.assertEqual((loop.done, loop.round, loop.notes, loop.cut), (True, 2, (), 0))

    async def test_a_motive_somebody_said_is_not_a_failure(self) -> None:
        said = "Mira wants to avenge her brother, he fell at the old keep."
        lines = [*fx.lines(), Line(9000, fx.ALICE, said, said)]
        fast = self.fast_for(WITH_MOTIVE)
        await self.setUpWorker(fast, fx.Canned(), critics=True, lines=lines)
        sentences = rd.sentences_of(WITH_MOTIVE)
        at = next(n for n, t in enumerate(sentences, 1) if "avenge" in t)
        fast.motive_answers = [
            json.dumps({"checks": [{"sentence": at, "lines": [8], "recap": False}]})
        ]
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertIn("avenge", job.summary)  # kept: a player said it
        self.assertEqual(cr.loop_from_json(job.critics).round, 1)

    async def test_what_plotbot_says_matters_reaches_the_writer_and_the_revision_is_read(
        self,
    ) -> None:
        fast = self.fast_for(grades=2)
        careful = fx.Canned(
            NO_NOTES,  # the notes before the writing
            change(0, "missed", "say the silver lock belongs to the order", MEMORY.id),
            fx.PROSE_ANSWER,  # the revision
            AGREE,  # the second reading
        )
        await self.setUpWorker(fast, careful, critics=True, story=self.story_of(MEMORY))
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertIn("say the silver lock belongs to the order", careful.sent[2])
        loop = cr.loop_from_json(job.critics)
        self.assertEqual((loop.done, loop.round, loop.notes), (True, 2, ()))

    async def test_critics_that_never_agree_stop_at_four_readings_and_leave_notes(self) -> None:
        fast = self.fast_for(grades=4)
        ask = change(0, "weight", "elaborate considerably", MEMORY.id)
        careful = fx.Canned(
            NO_NOTES, ask, fx.PROSE_ANSWER, ask, fx.PROSE_ANSWER, ask, fx.PROSE_ANSWER, ask
        )
        await self.setUpWorker(fast, careful, critics=True, story=self.story_of(MEMORY))
        job = await self.go()
        loop = cr.loop_from_json(job.critics)
        self.assertEqual((job.state, loop.done, loop.round), ("ready", True, cr.MAX_ROUNDS))
        self.assertEqual(len(loop.notes), 1)
        self.assertEqual(
            loop.notes[0], cr.NOTES["weight"]
        )  # for the DM's recap page: a thing to check
        self.assertEqual(len(careful.answers), 0)  # no fifth reading was paid for
        self.assertLessEqual(job.calls, pl.MAX_CALLS)

    async def test_a_secret_plotbot_finds_is_never_shipped(self) -> None:
        fast = self.fast_for(grades=4)
        ask = change(2, "secret", "it names the baron", MEMORY.id)
        careful = fx.Canned(
            NO_NOTES, ask, fx.PROSE_ANSWER, ask, fx.PROSE_ANSWER, ask, fx.PROSE_ANSWER, ask
        )
        await self.setUpWorker(fast, careful, critics=True, story=self.story_of(MEMORY))
        sentences = rd.sentences_of(fx.SUMMARY)
        job = await self.go()
        self.assertEqual(job.state, "ready")
        loop = cr.loop_from_json(job.critics)
        self.assertEqual(loop.cut, 1)  # the sentence is cut in code
        self.assertNotIn(sentences[1], job.summary)
        self.assertIn(sentences[0], job.summary)
        self.assertNotIn("baron", json.dumps(job.critics))
        self.assertNotIn("baron", careful.sent[2])  # and the writer was never told what it hid

    async def test_every_sentence_is_asked_about_even_when_there_are_more_than_one_call_holds(
        self,
    ) -> None:
        second = "Oskar hoped to find the silver lock before the bells rang."
        summary = WITH_MOTIVE.replace(
            "Mira drew her blade without a word,",
            f"{second} Mira drew her blade without a word,",
            1,
        )
        fast = self.fast_for(summary, grades=2)
        await self.setUpWorker(fast, fx.Canned(fx.PROSE_ANSWER), critics=True)
        with patch.object(mo, "MAX_ASKED", 1):
            job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertEqual(len(fast.motive_sent), 2)  # one call each, none left unasked
        self.assertNotIn("avenge", job.summary)
        self.assertNotIn("hoped", job.summary)

    async def test_plotbot_failing_after_the_motive_check_still_cuts_a_motive_nobody_said(
        self,
    ) -> None:
        fast = self.fast_for(WITH_MOTIVE)
        careful = fx.Canned(NO_NOTES, AIError("down"), AIError("down"))
        await self.setUpWorker(fast, careful, critics=True, story=self.story_of(MEMORY))
        job = await self.go()
        for _ in range(4):
            if job.finished:
                break
            self.clock.now += 1000
            job = await self.go()
        self.assertEqual((job.state, job.last_error), ("ready", ""))
        self.assertNotIn("avenge", job.summary)  # found by the motive check, cut, never shipped
        self.assertEqual(len(fast.motive_sent), 1)  # and never paid for twice
        self.assertEqual(cr.loop_from_json(job.critics).cut, 1)

    async def test_a_failed_plotbot_call_does_not_make_the_next_try_pay_for_the_motive_check(
        self,
    ) -> None:
        fast = self.fast_for(WITH_MOTIVE, grades=2)
        careful = fx.Canned(NO_NOTES, AIError("down"), change(0, "missed"), AGREE)
        await self.setUpWorker(fast, careful, critics=True, story=self.story_of(MEMORY))
        job = await self.go()
        self.assertEqual(cr.loop_from_json(job.critics).motives is not None, True)
        self.clock.now += 300
        careful.answers[:0] = [fx.PROSE_ANSWER]  # (the revision follows the second review)
        careful.answers[:] = [change(0, "missed"), fx.PROSE_ANSWER, AGREE]
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertEqual(len(fast.motive_sent), 1)

    async def test_critics_that_never_agree_about_a_motive_cut_it_and_stop_at_four(self) -> None:
        fast = self.fast_for(WITH_MOTIVE, grades=4)
        fast.motive_answers = [NO_MOTIVE_ANSWERS] * 4  # nobody ever says it
        careful = fx.Canned(*([fx.prose_answer(WITH_MOTIVE)] * 3))
        await self.setUpWorker(fast, careful, critics=True)
        job = await self.go()
        loop = cr.loop_from_json(job.critics)
        self.assertEqual((job.state, loop.round, loop.cut), ("ready", cr.MAX_ROUNDS, 1))
        self.assertNotIn("avenge", job.summary)
        self.assertEqual(len(fast.motive_sent), cr.MAX_ROUNDS)

    async def test_the_days_tally_near_its_cap_stops_the_loop_and_never_fails_the_recap(
        self,
    ) -> None:
        fast = self.fast_for(WITH_MOTIVE)
        await self.setUpWorker(fast, fx.Canned(), critics=True)
        tally = AsyncMock(return_value=pl.DAILY_CALLS - 3)  # room for the title, no more
        with patch.object(self.store, "calls_since", tally):
            job = await self.go()
        self.assertEqual((job.state, job.last_error), ("ready", ""))
        self.assertEqual(fast.motive_sent, [])  # the loop did not begin
        self.assertTrue(cr.loop_from_json(job.critics).done)

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
        bare = rd.Prose(fx.SUMMARY, ())
        draft = rd.parse_prose(fx.prose_answer(WITH_MOTIVE))
        assert draft is not None
        # (with a writer and a formatter the fast client does not write the prose)
        fast = Smart(fx.BEATS_ANSWER, fx.GRADE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER)
        await self.setUpWorker(fast, fx.Canned(), critics=True)
        fixed = rd.parse_prose(fx.PROSE_ANSWER)
        assert fixed is not None and bare
        writer = fx.Canned(rd.prose_text(draft), rd.prose_text(fixed))
        formatter = fx.Canned(fx.prose_answer(WITH_MOTIVE), AIError("down"), fx.PROSE_ANSWER)
        self.worker._creative, self.worker._formatting = writer, formatter
        self.worker._two = True
        self.worker._draft_stage = "write"
        job = await self.go()
        for _ in range(3):
            if job.finished:
                break
            self.clock.now += 1000
            job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertEqual(len(writer.sent), 2)  # the first draft and ONE revision
        self.assertNotIn("avenge", job.summary)

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
        fresh = fx.Canned(fx.PROSE_ANSWER)
        self.worker._fast, self.worker._careful = second, fresh
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertEqual(second.motive_sent, [])  # the old draft's reading was kept
        self.assertEqual(len(fresh.sent), 1)  # one revision, no second reading of the first draft
        self.assertNotIn("avenge", job.summary)

    async def test_a_service_that_keeps_failing_never_fails_a_recap_that_is_written(self) -> None:
        fast = self.fast_for(WITH_MOTIVE)
        careful = fx.Canned(AIError("down"), AIError("down"), AIError("down"))
        await self.setUpWorker(fast, careful, critics=True)
        job = await self.go()
        for _ in range(4):
            if job.finished:
                break
            self.clock.now += 1000
            job = await self.go()
        self.assertEqual((job.state, job.last_error), ("ready", ""))
        loop = cr.loop_from_json(job.critics)
        self.assertTrue(loop.done)
        self.assertNotIn("avenge", job.summary)  # the hard failure was cut, not shipped

    async def test_the_ceilings_hold(self) -> None:
        self.assertEqual((pl.MAX_CALLS, pl.DAILY_CALLS), (95, 280))
        fast = self.fast_for(WITH_MOTIVE)
        await self.setUpWorker(fast, fx.Canned(), critics=True)
        with patch.object(pl, "MAX_CALLS", 9):  # the loop keeps what the title step needs
            job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertLessEqual(job.calls, 9)
        loop = cr.loop_from_json(job.critics)
        self.assertTrue(loop.done)
        self.assertEqual(fast.motive_sent, [])

    async def test_nothing_of_the_recap_is_logged_by_the_loop(self) -> None:
        fast = self.fast_for(WITH_MOTIVE, grades=2)
        await self.setUpWorker(fast, fx.Canned(fx.PROSE_ANSWER), critics=True)
        with self.assertLogs("dmbot.recaps.pipeline", level="INFO") as logs:
            await self.go()
        text = " ".join(logs.output)
        self.assertIn("critics reading 1: 1 hard, 0 soft", text)
        self.assertNotIn("avenge", text)
        self.assertNotIn("Mira", text)

    def test_the_job_type_carries_the_loop(self) -> None:
        self.assertIn("critics", Job.__dataclass_fields__)
