"""The recap written by two models (#2074): a creative one writes plain text, a small one cuts it
into sections, and code checks that no word of the narration changed. Invented fixtures only."""

from __future__ import annotations

import unittest

from dmbot.ai import DEFAULT_MODELS, AIModels, AIModelTier
from dmbot.config import parse_ai_models
from dmbot.devtools.recap_run import __main__ as run
from dmbot.recaps import calls as cl
from dmbot.recaps import draft as rd
from dmbot.recaps import pipeline as pl
from tests import recap_fixtures as fx
from tests.test_recap_calls import calls_section, inputs

PROMPTS = tuple(m.split(". ", 1)[1] for m in fx.MUSIC.splitlines())
WRITTEN = rd.prose_text(rd.Prose(fx.SUMMARY, PROMPTS))
GOOD = fx.prose_answer(fx.SUMMARY)


def reformatted(old: str, new: str) -> str:
    return fx.prose_answer(fx.SUMMARY.replace(old, new, 1))


class TheWordCheck(unittest.TestCase):
    def test_the_written_text_has_mood_lines_music_lines_and_the_same_words(self) -> None:
        self.assertIn("MOOD: tense", WRITTEN)
        self.assertIn("MUSIC: Low strings", WRITTEN)
        self.assertEqual(
            rd.written_words(WRITTEN), rd.words(rd.narration_of(fx.SUMMARY)), "labels are not words"
        )
        prose = rd.parse_prose(GOOD)
        assert prose is not None
        self.assertTrue(rd.same_words(prose, WRITTEN))

    def test_a_word_dropped_added_or_reworded_is_not_the_same(self) -> None:
        for old, new in (
            ("Dusk settled over the road", "Dusk settled the road"),  # dropped
            ("Dusk settled over the road", "Dusk settled slowly over the road"),  # added
            ("Dusk settled over the road", "Evening fell across the road"),  # reworded
        ):
            prose = rd.parse_prose(reformatted(old, new))
            assert prose is not None
            self.assertFalse(rd.same_words(prose, WRITTEN), new)

    def test_the_writer_is_asked_for_plain_text_and_the_formatter_for_the_schema(self) -> None:
        plain = rd.prose_system(None, plain=True)
        self.assertIn("MOOD:", plain)
        self.assertIn("not JSON", plain)
        self.assertNotIn("Answer as the JSON", plain)
        self.assertIn("Answer as the JSON", rd.prose_system(None))  # the one-call form is as before
        self.assertIn("single word", rd.FORMAT_SYSTEM)

    def test_a_retry_shows_the_writer_its_draft_as_text(self) -> None:
        prose = rd.parse_prose(GOOD)
        assert prose is not None
        retry = rd.prose_retry(["too short"], prose, rd.length_for(2.5), plain=True)
        self.assertIn("MOOD: tense", retry)
        self.assertNotIn('"sections"', retry)


class TheModels(unittest.TestCase):
    def test_both_settings_are_read_with_their_defaults(self) -> None:
        models, _ = parse_ai_models(lambda name: "")
        self.assertEqual(models.creative, DEFAULT_MODELS.creative)
        self.assertEqual(models.formatting, DEFAULT_MODELS.formatting)
        chosen, _ = parse_ai_models(
            lambda name: {
                "AI_MODEL_CREATIVE": "claude-x-1",
                "AI_MODEL_FORMATTING": "claude-y-1",
            }.get(name, "")
        )
        self.assertEqual((chosen.creative, chosen.formatting), ("claude-x-1", "claude-y-1"))
        self.assertEqual(chosen.of(AIModelTier.CREATIVE), "claude-x-1")
        self.assertEqual(AIModels.same("m").of(AIModelTier.FORMATTING), "m")

    def test_a_refused_creative_model_falls_to_the_careful_tier(self) -> None:
        from dmbot.ai import NEXT_DOWN

        self.assertIs(NEXT_DOWN[AIModelTier.CREATIVE], AIModelTier.CAREFUL)
        self.assertIs(NEXT_DOWN[AIModelTier.FORMATTING], AIModelTier.FAST)


class TheRun(unittest.IsolatedAsyncioTestCase):
    def fast(self) -> fx.Canned:
        return fx.Canned(fx.BEATS_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER)

    async def go(
        self, creative: fx.Canned, formatting: fx.Canned, fast: fx.Canned | None = None
    ) -> run.Outcome:
        return await run.run_text(
            inputs(),
            fast or self.fast(),
            fx.Canned(),
            creative=creative,
            formatting=formatting,
            creative_models=("creative-model", "formatting-model"),
        )

    async def test_a_plain_text_answer_is_formatted_and_the_calls_show_write_and_format(
        self,
    ) -> None:
        creative, formatting = fx.Canned(WRITTEN), fx.Canned(GOOD)
        out = await self.go(creative, formatting)
        self.assertEqual(out.job.state, "ready")
        self.assertEqual(out.job.summary, fx.SUMMARY)  # not a word changed
        self.assertIsNone(creative.schemas[0])  # the writer has no schema
        self.assertEqual(formatting.schemas[0], rd.prose_schema())
        self.assertIn("<recap>", formatting.sent[0])
        stages = [r.split(" try ")[0].strip() for r in calls_section(out)[2:8]]
        self.assertEqual(stages, ["beats", "write", "format", "facts", "grade", "title"])
        rows = calls_section(out)
        self.assertIn("write try 1: creative", rows[3])
        self.assertIn("format try 1: formatting", rows[4])
        self.assertEqual(rows[-1], "  Draft that became the recap: try 1")
        report = "\n".join(run.report(out, {}))
        self.assertIn("Models: fast fast, careful careful, creative creative-model", report)
        self.assertIn("formatting formatting-model", report)

    async def test_a_formatter_that_changes_a_word_is_run_once_more(self) -> None:
        bad = reformatted("Dusk settled over the road", "Dusk settled the road")
        creative, formatting = fx.Canned(WRITTEN), fx.Canned(bad, GOOD)
        out = await self.go(creative, formatting)
        self.assertEqual(out.job.state, "ready")
        self.assertEqual(len(formatting.sent), 2)
        self.assertEqual(len(creative.sent), 1)  # the writer was not asked again
        self.assertEqual(out.job.summary, fx.SUMMARY)

    async def test_a_second_mismatch_fails_the_try_as_shape_and_the_writer_is_told(self) -> None:
        bad = reformatted("Dusk settled over the road", "Evening fell across the road")
        creative, formatting = fx.Canned(WRITTEN, WRITTEN), fx.Canned(bad, bad, GOOD)
        out = await self.go(creative, formatting)
        self.assertEqual(out.job.state, "ready")  # the second try's format was right
        self.assertEqual(len(creative.sent), 2)
        self.assertIn("keep every word", creative.sent[1])
        self.assertTrue(any("shape" in e.checks for e in out.calls if e.checks))
        stage, _, problems = out.failures[0]
        self.assertEqual((stage, cl.kinds(problems)), ("checked", ["shape"]))

    async def test_a_formatter_that_never_keeps_the_words_fails_the_recap_within_the_ceiling(
        self,
    ) -> None:
        bad = reformatted("Dusk settled over the road", "Evening fell across the road")
        creative = fx.Canned(WRITTEN, WRITTEN, WRITTEN)
        formatting = fx.Canned(*[bad] * 6)
        out = await self.go(creative, formatting, fast=fx.Canned(fx.BEATS_ANSWER))
        self.assertEqual(out.job.state, "failed")
        self.assertEqual(out.job.last_error, pl.FAILED_CHECKS)  # not the call limit
        self.assertLessEqual(out.job.calls, pl.MAX_CALLS)

    async def test_the_call_limits_leave_room_for_the_extra_formatting_calls(self) -> None:
        # beats + three tries of (write + two formats) + the fact check of the last + grade + title
        worst = 1 + 3 * 3 + 1 + 1 + 1
        self.assertGreaterEqual(pl.MAX_CALLS, worst)
        clean = 6  # beats, write, format, facts, grade, title
        self.assertGreaterEqual(pl.DAILY_CALLS // clean, 8)

    async def test_without_the_two_clients_the_one_structured_call_runs_as_before(self) -> None:
        out = await run.run_text(
            inputs(),
            fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(),
        )
        self.assertEqual(out.job.state, "ready")
        stages = [r.split(" try ")[0].strip() for r in calls_section(out)[2:7]]
        self.assertEqual(stages, ["beats", "prose", "facts", "grade", "title"])
