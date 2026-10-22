"""The recap test report's "Calls" section (#2053): one line per AI call, counts and fixed words
only, so a failed test needs no follow-up questions. A sentinel word proves no text gets in."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.ai import Reply
from dmbot.devtools.recap_run import __main__ as run
from dmbot.recaps import calls as cl
from dmbot.recaps import pipeline as pl
from tests import recap_fixtures as fx

SENTINEL = "Zephyrquill"  # a word no check or fixture uses: it must never reach the section


def inputs(**kw: Any) -> pl.Inputs:
    base: dict[str, Any] = {
        "lines": fx.lines(), "dm_ids": {fx.DM}, "allowed_names": list(fx.NAMES),
        "secret_names": [], "previous": "", "day": 4, "hours": 3.5,
    }  # fmt: skip
    return pl.Inputs(**{**base, **kw})


def calls_section(out: run.Outcome) -> list[str]:
    return cl.section(out.calls)


class ThePureParts(unittest.TestCase):
    def test_a_problem_becomes_a_fixed_word(self) -> None:
        for problem, kind in (
            ("Your draft was 173 words. This session ran about 1.8 hours", "length"),
            ("the summary is over 3000 characters", "length"),
            ("no quotation marks: tell it as narration", "quotes"),
            ("it must end exactly where the session stopped: the last beat", "ending"),
            ("names that are not allowed: Zed. Use only the names listed", "names"),
            ("it names something the DM keeps secret: leave it out", "secret"),
            ("give exactly one music prompt for each 《…》 section: 3 sections", "layout"),
            ("the answer was cut off: write it again, shorter", "cut off"),
            ("the answer was declined: write it again from the beats", "declined"),
            ("it copies the table word for word: tell that part in your own words", "copying"),
            ("something nobody wrote yet", "other"),
        ):
            self.assertEqual(cl.problem_kind(problem), kind, problem)
        self.assertEqual(cl.kinds(["no quotation marks", "no quotation marks again"]), ["quotes"])

    def test_a_line_has_the_agreed_fields(self) -> None:
        event = cl.CallEvent("prose", 2, "careful", 1204, 2310, 2559, "max_tokens", 173, ["length"])
        self.assertEqual(
            cl.line(event),
            "prose try 2: careful, 1204/2310 (limit 2559), stop: max_tokens, cut off at 2559, "
            "173 words, checks: length",
        )
        event.checks, event.grade = [], {"tension": 4, "ending": 5}
        self.assertTrue(cl.line(event).endswith("checks: passed, grade tension 4 ending 5"))

    def test_the_chosen_draft_line(self) -> None:
        a = cl.CallEvent("prose", 1, "fast", 1, 1, 1, "end_turn", outcome="kept after a failed one")
        b = cl.CallEvent("prose", 2, "careful", 1, 1, 1, "end_turn")
        self.assertIn("try 1 (kept after a failed one)", cl.which_draft([a, b]))
        a.outcome, b.outcome = "", cl.FINAL
        self.assertEqual(cl.which_draft([a, b]), "Draft that became the recap: try 2")
        self.assertEqual(cl.which_draft([]), "")


class TheReport(unittest.IsolatedAsyncioTestCase):
    async def test_an_ordinary_run_lists_every_call_in_order(self) -> None:
        fast = fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER)
        out = await run.run_text(inputs(), fast, fx.Canned())
        rows = calls_section(out)
        self.assertEqual(rows[1], "Calls (5):")
        stages = [r.split(" try ")[0].strip() for r in rows[2:7]]
        self.assertEqual(stages, ["beats", "prose", "facts", "grade", "title"])
        self.assertIn("checks: passed", rows[3])  # the prose call
        self.assertIn("words", rows[3])
        self.assertIn("flagged: nothing", rows[4])  # the fact check
        self.assertIn("grade in_world 5", rows[5])
        self.assertEqual(rows[-1], "  Draft that became the recap: try 1")
        self.assertIn("Calls (5):", "\n".join(run.report(out, {})))  # and it is in the report

    async def test_a_short_revision_and_a_cut_off_one_leave_the_first_draft_standing(self) -> None:
        short = fx.prose_answer("《Tense》 Mira waited.", "1. Low strings.")
        for revision, why in (
            (short, "length"),
            (Reply("{", True, 50, 2559, "m"), "cut off"),
        ):
            out = await run.run_text(
                inputs(),
                fx.Canned(fx.BEATS_ANSWER, fx.PROSE_ANSWER, fx.WEAK_GRADE, fx.FINISH_ANSWER),
                fx.Canned(revision),
            )
            rows = calls_section(out)
            prose = [r for r in rows if r.strip().startswith("prose try")]
            self.assertEqual(len(prose), 2, rows)
            self.assertIn("careful", prose[1])
            self.assertIn(f"checks: {why}", prose[1])
            if why == "cut off":
                self.assertIn("stop: max_tokens", prose[1])
            self.assertIn("try 1 (kept after a failed revision)", rows[-1])
            self.assertIn("checks: passed", prose[0])

    async def test_a_stop_word_the_service_made_up_is_reported_as_other(self) -> None:
        odd = Reply(fx.PROSE_ANSWER, False, 10, 10, "m", stop=f"{SENTINEL}_stop")
        fast = fx.Canned(fx.BEATS_ANSWER, odd, fx.GRADE_ANSWER, fx.FINISH_ANSWER)
        out = await run.run_text(inputs(), fast, fx.Canned())
        text = "\n".join(calls_section(out))
        self.assertNotIn(SENTINEL, text)
        self.assertIn("stop: other", text)

    async def test_no_transcript_or_recap_text_is_in_the_section(self) -> None:
        summary = fx.SUMMARY.replace("Mira", f"Mira, called {SENTINEL},", 1)
        draft = fx.prose_answer(summary, f"1. {SENTINEL} strings.")
        out = await run.run_text(
            inputs(),
            fx.Canned(fx.BEATS_ANSWER, draft, fx.WEAK_GRADE, fx.FINISH_ANSWER),
            fx.Canned(fx.prose_answer(f"《Tense》 {SENTINEL}.", "1. Strings.")),
        )
        text = "\n".join(calls_section(out))
        self.assertTrue(text)
        self.assertNotIn(SENTINEL, text)
        for said in fx.lines():
            for word in said.text.split():
                if len(word) > 5 and word.isalpha():
                    self.assertNotIn(word, text)  # no word of the transcript either
        self.assertNotIn("Silver Lock", text)  # nor the recap's title


if __name__ == "__main__":
    unittest.main()
