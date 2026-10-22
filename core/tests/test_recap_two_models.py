"""The recap written by two models (#2074): a creative one writes plain text, a small one cuts it
into sections, and code checks that no word of the narration changed. Invented fixtures only."""

from __future__ import annotations

import unittest

from dmbot.ai import DEFAULT_MODELS, AIModels, AIModelTier
from dmbot.config import parse_ai_models
from dmbot.devtools.recap_run import __main__ as run
from dmbot.recaps import calls as cl
from dmbot.recaps import draft as rd
from dmbot.recaps import fixed_lines
from dmbot.recaps import pipeline as pl
from dmbot.recaps.text import narration_of, words
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
            rd.written_words(WRITTEN), words(narration_of(fx.SUMMARY)), "labels are not words"
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

    def test_a_cue_added_moved_or_changed_a_case_or_a_possessive_is_caught(self) -> None:
        for old, new in (
            ("Dusk settled over the road", "Dusk ((hushed)) settled over the road"),  # a cue added
            ("((pause))", "((fast))"),  # a cue reworded
            ("Dusk settled", "dusk settled"),  # only the case
            ("Mira and Oskar", "mira and Oskar"),  # a name lowered
            ("Mira drew her blade", "Mira's drew her blade"),  # an apostrophe added
        ):
            prose = rd.parse_prose(reformatted(old, new))
            assert prose is not None
            self.assertFalse(rd.same_words(prose, WRITTEN), new)

    def test_form_alone_is_not_a_change(self) -> None:
        curly = WRITTEN.replace("Mira drew her blade", "Mira\u2019s blade and Mira drew her blade")
        straight = fx.SUMMARY.replace("Mira drew her blade", "Mira's blade and Mira drew her blade")
        prose = rd.parse_prose(fx.prose_answer(straight))
        assert prose is not None
        self.assertTrue(rd.same_words(prose, curly))  # a curly apostrophe is the same word

    def test_only_the_uppercase_label_lines_are_not_narration(self) -> None:
        written = "MOOD: tense\nMusic: the bard began a slow dirge.\nMUSIC: strings\n"
        self.assertEqual(
            rd.written_tokens(written), ["Music", "the", "bard", "began", "a", "slow", "dirge"]
        )

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
        retry = rd.prose_retry(["too short"], prose, rd.length_for(3.5), plain=True)
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


class TheWritersRoom(unittest.IsolatedAsyncioTestCase):
    """The plain-text writer has room for its answer (#2086); the formatter's room is as it was."""

    def test_the_writers_room_is_larger_than_the_structured_rooms_and_grows_with_the_target(
        self,
    ) -> None:
        rooms = [rd.write_room(rd.length_for(h)) for h in (0.5, 1.0, 1.8, 3.0, 6.0)]
        self.assertEqual(rooms, sorted(rooms))
        self.assertEqual(len(set(rooms)), len(rooms))
        for hours in (0.5, 1.0, 1.8, 3.0, 6.0):
            length = rd.length_for(hours)
            self.assertGreater(rd.write_room(length), length.max_tokens)
        self.assertEqual(rd.length_for(1.8).target, 280)
        self.assertGreater(rd.write_room(rd.length_for(1.8)), 1556)  # run 12's cut-off limit

    async def test_the_writer_is_given_it_and_the_formatter_keeps_the_old_room(self) -> None:
        creative, formatting = fx.Canned(WRITTEN), fx.Canned(GOOD)
        out = await run.run_text(
            inputs(),
            fx.Canned(fx.BEATS_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(),
            creative=creative,
            formatting=formatting,
            creative_models=("creative-model", "formatting-model"),
        )
        self.assertEqual(out.job.state, "ready")
        length = rd.length_for(3.5)
        self.assertEqual(creative.tokens, [rd.write_room(length)])
        self.assertEqual(formatting.tokens, [length.max_tokens])  # unchanged

    def test_a_write_that_was_cut_off_says_the_limit(self) -> None:
        cut = cl.CallEvent("write", 1, "creative", 900, 2890, 2890, "max_tokens")
        self.assertIn("cut off at 2890", cl.line(cut))
        done = cl.CallEvent("write", 1, "creative", 900, 400, 2890, "end_turn")
        self.assertNotIn("cut off", cl.line(done))


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
        self.assertEqual(fixed_lines.without(out.job.summary), fx.SUMMARY)  # not a word changed
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
        self.assertEqual(fixed_lines.without(out.job.summary), fx.SUMMARY)

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
        self.assertTrue(out.job.last_error.startswith(pl.FAILED_CHECKS))  # not the call limit
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


class TheCounts(unittest.IsolatedAsyncioTestCase):
    async def test_every_call_of_a_try_counts_the_formatters_re_run_too(self) -> None:
        bad = reformatted("Dusk settled over the road", "Evening fell across the road")
        out = await TheRun().go(fx.Canned(WRITTEN), fx.Canned(bad, GOOD))
        # beats, write, format, format again, facts, grade, title
        self.assertEqual(out.job.calls, 7)
        self.assertEqual(out.job.spent, 7)

    async def test_a_failed_format_call_keeps_the_text_so_the_writer_is_not_paid_twice(
        self,
    ) -> None:
        from dataclasses import replace

        from dmbot.devtools.recap_run.ephemeral import EphemeralStore

        store = EphemeralStore(1, 1)
        creative, formatting = fx.Canned(WRITTEN), fx.Canned(OSError("the service is down"), GOOD)
        speakers = {fx.DM, fx.ALICE, fx.BOB}

        async def load(gid: int, cid: str, job: object) -> pl.Inputs:
            return inputs()

        async def everyone() -> set[int]:
            return speakers

        async def yes(gid: int, cid: str, job: object) -> bool:
            return True

        async def nobody(gid: int, cid: str, job: object) -> None:
            return None

        worker = pl.RecapWorker(
            store,  # type: ignore[arg-type]
            fx.Canned(fx.BEATS_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER),
            fx.Canned(),
            load=load,
            consenting=lambda gid: everyone,
            allowed=yes,
            notify=nobody,
            creative=creative,
            formatting=formatting,
        )
        for _ in range(8):
            await worker.run(1, "c" * 32, 1)
            if store.job.finished:
                break
            store.job = replace(store.job, next_at=0)  # the wait is over
        self.assertEqual(store.job.state, "ready")
        self.assertEqual(len(creative.sent), 1)  # written once, shaped on the second try
        self.assertEqual(len(formatting.sent), 2)
