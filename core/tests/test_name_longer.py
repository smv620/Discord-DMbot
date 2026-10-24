"""Longer names a suggested name may be part of (#2570). Names here are made up."""

from __future__ import annotations

import unittest

from dmbot.memory.name_longer import PLACE_WORDS, longer_names, widened


class LongerNames(unittest.TestCase):
    def test_the_words_after_a_possessive_are_offered_as_heard(self) -> None:
        quotes = ["…to **Harrow**'s keep and", "…the foothills of **Harrow**'s keep"]
        self.assertEqual(longer_names(quotes, "Harrow"), ["Harrow's keep"])

    def test_words_before_and_after_are_offered_most_said_first(self) -> None:
        quotes = [
            "the old **Caer Dineval** ice",
            "the old **Caer Dineval** road",
            "by Caer Dineval",
        ]
        got = longer_names(quotes, "Caer Dineval")
        self.assertEqual(got[0], "old Caer Dineval")

    def test_a_joining_word_never_starts_or_ends_a_candidate(self) -> None:
        got = longer_names(["we met **Harrow** and then", "ask **Harrow** about it"], "Harrow")
        self.assertNotIn("Harrow and", got)
        self.assertNotIn("Harrow and then", got)
        self.assertTrue(all(not c.casefold().endswith((" and", " the", " of")) for c in got))

    def test_at_most_three_and_none_for_a_name_nobody_extends(self) -> None:
        quotes = [f"**Oskar** {w} {w}" for w in ("grim", "tall", "wise", "old")] * 2
        self.assertLessEqual(len(longer_names(quotes, "Oskar")), 3)
        self.assertEqual(longer_names(["**Zed**."], "Zed"), [])
        self.assertEqual(longer_names(["x"], ""), [])


class Widened(unittest.TestCase):
    def test_a_place_word_after_the_same_name_widens_it(self) -> None:
        lines = ["we ride to Harrow's keep", "at Harrow's Keep again"]
        self.assertEqual(widened(lines, "Harrow", 2), "Harrow's Keep")

    def test_once_is_not_enough_and_other_words_never_widen(self) -> None:
        self.assertEqual(widened(["Harrow's keep"], "Harrow", 2), "")
        self.assertEqual(widened(["Harrow ate", "Harrow ate"], "Harrow", 2), "")
        self.assertIn("keep", PLACE_WORDS)

    def test_a_name_of_several_words_is_left_alone(self) -> None:
        self.assertEqual(widened(["Caer Dineval keep"] * 2, "Caer Dineval", 2), "")


if __name__ == "__main__":
    unittest.main()
