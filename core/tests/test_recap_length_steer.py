"""A recap draft of the wrong length is steered, and a retry that does not obey stops the job on our
side (the owner's rule, 2026-10-22, #2383): too short is told to be longer by about N words, too
long shorter; a retry that moves the wrong way, not at all, or past the other end of the range
fails with `check: length` and gives the Try again back."""

from __future__ import annotations

import unittest

from dmbot.recaps import draft as rd
from dmbot.recaps import pipeline as pl
from dmbot.recaps.store import Job
from dmbot.recaps.text import CUE, narration_of, words
from tests import recap_fixtures as fx
from tests.test_recap_jobs import WorkerHarness


def draft(count: int) -> str:
    """A draft of exactly `count` words that ends where the canned session does."""
    return fx.prose_answer(
        "《Tense》 " + " ".join(["calm"] * (count - 1)) + " Oskar", "1. Strings."
    )


# A one-hour session: about 150 words, 75 to 180 (202 on the last try).
HOUR = rd.length_for(1.0)


class TheSteer(unittest.TestCase):
    def test_too_short_says_longer_by_the_distance_to_the_target(self) -> None:
        self.assertEqual(rd.length_steer(40, HOUR), "Make it longer by about 110 words")

    def test_too_long_says_shorter_by_the_distance_to_the_target(self) -> None:
        self.assertEqual(rd.length_steer(230, HOUR), "Make it shorter by about 80 words")

    def test_a_draft_that_fits_is_not_steered(self) -> None:
        for count in (HOUR.low, HOUR.target, HOUR.high):
            self.assertEqual(rd.length_steer(count, HOUR), "")
        self.assertEqual(rd.length_steer(HOUR.last_high, HOUR, last=True), "")
        self.assertNotEqual(rd.length_steer(HOUR.last_high, HOUR, last=False), "")

    def test_the_check_says_it_in_the_retry_feedback_beside_the_other_words(self) -> None:
        short = fx.prose_answer("《Tense》 " + " ".join(["calm"] * 39) + " Oskar", "1. Strings.")
        prose = rd.parse_prose(short)
        assert prose is not None
        problems = rd.check_prose(
            prose, beats=[], transcript="", allowed_names=["Oskar"], secret_names=[], length=HOUR
        )
        self.assertIn("Your draft was 40 words.", problems[0])
        self.assertIn("between 75 and 180. Make it longer by about 110 words", problems[0])


class TheMove(unittest.TestCase):
    def move(self, before: tuple[int, int] | None, count: int, last: bool = False) -> str:
        return rd.length_move(before, count, HOUR, last)

    def test_the_first_draft_is_never_stopped(self) -> None:
        self.assertEqual(self.move(None, 10), "")

    def test_a_retry_into_the_range_carries_on(self) -> None:
        self.assertEqual(self.move((40, 1), 120), "")
        self.assertEqual(self.move((230, -1), 150), "")

    def test_a_short_draft_that_moved_up_but_is_still_short_is_steered_again(self) -> None:
        self.assertEqual(self.move((40, 1), 60), "")
        self.assertEqual(self.move((40, 1), 43), "")  # (more than the margin)

    def test_a_long_draft_that_moved_down_but_is_still_long_is_steered_again(self) -> None:
        self.assertEqual(self.move((230, -1), 200), "")

    def test_a_retry_that_moves_the_wrong_way_or_not_at_all_is_stuck(self) -> None:
        for before, count in (((40, 1), 30), ((40, 1), 40), ((40, 1), 42), ((230, -1), 240),
                              ((230, -1), 230), ((230, -1), 228)):  # fmt: skip
            self.assertEqual(self.move(before, count), "stuck", (before, count))

    def test_a_retry_that_goes_past_the_other_end_overshot(self) -> None:
        self.assertEqual(self.move((40, 1), 200), "overshot")
        self.assertEqual(self.move((230, -1), 60), "overshot")

    def test_the_last_try_judges_by_its_wider_range(self) -> None:
        self.assertEqual(self.move((40, 1), 190, last=True), "")  # 190 fits up to 202
        self.assertEqual(self.move((40, 1), 205, last=True), "overshot")

    def test_the_fix_up_of_sections_leaves_the_words_for_the_length_check(self) -> None:
        text = "《Tense》 one two three. 《Calm》 four five. 《Tense》 six seven eight nine."
        prose = rd.Prose(text, ("a", "b", "c"))
        fixed = rd.fix_prose(prose, 1)
        self.assertEqual(
            len(words(narration_of(fixed.summary))), len(words(narration_of(prose.summary)))
        )
        self.assertEqual(len(CUE.findall(fixed.summary)), 1)
        self.assertEqual(len(fixed.music), 1)


class TheJob(WorkerHarness):
    async def run_drafts(
        self, first: int, *retries: int, hours: float = 1.0, tries: int = 1
    ) -> Job:
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, draft(first), fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(*(draft(n) for n in retries)),
            hours=hours,
        )
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET tries = %s", (tries,))
        return await self.go()

    async def tries_left(self) -> int:
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        return job.tries

    async def test_too_short_is_told_to_be_longer_by_the_distance_and_then_passes(self) -> None:
        job = await self.run_drafts(40, 120)
        self.assertEqual(job.state, "ready")
        self.assertIn("Make it longer by about 110 words", self.careful.sent[0])

    async def test_too_long_is_told_to_be_shorter_by_the_distance(self) -> None:
        job = await self.run_drafts(230, 140)
        self.assertEqual(job.state, "ready")
        self.assertIn("Make it shorter by about 80 words", self.careful.sent[0])

    async def test_a_retry_that_goes_the_wrong_way_fails_ours_and_gives_the_try_back(self) -> None:
        job = await self.run_drafts(40, 30)
        self.assertEqual(job.state, "failed")
        self.assertTrue(job.last_error.startswith(pl.FAILED_CHECKS), job.last_error)
        self.assertTrue(job.last_error.endswith("(check: length)"), job.last_error)
        self.assertEqual(await self.tries_left(), 0)  # the DM's Try again is given back
        self.assertEqual(len(self.careful.sent), 1)  # and no more is spent on it

    async def test_a_retry_that_does_not_move_fails_the_same_way(self) -> None:
        job = await self.run_drafts(40, 41)
        self.assertEqual((job.state, await self.tries_left()), ("failed", 0))
        self.assertIn("(check: length)", job.last_error)

    async def test_a_short_draft_that_overshoots_to_too_long_fails_the_same_way(self) -> None:
        job = await self.run_drafts(40, 200)
        self.assertEqual((job.state, await self.tries_left()), ("failed", 0))
        self.assertIn("(check: length)", job.last_error)

    async def test_a_long_draft_that_overshoots_to_too_short_fails_the_same_way(self) -> None:
        job = await self.run_drafts(230, 60)
        self.assertEqual((job.state, await self.tries_left()), ("failed", 0))
        self.assertIn("(check: length)", job.last_error)

    async def test_a_retry_that_moves_right_but_not_far_enough_is_steered_again(self) -> None:
        job = await self.run_drafts(40, 60, 130)
        self.assertEqual(job.state, "ready")
        self.assertIn("Make it longer by about 90 words", self.careful.sent[1])

    async def test_a_half_hour_session_passes_with_a_writer_that_follows_the_steer(self) -> None:
        job = await self.run_drafts(20, 110, hours=0.5)
        self.assertEqual(job.state, "ready")
        self.assertIn("longer by about 90 words", self.careful.sent[0])

    async def test_a_one_hour_session_passes_with_a_writer_that_follows_the_steer(self) -> None:
        job = await self.run_drafts(40, 150, hours=1.0)
        self.assertEqual(job.state, "ready")
        self.assertIn("longer by about 110 words", self.careful.sent[0])


class TheEnding(WorkerHarness):
    """Test L failed twice on the ending check (dev1, #2331). A short session shows the writer only
    a pick of its beats, and the ending block was the pick's last two: a middle beat in front of
    the real last one, while the check looks for the session's real last two."""

    def test_the_ending_block_holds_the_sessions_last_two_beats_not_the_picks(self) -> None:
        from tests.test_recaps import beats

        sheet = beats()
        picked = rd.pick_beats(sheet, 3)
        self.assertEqual([b.number for b in picked], [1, 3, 5])  # (beat 4 is not in the pick)
        message = rd.prose_message(picked, "", ["Mira"], None, ending=sheet)
        ending = message.split("<ending>")[1].split("</ending>")[0]
        self.assertIn("Mira found a locked door with a silver lock", ending)  # beat 4
        self.assertIn("Oskar picked the lock and something breathed", ending)  # beat 5
        self.assertIn("Mira", message.split("<names>")[1])  # beat 4's own characters are allowed

    def test_without_the_sheet_the_block_is_the_shown_beats_last_two_as_before(self) -> None:
        from tests.test_recaps import beats

        shown = rd.pick_beats(beats(), 3)
        ending = rd.prose_message(shown, "", [], None).split("<ending>")[1].split("</ending>")[0]
        self.assertIn(shown[-2].what, ending)

    async def test_the_writer_of_a_short_session_is_shown_the_real_last_two_beats(self) -> None:
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, draft(100), fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(),
            hours=0.5,
        )
        job = await self.go()
        self.assertEqual(job.state, "ready")
        prose_message = self.fast.sent[1]
        ending = prose_message.split("<ending>")[1].split("</ending>")[0]
        self.assertIn("Mira found a locked door with a silver lock", ending)


class TheLog(WorkerHarness):
    async def test_a_failed_draft_logs_its_counts_and_check_names_never_its_words(self) -> None:
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, draft(40), fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(draft(120)),
            hours=1.0,
        )
        with self.assertLogs("dmbot.recaps.pipeline", "INFO") as logged:
            await self.go()
        line = next(m for m in logged.output if "a draft failed" in m)
        self.assertIn("length", line)
        self.assertIn(
            "40 words, 75 to 180 wanted, 1 section(s) of at most 2, about 1.00 hours", line
        )
        self.assertNotIn("calm", " ".join(logged.output))


class TheState(WorkerHarness):
    async def run_drafts(self, *drafts: int, hours: float = 1.0) -> Job:
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, draft(drafts[0]), fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(*(draft(n) for n in drafts[1:])),
            hours=hours,
        )
        return await self.go()

    async def test_a_draft_that_fits_but_fails_another_check_starts_the_count_afresh(self) -> None:
        invented = fx.prose_answer(
            "《Tense》 " + " ".join(["calm"] * 99) + " Lord Varkus laughed.", "1. Strings."
        )
        await self.setUpWorker(
            fx.Canned(fx.BEATS_ANSWER, draft(40), fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(invented, draft(41)),
            hours=1.0,
        )
        with self.assertLogs("dmbot.recaps.pipeline", "INFO") as logged:
            await self.go()
        # 40 (short), then 103 words that fit but name Varkus, then 41 (short again): the 41 is
        # steered afresh, not stopped as "did not move" from the 40 of two drafts ago.
        self.assertFalse(any("length is stuck" in m for m in logged.output), logged.output)

    async def test_the_last_try_accepts_a_draft_up_to_35_percent_over_after_steering(self) -> None:
        job = await self.run_drafts(230, 215, 200)  # 202 is the last try's top: 200 fits
        self.assertEqual(job.state, "ready")
        self.assertIn("Make it shorter by about 65 words", self.careful.sent[1])

    async def test_a_draft_that_names_a_secret_leaves_its_length_out_of_the_next_comparison(
        self,
    ) -> None:
        await self.setUpWorker(
            fx.Canned(
                fx.BEATS_ANSWER,
                fx.prose_answer("《Tense》 " + " ".join(["calm"] * 38) + " Oskar", "1. Strings."),
                fx.GRADE_ANSWER,
                fx.FINISH_ANSWER,
            ),
            fx.Canned(draft(41)),
            hours=1.0,
            secret=[
                "calm"
            ],  # (the draft names it, so it is not sent back and the retry starts over)
        )
        job = await self.go()
        self.assertNotIn("(check: length)", job.last_error)

    async def test_a_failed_job_leaves_no_count_for_the_next_try_again(self) -> None:
        job = await self.run_drafts(40, 41)  # stuck: fails ours
        self.assertIn("(check: length)", job.last_error)
        self.assertNotIn((1, self.one.id, 100), self.worker._lengths)  # type: ignore[attr-defined]
