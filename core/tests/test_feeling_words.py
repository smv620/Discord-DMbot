"""The words `link_words` writes for a feeling read back as that feeling (#3606): "knows (wary)"
typed into "Words for connection" is a `knows` with the word, not a catch-all labelled with it."""

from __future__ import annotations

import unittest

from dmbot.memory import reputation_words
from dmbot.memory.link_words import predicate_for, predicate_for_either


class ReadBack(unittest.TestCase):
    def test_each_of_the_nine_words_round_trips(self) -> None:
        for word in reputation_words.WORDS:
            with self.subTest(word):
                words = f"knows ({word})"  # (how `link_words` words it for the DM)
                stored = "" if word == "neutral" else word
                self.assertEqual(predicate_for(words), ("knows", stored))
                self.assertEqual(predicate_for_either(words), ("knows", stored, False))

    def test_spacing_case_and_hyphens_do_not_matter(self) -> None:
        self.assertEqual(predicate_for("Knows ( Sworn-Enemy )"), ("knows", "sworn enemy"))
        self.assertEqual(predicate_for("knows(wary)"), ("knows", "wary"))

    def test_a_word_outside_the_nine_stays_the_catch_all(self) -> None:
        self.assertEqual(predicate_for("knows (adores)"), ("associated_with", "knows (adores)"))
        self.assertEqual(predicate_for("knows ()"), ("associated_with", "knows ()"))
        self.assertEqual(
            predicate_for("knows (wary) well"), ("associated_with", "knows (wary) well")
        )

    def test_neutral_stores_no_word_as_a_lore_read_does(self) -> None:
        self.assertEqual(predicate_for("knows (neutral)"), ("knows", ""))

    def test_plain_words_are_as_before(self) -> None:
        self.assertEqual(predicate_for("is a member of"), ("member_of", ""))
