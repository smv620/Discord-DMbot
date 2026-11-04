"""The check before any name is proposed (#3394, #3385), with invented names."""

from __future__ import annotations

import unittest

from dmbot.names.not_names import why_not


class Shapes(unittest.TestCase):
    def test_shapes_decide_without_capitals(self) -> None:
        for text, why in [
            ("two", "number"), ("a few copper", "number"), ("two sleds", "number"),
            ("four miles per hour", "measure"), ("Four miles per hour", "measure"),
            ("five days ago", "measure"), ("current time", "time"),
            ("4:00 in the afternoon", "time"), ("Friend", "address"), ("my lord", "address"),
            ("Captain", "address"), ("Father", "address"),
        ]:  # fmt: skip
            self.assertEqual(why_not(text), why, text)

    def test_real_names_are_kept(self) -> None:
        for text in ["Captain Vel", "Speaker Masthew", "Sister Nora", "Third Tower",
                     "Second Wind", "Area 51", "Aldmoor", "Boy"]:  # fmt: skip
            self.assertIsNone(why_not(text, text=text), text)


class CommonNouns(unittest.TestCase):
    def test_a_plain_word_lower_case_everywhere_is_dropped(self) -> None:
        self.assertEqual(why_not("the dogs", text="he fed the dogs"), "common_noun")
        self.assertEqual(why_not("snow", text="the snow falls"), "common_noun")
        self.assertEqual(why_not("the lady", text="the lady said"), "common_noun")

    def test_capitals_cues_and_two_lines_keep_it(self) -> None:
        self.assertIsNone(why_not("the Kennel", text="at the Kennel"))
        self.assertIsNone(why_not("Boy", text="Boy ran"))
        self.assertIsNone(why_not("boy", text="the boy ran", offered=2))  # two lines offer it
        self.assertIsNone(why_not("bent", text="a gnome named bent", cued=frozenset({"bent"})))

    def test_a_name_not_found_in_the_text_cannot_be_judged_and_is_kept(self) -> None:
        self.assertIsNone(why_not("the dogs", text="nothing about them"))

    def test_a_plain_phrase_with_a_small_word_inside_is_judged_whole(self) -> None:
        phrase = "heads of people poking up over a ridge"
        self.assertEqual(why_not(phrase, text=phrase), "common_noun")
