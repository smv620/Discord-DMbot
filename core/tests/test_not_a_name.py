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

    def test_a_title_alone_from_the_shared_list_is_an_address(self) -> None:
        # (the same list the short-name rule reads, `TITLE_WORDS`, so the two never disagree)
        for text in ("Major", "Doctor", "Mayor", "Sergeant", "Reverend", "Mistress", "King"):
            self.assertEqual(why_not(text), "address", text)

    def test_a_describing_word_alone_is_left_to_the_other_checks(self) -> None:
        for text in ("Old", "Little", "Grand", "Saint"):
            self.assertIsNone(why_not(text, text=text), text)
        for text in ("Major Ursa", "Doctor Hale", "Duke Tran"):
            self.assertIsNone(why_not(text, text=text), text)

    def test_a_lone_title_the_text_names_someone_by_is_kept(self) -> None:
        self.assertIsNone(why_not("Major", cued=frozenset({"major"})))
        self.assertEqual(why_not("Friend", cued=frozenset({"friend"})), "address")  # (never)

    def test_titles_that_are_also_first_names_are_still_asked(self) -> None:
        for text in ("Sage", "Earl", "Bishop", "Prince"):
            self.assertIsNone(why_not(text, text=text), text)

    def test_a_lone_title_in_lower_case_is_an_address(self) -> None:
        self.assertEqual(why_not("king", text="the king said", offered=2), "address")

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

    def test_capitalised_real_names_that_contain_shape_words_are_kept(self) -> None:
        for text in ["the Dawn Court", "the Midnight Market", "Old Time", "Dawn", "Midnight",
                     "Seven", "Nine Hells", "First Light", "Last Hope", "One-Eyed Jack",
                     "Day of Rest", "Father Tobias", "Night Watch"]:  # fmt: skip
            self.assertIsNone(why_not(text, text=text), text)

    def test_a_title_with_a_capital_behind_an_article_is_judged_by_the_lines(self) -> None:
        self.assertIsNone(why_not("the Speaker", text="the Speaker rose"))
        self.assertIsNone(why_not("the Lady", text="the Lady of Pain"))
        self.assertEqual(why_not("Lady"), "address")  # said alone, a title is no name


class Clauses(unittest.TestCase):
    """A clause cut out of a sentence is no name (#3394); real names with small words stay."""

    def test_a_linking_word_inside_or_at_the_start_makes_it_a_clause(self) -> None:
        for text in ("twenty miles from Aldmoor", "Quillgate to the ford", "prophecy to Tavin",
                     "in Elvish", "past Fenwick", "the people who hired Mira",
                     "Tavin to shove Brin into the water"):  # fmt: skip
            self.assertEqual(why_not(text, text=text), "clause", text)

    def test_real_names_with_small_words_are_kept(self) -> None:
        for text in ("Tower of Joy", "Heart of chardalyn", "Fort at the Pass", "the Kennel",
                     "Lord Quillgate", "Bridge of Sighs", "Cask and Cleaver"):  # fmt: skip
            self.assertNotEqual(why_not(text, text=text), "clause", text)

    def test_the_six_word_rule_was_left_out_on_purpose(self) -> None:
        self.assertNotEqual(
            why_not("Name Known In The Old Stories", text="Name Known In The Old Stories"), "clause"
        )
