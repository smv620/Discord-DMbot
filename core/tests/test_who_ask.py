"""Asking the AI who "he" is (#2790): the question, reading the answers, the blend. A scripted
stand-in for the AI; invented names; no network."""

from __future__ import annotations

import json
import unittest
from collections.abc import Sequence
from typing import Any

from dmbot.ai import Reply
from dmbot.memory import who_ask
from dmbot.memory.who_ask import ask, blend, parse_pick, parse_scores, question
from dmbot.memory.who_is_it import NOT_LISTED, Candidate, Verdict, decide

NAMES = ["Ken", "Mira Voss", "Bram"]
PASSAGE = "Ken went to meet Bram at the docks. He had brought the payment."


def scores(*pairs: tuple[str, float]) -> str:
    return json.dumps({"candidates": [{"name": n, "confidence": c} for n, c in pairs]})


def pick(name: str) -> str:
    return json.dumps({"name": name})


class Scripted:
    """Answers the scoring call first, then each sample from `picks` in turn."""

    def __init__(self, first: str | Exception, picks: Sequence[str | Exception]) -> None:
        self.first, self.picks = first, list(picks)
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def complete(
        self, system: str, text: str, *, max_tokens: int = 0, schema: dict[str, Any] | None = None
    ) -> Reply:
        self.calls.append((system, text, schema))
        item = self.first if len(self.calls) == 1 else self.picks.pop(0)
        if isinstance(item, Exception):
            raise item
        return Reply(item, cut=False)


class TheQuestion(unittest.TestCase):
    def test_only_the_local_passage_and_the_offered_names_go_and_it_is_data(self) -> None:
        text = question("a <b> line\n  two", "He", ["Ken", "Ken", "Bram"], "ASK")
        self.assertIn("<passage>\na ‹b› line two\n</passage>", text)
        self.assertEqual(text.count("Ken"), 1)  # offered once
        self.assertTrue(text.endswith("ASK"))

    def test_a_long_document_is_cut_to_the_end_nearest_the_phrase(self) -> None:
        long = "x" * 5000 + " the end"
        text = question(long, "he", ["Ken"], "ASK")
        self.assertIn("the end", text)
        self.assertLess(len(text), who_ask.MAX_PASSAGE_CHARS + 400)

    def test_at_most_the_offer_limit_of_names(self) -> None:
        names = [f"N{i}" for i in range(100)]
        text = question("p", "he", names, "ASK")
        self.assertIn(f"N{who_ask.MAX_OFFERED - 1}", text)
        self.assertNotIn(f"N{who_ask.MAX_OFFERED}\n", text)


class ReadingAnswers(unittest.TestCase):
    def test_a_name_is_kept_only_if_offered_however_it_is_cased(self) -> None:
        got = parse_scores(scores(("ken", 60), ("Invented Person", 90), ("Bram", 20)), NAMES)
        assert got is not None
        by_name = {c.name: c.confidence for c in got}
        self.assertEqual(by_name, {"Ken": 60, "Bram": 20, NOT_LISTED: 0.0})

    def test_not_listed_is_always_there_and_a_repeat_keeps_its_best_score(self) -> None:
        got = parse_scores(scores(("Ken", 30), ("Ken", 55), (NOT_LISTED, 10)), NAMES)
        assert got is not None
        self.assertEqual({c.name: c.confidence for c in got}, {"Ken": 55, NOT_LISTED: 10})

    def test_unreadable_answers_are_nothing(self) -> None:
        for text in ("", "not json", "[]", '{"candidates": 3}', '{"x": 1}'):
            with self.subTest(text=text):
                self.assertIsNone(parse_scores(text, NAMES))
        self.assertIsNone(parse_pick("{}", NAMES))
        self.assertIsNone(parse_pick(pick("Stranger"), NAMES))
        self.assertEqual(parse_pick(pick("mira voss"), NAMES), "Mira Voss")

    def test_a_bad_confidence_is_skipped(self) -> None:
        rows = {"candidates": [{"name": "Ken", "confidence": "high"}, {"name": "Bram"}]}
        got = parse_scores(json.dumps(rows), NAMES)
        assert got is not None
        self.assertEqual([c.name for c in got], [NOT_LISTED])


class TheBlend(unittest.TestCase):
    def test_score_and_agreement_are_mixed_half_and_half(self) -> None:
        scored = [Candidate("Ken", 80), Candidate("Bram", 20), Candidate(NOT_LISTED, 0)]
        blended, raw, agree = blend(scored, ["Ken"] * 4 + ["Bram"])
        self.assertEqual([c.name for c in blended], ["Ken", "Bram", NOT_LISTED])
        self.assertAlmostEqual(blended[0].confidence, 0.5 * 80 + 0.5 * 80)
        self.assertAlmostEqual(blended[1].confidence, 0.5 * 20 + 0.5 * 20)
        self.assertEqual((raw, agree), ([80, 20, 0], [80, 20, 0]))

    def test_a_top_pick_the_samples_do_not_back_drops_in_the_blend(self) -> None:
        scored = [Candidate("Ken", 70), Candidate("Bram", 30), Candidate(NOT_LISTED, 0)]
        blended, _, _ = blend(scored, ["Bram"] * 5)
        self.assertEqual(blended[0].name, "Bram")

    def test_with_no_usable_samples_the_scores_stand(self) -> None:
        scored = [Candidate("Ken", 70), Candidate(NOT_LISTED, 0)]
        blended, raw, agree = blend(scored, [])
        self.assertEqual([c.confidence for c in blended], [70, 0])
        self.assertEqual(agree, [0.0, 0.0])
        self.assertEqual(raw, [70, 0])


class Asking(unittest.IsolatedAsyncioTestCase):
    async def test_a_sure_answer_with_agreeing_samples_is_accepted(self) -> None:
        ai = Scripted(scores(("Ken", 92), ("Bram", 4)), [pick("Ken")] * 5)
        got = await ask(ai, PASSAGE, "He", NAMES)
        assert got is not None
        self.assertEqual(len(ai.calls), 6)
        self.assertEqual(got.picks, 5)
        decision = decide(got.candidates)
        self.assertEqual((decision.verdict, decision.name), (Verdict.ACCEPT, "Ken"))
        self.assertIsNotNone(ai.calls[0][2])  # asked for structured JSON

    async def test_samples_that_disagree_pull_a_high_score_into_a_question_or_an_unnamed_one(
        self,
    ) -> None:
        ai = Scripted(
            scores(("Ken", 85), ("Bram", 10)), [pick("Ken"), pick("Bram")] * 2 + [pick("Bram")]
        )
        got = await ask(ai, PASSAGE, "He", NAMES)
        assert got is not None
        self.assertNotEqual(decide(got.candidates).verdict, Verdict.ACCEPT)

    async def test_a_failed_or_unreadable_sample_is_left_out(self) -> None:
        ai = Scripted(
            scores(("Ken", 90)),
            [pick("Ken"), RuntimeError("down"), "garbage", pick("Ken"), pick("Ken")],
        )
        got = await ask(ai, PASSAGE, "He", NAMES)
        assert got is not None
        self.assertEqual(got.picks, 3)

    async def test_if_the_scoring_call_fails_nothing_is_guessed(self) -> None:
        for first in (RuntimeError("down"), "not json"):
            with self.subTest(first=repr(first)):
                self.assertIsNone(await ask(Scripted(first, []), PASSAGE, "He", NAMES))

    async def test_a_refused_or_cut_scoring_reply_is_nothing(self) -> None:
        class Odd:
            async def complete(self, *a: Any, **k: Any) -> Reply:
                return Reply(scores(("Ken", 90)), cut=True)

        self.assertIsNone(await ask(Odd(), PASSAGE, "He", NAMES))

    async def test_the_log_holds_numbers_only(self) -> None:
        ai = Scripted(scores(("Ken", 92)), [pick("Ken")] * 5)
        with self.assertLogs("dmbot.memory.who_ask", "INFO") as logged:
            await ask(ai, "Secret passage about Ken", "He", NAMES)
        text = "\n".join(logged.output)
        self.assertNotIn("Ken", text)
        self.assertNotIn("Secret", text)

    async def test_no_samples_asked_for_means_the_scores_stand(self) -> None:
        ai = Scripted(scores(("Ken", 70), ("Bram", 5)), [])
        got = await ask(ai, PASSAGE, "He", NAMES, samples=0)
        assert got is not None
        self.assertEqual(len(ai.calls), 1)
        self.assertEqual(got.candidates[0].confidence, 70)


if __name__ == "__main__":
    unittest.main()
