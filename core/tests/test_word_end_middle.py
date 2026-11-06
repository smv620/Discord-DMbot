"""Shortening the middle of a quote, and a statement line cut at a word end (#2757)."""

from __future__ import annotations

import unittest

from dmbot.memory import lore_knowledge
from dmbot.word_end import in_the_middle


class TheMiddleCut(unittest.TestCase):
    def test_a_text_that_fits_is_left_alone(self) -> None:
        self.assertEqual(in_the_middle("A short  quote.", 100), "A short quote.")

    def test_the_start_and_the_end_stay_and_no_word_is_cut(self) -> None:
        text = "Corvin hunted wolves. " + "word " * 80 + "His brother, Daric, vanished."
        out = in_the_middle(text, 120)
        self.assertLessEqual(len(out), 120)
        self.assertTrue(out.startswith("Corvin hunted"))
        self.assertTrue(out.endswith("His brother, Daric, vanished."))
        self.assertIn(" … ", out)
        for piece in out.split(" … "):
            self.assertTrue(all(w in text for w in piece.split()))

    def test_one_long_word_does_not_break_it(self) -> None:
        out = in_the_middle("x" * 500, 100)
        self.assertLessEqual(len(out), 100)


class TheStatementLine(unittest.TestCase):
    def test_a_long_statement_is_cut_at_a_word_end(self) -> None:
        line = lore_knowledge.line("fact", "word " * 100, "quote words")
        self.assertLessEqual(len(line), lore_knowledge.LINE_MAX)
        self.assertTrue(line.endswith("word"))  # (never "wor")


if __name__ == "__main__":
    unittest.main()
