"""The calibration set for "who is he?" (#2790): its shape, and the scorer on stand-in answers."""

from __future__ import annotations

import re
import unittest
from collections import Counter
from pathlib import Path

from dmbot.memory.who_calibration import ASK, Passage, load, score
from dmbot.memory.who_is_it import NOT_LISTED, Candidate

SET = Path(__file__).parent / "fixtures" / "who_is_it_calibration.json"


class TheSet(unittest.TestCase):
    def setUp(self) -> None:
        self.passages = load(SET)

    def test_about_fifty_passages_with_unique_ids(self) -> None:
        self.assertEqual(len(self.passages), 52)
        self.assertEqual(len({p.id for p in self.passages}), 52)

    def test_every_passage_holds_its_phrase_and_has_a_possible_answer(self) -> None:
        for p in self.passages:
            with self.subTest(id=p.id):
                self.assertIn(p.phrase.casefold(), p.text.casefold())
                self.assertTrue(p.answer in (NOT_LISTED, ASK) or p.answer in p.known, p.answer)
                self.assertIn(p.kind, ("pronoun", "description", "unnamed", "ambiguous"))
                self.assertEqual(p.kind == "unnamed", p.answer == NOT_LISTED)
                self.assertEqual(p.kind == "ambiguous", p.answer == ASK)

    def test_the_right_name_is_in_the_text_before_the_phrase_that_needs_it(self) -> None:
        """A reader (or the AI) only has what comes before the pronoun (#2790 section 1)."""
        for p in self.passages:
            if p.answer in (NOT_LISTED, ASK):
                continue
            with self.subTest(id=p.id):
                hits = [m.start() for m in re.finditer(rf"\b{re.escape(p.phrase)}\b", p.text, re.I)]
                self.assertTrue(hits)
                self.assertTrue(0 <= p.text.find(p.answer) < hits[-1], p.text)

    def test_an_ambiguous_passage_has_two_people_it_could_be(self) -> None:
        for p in self.passages:
            if p.kind == "ambiguous":
                with self.subTest(id=p.id):
                    self.assertGreaterEqual(len([n for n in p.known if n in p.text]), 2)

    def test_the_mix_covers_easy_hard_described_and_unnamed(self) -> None:
        kinds = Counter(p.kind for p in self.passages)
        self.assertGreaterEqual(kinds["pronoun"], 20)
        self.assertGreaterEqual(kinds["description"], 8)
        self.assertGreaterEqual(kinds["unnamed"], 10)
        self.assertGreaterEqual(kinds["ambiguous"], 6)

    def test_it_holds_no_real_looking_data(self) -> None:
        raw = SET.read_text(encoding="utf-8")
        for bad in ("@", "http", "discord", "dndbeyond"):
            self.assertNotIn(bad, raw.casefold())


class TheScorer(unittest.TestCase):
    def setUp(self) -> None:
        self.passages = load(SET)
        self.n = len(self.passages)
        self.open = sum(p.kind == "ambiguous" for p in self.passages)

    @staticmethod
    def oracle(p: Passage) -> list[Candidate]:
        """Sure where the text settles it; an even split where it does not."""
        if p.answer == ASK:
            return [Candidate(p.known[0], 45), Candidate(p.known[1], 44)]
        return [Candidate(p.answer, 90), Candidate("Nobody Else", 4)]

    def test_an_oracle_is_always_right_and_each_passage_is_counted_once(self) -> None:
        counts = score(self.passages, self.oracle).counts()
        self.assertTrue(all(total == right for total, right in counts.values()), counts)
        self.assertEqual(counts["ambiguous"][0], self.open)
        settled = self.n - self.open
        self.assertEqual(counts["accepted_high"][0] + counts["unnamed"][0], settled)
        self.assertEqual(counts["asked"][0], 0)  # the questions are the ambiguous line's

    def test_an_overconfident_accept_on_an_ambiguous_passage_is_wrong_twice_over(self) -> None:
        def sure_of_the_first(p: Passage) -> list[Candidate]:
            return [Candidate(p.known[0], 95), Candidate(NOT_LISTED, 3)]

        counts = score(self.passages, sure_of_the_first).counts()
        self.assertEqual(counts["ambiguous"], (self.open, 0))
        total, right = counts["accepted_high"]
        self.assertEqual(total, self.n)
        self.assertEqual(right, sum(p.known[0] == p.answer for p in self.passages))

    def test_a_wrong_high_confidence_answer_is_counted_wrong(self) -> None:
        def always_first(p: Passage) -> list[Candidate]:
            return [Candidate(p.known[0], 85), Candidate(NOT_LISTED, 5)]

        total, right = score(self.passages, always_first).counts()["accepted_high"]
        self.assertEqual(total, self.n)
        self.assertLess(right, total)

    def test_the_band_comes_from_the_cleaned_top_not_the_raw_numbers(self) -> None:
        """A raw 150 is held to 100, and a NaN is dropped: 85 and 90 both clear 80 either way,
        but a lone 60 next to a NaN must land in the band, not in the high line."""
        one = [p for p in self.passages if p.kind == "pronoun"][:1]
        report = score(one, lambda p: [Candidate(p.answer, 60), Candidate("X", float("nan"))])
        counts = report.counts()
        self.assertEqual((counts["accepted_high"][0], counts["accepted_band"][0]), (0, 1))
        report = score(one, lambda p: [Candidate(p.answer, 150)])
        self.assertEqual(report.counts()["accepted_high"], (1, 1))

    def test_unsure_answers_go_to_the_band_and_the_questions(self) -> None:
        def unsure(p: Passage) -> list[Candidate]:
            rival = [Candidate(n, 44) for n in p.known if n != p.answer][:1]
            return [Candidate(p.answer, 45), *(rival if int(p.id[1:]) % 2 else [])]

        named = [p for p in self.passages if p.kind in ("pronoun", "description")]
        counts = score(named, unsure).counts()
        self.assertGreater(counts["asked"][0], 0)  # 45 against 44 is a question
        self.assertGreater(counts["accepted_band"][0], 0)  # 45 alone is not
        self.assertEqual(counts["accepted_high"][0], 0)

    def test_nothing_known_is_an_unnamed_person_for_the_settled_ones(self) -> None:
        counts = score(self.passages, lambda p: []).counts()
        self.assertEqual(counts["unnamed"][0], self.n - self.open)
        self.assertEqual(counts["unnamed"][1], sum(p.answer == NOT_LISTED for p in self.passages))
        self.assertEqual(counts["ambiguous"], (self.open, 0))


if __name__ == "__main__":
    unittest.main()
