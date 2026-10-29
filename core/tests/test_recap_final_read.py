"""The final read (#2952) against a real database: the careful tier edits a finished recap, the
edit goes through the writer's hard checks, and anything that fails leaves the draft as it was."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from dmbot.ai import AIError, AIOutOfFunds, Reply
from dmbot.recaps import critics as cr
from dmbot.recaps import draft as rd
from dmbot.recaps import final_read as fr
from dmbot.recaps import pipeline as pl
from dmbot.recaps import progress as rp
from dmbot.recaps.store import Job
from tests import recap_fixtures as fx
from tests.test_recap_critics_run import CLEAN, Smart
from tests.test_recap_jobs import WorkerHarness

EDITED = CLEAN.replace(
    "They stepped inside together.", "They stepped inside together, out of the fading dusk."
)


class FinalRead(WorkerHarness):
    def fast_for(self, summary: str = CLEAN, *, grades: int = 1) -> Smart:
        prose = fx.prose_answer(summary)
        return Smart(fx.BEATS_ANSWER, prose, *([fx.GRADE_ANSWER] * grades), fx.FINISH_ANSWER)

    async def settle(self, job: Job, times: int = 6) -> Job:
        for _ in range(times):
            if job.finished:
                break
            self.clock.now += 1000
            job = await self.go()
        return job

    async def run_it(self, careful: fx.Canned, **kw: Any) -> Job:
        await self.setUpWorker(self.fast_for(), careful, critics=True, final_read=True, **kw)
        return await self.settle(await self.go())

    def loop_of(self, job: Job) -> cr.Loop:
        return cr.loop_from_json(job.critics)

    async def test_an_edit_that_passes_every_check_is_what_the_dm_gets(self) -> None:
        careful = fx.Canned(fx.prose_answer(EDITED))
        job = await self.run_it(careful)
        self.assertEqual(job.state, "ready")
        self.assertIn("out of the fading dusk", job.summary)
        self.assertEqual(self.loop_of(job).final, "edited")
        self.assertNotIn(fr.UNUSED_NOTE, self.loop_of(job).notes)
        # it was shown the draft and everything the writer had, as data
        told = careful.sent[0]
        self.assertIn("<draft>", told)
        self.assertIn("<transcript>", told)
        self.assertIn("coherent short story", careful.systems[0])
        self.assertEqual(len(careful.fact_sent), 2)  # the draft's, then the edit's

    async def test_a_name_nobody_gave_it_falls_back_to_the_draft(self) -> None:
        bad = EDITED.replace("the fading dusk", "the dusk, as Lord Varkus had foretold")
        job = await self.run_it(fx.Canned(fx.prose_answer(bad)))
        self.assertEqual(job.state, "ready")
        self.assertNotIn("Varkus", job.summary)
        self.assertNotIn("fading dusk", job.summary)  # not half-edited: the draft whole
        self.assertIn(CLEAN, job.summary)
        self.assertEqual(self.loop_of(job).final, "kept")
        self.assertIn(fr.UNUSED_NOTE, self.loop_of(job).notes)

    async def test_an_edit_that_leaves_a_secret_in_falls_back(self) -> None:
        bad = EDITED.replace("the fading dusk", "the dusk, with Brann watching")
        job = await self.run_it(
            fx.Canned(fx.prose_answer(bad)), secret=["Brann"], names=[*fx.NAMES, "Maris"]
        )
        self.assertNotIn("Brann", job.summary)
        self.assertEqual(self.loop_of(job).final, "kept")
        for sent in [*self.careful.sent, *self.careful.fact_sent]:
            self.assertNotIn("Brann", sent)  # the rejected edit is never sent back anywhere

    async def test_a_cut_answer_falls_back(self) -> None:
        cut = Reply(fx.prose_answer(EDITED), cut=True)
        job = await self.run_it(fx.Canned(cut))
        self.assertIn(CLEAN, job.summary)  # the draft whole, with the fixed lines round it
        self.assertEqual(self.loop_of(job).final, "kept")
        self.assertIn(fr.UNUSED_NOTE, self.loop_of(job).notes)

    async def test_a_failed_fact_check_falls_back(self) -> None:
        careful = fx.Canned(
            fx.prose_answer(EDITED),
            facts=[fx.CLEAN_FACTS, fx.facts_answer(unsupported=[(2, "place")])],
        )
        job = await self.run_it(careful)
        self.assertIn(CLEAN, job.summary)  # the draft whole, with the fixed lines round it
        self.assertEqual(self.loop_of(job).final, "kept")

    async def test_a_service_that_keeps_failing_leaves_the_draft_with_a_note(self) -> None:
        job = await self.run_it(fx.Canned(AIError("x"), AIError("x"), AIError("x")))
        self.assertEqual(job.state, "ready")
        self.assertIn(CLEAN, job.summary)  # the draft whole, with the fixed lines round it
        self.assertEqual(self.loop_of(job).final, "kept")

    async def test_it_is_off_unless_the_worker_is_told(self) -> None:
        await self.setUpWorker(self.fast_for(), fx.Canned(), critics=True)
        job = await self.settle(await self.go())
        self.assertEqual((job.state, self.loop_of(job).final), ("ready", ""))
        self.assertEqual(self.careful.sent, [])

    def test_a_draft_the_dm_approved_as_it_stood_is_not_edited(self) -> None:
        # (#2305) the website sets done + approved together; the worker goes straight to the title
        self.assertTrue(pl.final_due(cr.Loop(done=True)))
        self.assertTrue(pl.final_due(cr.Loop(done=True, final="reading")))  # a restart resumes it
        self.assertFalse(pl.final_due(cr.Loop(done=True, approved_by=7, approved_at=5)))
        self.assertFalse(pl.final_due(cr.Loop(done=True, final="edited")))
        self.assertFalse(pl.final_due(cr.Loop(done=True, final="kept")))

    async def test_a_recap_without_room_for_the_pass_skips_it_and_still_finishes(self) -> None:
        careful = fx.Canned()
        with patch.object(pl, "FINAL_ROOM", 10_000):
            job = await self.run_it(careful)
        self.assertEqual(job.state, "ready")
        self.assertEqual(careful.sent, [])  # no call was made for it
        self.assertEqual(self.loop_of(job).final, "kept")
        self.assertIn(fr.SKIPPED_NOTE, self.loop_of(job).notes)

    async def test_running_out_of_credit_in_the_pass_fails_the_job_plainly(self) -> None:
        job = await self.run_it(fx.Canned(AIOutOfFunds("no credit")))
        self.assertEqual((job.state, job.last_error), ("failed", pl.FUNDS))

    async def test_the_pass_leaves_no_call_record_behind_when_the_job_ends(self) -> None:
        await self.run_it(fx.Canned(fx.prose_answer(EDITED)))
        self.assertFalse([k for k in self.worker._last if k[3] == "final"])

    def test_the_switch_is_on_unless_turned_off(self) -> None:
        from dmbot.config import ConfigError, _on_unless_off

        def get(value: str) -> Any:
            return lambda name: value

        for raw in ("", "1", "on", "TRUE"):
            self.assertTrue(_on_unless_off(get(raw), "RECAP_FINAL_READ"), raw)
        for raw in ("0", "off", "false"):
            self.assertFalse(_on_unless_off(get(raw), "RECAP_FINAL_READ"), raw)
        with self.assertRaises(ConfigError):
            _on_unless_off(get("maybe"), "RECAP_FINAL_READ")

    def test_the_page_names_the_stage_while_the_pass_runs(self) -> None:
        row = {
            "state": "prose",
            "last_error": "",
            "critics": cr.Loop(done=True, final="reading").to_json(),
            "window_count": 0,
        }
        self.assertEqual(rp.stage_of(row, now=0), rp.FINAL_READ)
        row["critics"] = cr.Loop(done=True, final="kept").to_json()
        self.assertEqual(rp.stage_of(row, now=0), rp.FINISHING)

    def test_the_message_is_the_writers_material_with_the_draft_last(self) -> None:
        message = fr.final_message(
            rd.Prose(CLEAN, ("quiet strings",)),
            [],
            "",
            ["Mira"],
            None,
            ending=[],
            transcript="DM: A door.",
            who_and_what=("Mira: a scout",),
            threads=("Find the crown",),
            story=("An earlier recap.",),
        )
        self.assertTrue(message.rstrip().endswith("</draft>"))
        for tag in ("<transcript>", "<who_and_what>", "<threads>", "<story_so_far>"):
            self.assertIn(tag, message)
