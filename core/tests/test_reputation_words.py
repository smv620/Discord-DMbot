"""The nine feeling words and the five steps they become (#3175)."""

from __future__ import annotations

import unittest

from dmbot.memory import reputation_words as rw
from dmbot.memory import standing


class TheNineWords(unittest.TestCase):
    def test_there_are_nine_and_those_with_a_score_run_worst_to_best(self) -> None:
        self.assertEqual(len(rw.WORDS), 9)
        scores = [rw.score_of(w) for w in rw.WORDS if rw.score_of(w) is not None]
        self.assertEqual(scores, sorted(scores))  # (worst to best, never out of order)

    def test_each_word_becomes_one_of_the_dms_five_steps(self) -> None:
        self.assertEqual(
            {w: rw.score_of(w) for w in rw.WORDS},
            {
                "sworn enemy": -80, "hostile": -80, "distrusts": -40, "wary": 0, "neutral": None,
                "trusts": 40, "friendly": 40, "close": 80, "devoted": 80,
            },
        )  # fmt: skip
        for word in rw.WORDS:
            score = rw.score_of(word)
            if score is not None:  # the stored score reads back as the step it came from
                self.assertEqual(standing.step_of(score), rw.step_of_word(word))

    def test_every_word_survives_word_of_and_the_steps_reached_are_the_scales_five(self) -> None:
        for word in rw.WORDS:
            self.assertEqual(rw.word_of(word), word)
        self.assertEqual({rw.step_of_word(w) for w in rw.WORDS} - {None}, set(standing.STEPS))

    def test_score_of_does_no_tidying_of_its_own(self) -> None:
        self.assertIsNone(rw.score_of("Friendly"))  # (the caller passes a word from word_of)
        self.assertEqual(rw.word_of("neutral"), "neutral")
        for blank in ("   ", "-", "_"):
            self.assertIsNone(rw.word_of(blank), blank)
        self.assertIsNone(rw.word_of("sworn\u2013enemy"))  # (an en dash is not a hyphen: dropped)

    def test_neutral_stores_nothing_because_no_opinion_is_how_it_is_shown(self) -> None:
        self.assertIsNone(rw.score_of(rw.NEUTRAL))
        self.assertIsNone(rw.step_of_word(rw.NEUTRAL))

    def test_a_word_is_read_loosely_in_spelling_and_not_at_all_in_meaning(self) -> None:
        for raw in ("Sworn-Enemy", "sworn_enemy", "  SWORN   enemy ", "sworn enemy"):
            self.assertEqual(rw.word_of(raw), "sworn enemy", raw)
        self.assertEqual(rw.word_of("Friendly"), "friendly")
        for off in ("best friends", "nemesis", "", "friendlyish", None, 40, ["friendly"]):
            self.assertIsNone(rw.word_of(off), off)
        self.assertIsNone(rw.score_of("nemesis"))
        self.assertIsNone(rw.step_of_word("nemesis"))


if __name__ == "__main__":
    unittest.main()
