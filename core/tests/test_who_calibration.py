"""The calibration set for "who is he?" (#2790): its shape, and the scorer on stand-in answers."""

from __future__ import annotations

import unittest
from collections import Counter
from pathlib import Path

from dmbot.memory.who_calibration import Passage, load, score
from dmbot.memory.who_is_it import NOT_LISTED, Candidate

SET = Path(__file__).parent / "fixtures" / "who_is_it_calibration.json"


class TheSet(unittest.TestCase):
    def setUp(self) -> None:
        self.passages = load(SET)

    def test_about_fifty_passages_with_unique_ids(self) -> None:
        self.assertEqual(len(self.passages), 50)
        self.assertEqual(len({p.id for p in self.passages}), 50)

    def test_every_passage_holds_its_phrase_and_has_a_possible_answer(self) -> None:
        for p in self.passages:
            with self.subTest(id=p.id):
                self.assertIn(p.phrase.casefold(), p.text.casefold())
                self.assertTrue(p.answer == NOT_LISTED or p.answer in p.known, p.answer)
                self.assertIn(p.kind, ("pronoun", "description", "unnamed"))
                self.assertEqual(p.kind == "unnamed", p.answer == NOT_LISTED)

    def test_the_mix_covers_easy_hard_described_and_unnamed(self) -> None:
        kinds = Counter(p.kind for p in self.passages)
        self.assertGreaterEqual(kinds["pronoun"], 20)
        self.assertGreaterEqual(kinds["description"], 8)
        self.assertGreaterEqual(kinds["unnamed"], 10)

    def test_it_holds_no_real_looking_data(self) -> None:
        raw = SET.read_text(encoding="utf-8")
        for bad in ("@", "http", "discord", "dndbeyond"):
            self.assertNotIn(bad, raw.casefold())


class TheScorer(unittest.TestCase):
    def setUp(self) -> None:
        self.passages = load(SET)

    @staticmethod
    def oracle(p: Passage) -> list[Candidate]:
        return [Candidate(p.answer, 90), Candidate("Nobody Else", 4)]

    def test_an_oracle_is_always_right_and_everything_is_counted_once(self) -> None:
        report = score(self.passages, self.oracle)
        counts = report.counts()
        self.assertEqual(report.total, 50)
        self.assertEqual(sum(t for t, _ in counts.values()), 50)
        self.assertEqual(counts["accepted_high"][0] + counts["unnamed"][0], 50)
        self.assertTrue(all(total == right for total, right in counts.values()))

    def test_a_wrong_high_confidence_answer_is_counted_wrong(self) -> None:
        def always_first(p: Passage) -> list[Candidate]:
            return [Candidate(p.known[0], 85), Candidate(NOT_LISTED, 5)]

        report = score(self.passages, always_first)
        total, right = report.counts()["accepted_high"]
        self.assertEqual(total, 50)
        self.assertEqual(right, sum(p.known[0] == p.answer for p in self.passages))
        self.assertLess(right, total)

    def test_unsure_answers_go_to_the_band_and_the_questions(self) -> None:
        def unsure(p: Passage) -> list[Candidate]:
            rival = [Candidate(n, 44) for n in p.known if n != p.answer][:1]
            return [Candidate(p.answer, 45), *(rival if int(p.id[1:]) % 2 else [])]

        report = score([p for p in self.passages if p.kind != "unnamed"], unsure)
        counts = report.counts()
        self.assertGreater(counts["asked"][0], 0)  # 45 against 44 is a question
        self.assertGreater(counts["accepted_band"][0], 0)  # 45 alone is not
        self.assertEqual(counts["accepted_high"][0], 0)

    def test_nothing_known_is_an_unnamed_person(self) -> None:
        report = score(self.passages, lambda p: [])
        total, right = report.counts()["unnamed"]
        self.assertEqual(total, 50)
        self.assertEqual(right, sum(p.answer == NOT_LISTED for p in self.passages))


if __name__ == "__main__":
    unittest.main()
