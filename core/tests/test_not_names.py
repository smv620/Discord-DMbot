"""Pronouns are never suggested as names unless a line says they are one (#2358)."""

from __future__ import annotations

import unittest
from typing import ClassVar

from dmbot.names import not_names as nn

PRONOUN_FORMS = [
    "I",
    "me",
    "my",
    "mine",
    "myself",
    "we",
    "us",
    "our",
    "ours",
    "ourselves",
    "you",
    "your",
    "yours",
    "he",
    "him",
    "his",
    "himself",
    "she",
    "her",
    "hers",
    "herself",
    "it",
    "its",
    "itself",
    "they",
    "them",
    "their",
    "theirs",
    "themselves",
]
CONTRACTIONS = [
    "I'll",
    "I'm",
    "I'd",
    "I've",
    "we're",
    "we'll",
    "we've",
    "we'd",
    "he's",
    "he'll",
    "he'd",
    "she's",
    "she'll",
    "she'd",
    "they're",
    "they'll",
    "they'd",
    "they've",
    "it's",
    "it'll",
    "it'd",
    "you'll",
    "you're",
    "you've",
    "you'd",
    "that's",
    "there's",
]


class TheList(unittest.TestCase):
    def test_every_pronoun_form_is_dropped_in_any_case(self) -> None:
        for word in PRONOUN_FORMS:
            for form in (word, word.capitalize(), word.upper()):
                self.assertTrue(nn.is_not_name(form), form)
                self.assertFalse(nn.keep(form, "Then we went in"), form)

    def test_contractions_with_straight_and_curly_apostrophes_are_dropped(self) -> None:
        for word in CONTRACTIONS:
            for form in (word, word.replace("'", "’"), word.capitalize()):
                self.assertTrue(nn.is_not_name(form), form)

    def test_a_two_word_candidate_led_by_a_pronoun_is_dropped(self) -> None:
        for text in ("We Should", "He Said", "She Went", "They Left", "I Think"):
            self.assertTrue(nn.is_not_name(text), text)

    def test_sentence_openers_fail_only_at_the_start_of_a_sentence(self) -> None:
        for word in ("This", "That", "Someone", "Everyone", "Nobody", "These"):
            self.assertTrue(nn.is_not_name(word, sentence_start=True), word)
            self.assertFalse(nn.is_not_name(word), word)

    def test_real_names_that_only_look_like_one_are_kept(self) -> None:
        names = (
            *("Hermes", "Ivy", "Usher", "Theyra", "Hera", "Itch", "Wearn", "Hershel", "Meg"),
            *(
                "Oskar Vane",
                "Sheila",
                "Mine Hollow",
                "Our Lady",
                "His Grace",
                "Her Majesty",
                "Ourea",
            ),
        )
        for name in names:
            self.assertFalse(nn.is_not_name(name), name)
            self.assertTrue(nn.keep(name, "Oskar Vane said hello"), name)


class TheNamingCue(unittest.TestCase):
    def test_a_plain_cue_makes_the_word_after_it_a_name(self) -> None:
        for candidate, line in (
            ("We", "My name is We"),
            ("They", "I'm called They"),
            ("Us", "call me Us"),
            ("He", "his name is He"),
            ("She", "a gnome named She"),
            ("It", "they call him It"),
            ("Them", "she goes by Them"),
            ("We", "Your name is “We”"),
        ):
            self.assertTrue(nn.keep(candidate, line), line)

    def test_ordinary_sentences_do_not_count(self) -> None:
        for candidate, line in (
            ("We", "We went in"),
            ("He", "He said no"),
            ("They", "My name is Kesh and they left"),  # the cue names Kesh, not They
            ("We", "named after what we saw"),  # "after" follows the cue, not We
            ("I", "I think so"),
        ):
            self.assertFalse(nn.keep(candidate, line), line)

    def test_the_cue_is_for_its_own_line_only(self) -> None:
        self.assertFalse(nn.keep("We", "We went in"))
        self.assertTrue(nn.keep("We", "My name is We"))

    def test_filtered_keeps_order(self) -> None:
        self.assertEqual(nn.filtered(["Oskar", "We", "Mira", "I'll"], "okay"), ["Oskar", "Mira"])


if __name__ == "__main__":
    unittest.main()


class TheScan(unittest.TestCase):
    """The Check new names scan (`memory.scan`) never offers a pronoun (#2358)."""

    LINES: ClassVar[list[str]] = [
        "We went in. We went in. We went in again.",
        "I'll look. I'll look. They're here. They're here. She said hi. She said hi.",
        "He said no. He said no. Someone knocked. Someone knocked. This is it. This is it.",
        "We Should go. We Should go. It's a trap. It’s a trap.",
        "Then Oskar spoke. Ask Oskar. Oskar nodded, and Hermes and Ivy and Usher and Theyra left.",
        "Hermes waved. Ivy waved. Usher waved. Theyra waved.",
    ]

    def names(self, lines: list[str]) -> set[str]:
        from dmbot.memory import scan

        return {s.name for s in scan.find_new_names(lines, min_times=2)}

    def test_pronouns_and_contractions_are_never_offered(self) -> None:
        got = self.names(self.LINES)
        for bad in ("We", "I'll", "They're", "She", "He", "Someone", "This", "We Should", "It's"):
            self.assertNotIn(bad, got)
        self.assertNotIn("It’s", got)

    def test_real_names_that_start_like_a_pronoun_are_still_offered(self) -> None:
        self.assertEqual(
            self.names(self.LINES) & {"Oskar", "Hermes", "Ivy", "Usher", "Theyra"},
            {"Oskar", "Hermes", "Ivy", "Usher", "Theyra"},
        )

    def test_a_naming_cue_in_the_line_lets_it_through(self) -> None:
        got = self.names(["My name is We. My name is We.", "We went in. Then We left."])
        self.assertIn("We", got)
        got = self.names(["We went in. We went in. A gnome was named Hermes. Hermes laughed."])
        self.assertNotIn("We", got)


class TheOtherSources(unittest.TestCase):
    """The same list at the table-names learner and the recap's names to check (#2358)."""

    def test_table_names_never_learns_a_pronoun_as_a_name(self) -> None:
        from dmbot.memory.table_names import called_names

        for text in ("We, go in", "Okay, your turn, They", "I'll, run", "Okay, your turn, he’s"):
            self.assertEqual(called_names(text), [], text)
        self.assertEqual(called_names("Okay, your turn, Hermes."), ["Hermes"])

    def test_table_names_keeps_a_pronoun_the_line_names(self) -> None:
        from dmbot.memory.table_names import called_names

        self.assertEqual(called_names("My name is We, you're up, We."), ["We"])

    def test_recap_names_to_check_skip_pronouns_unless_a_line_names_them(self) -> None:
        from dmbot.recaps import draft as rd
        from tests import recap_fixtures as fx
        from tests.test_recaps import TRANSCRIPT, beats

        def kept(text: str, said: str) -> list[str]:
            return rd.kept_names(
                text,
                beats=beats(),
                transcript=TRANSCRIPT + " " + said,
                allowed_names=fx.NAMES,
                secret_names=[],
            )

        self.assertEqual(kept("We reached Zarathon.", "We went to Zarathon"), ["Zarathon"])
        self.assertEqual(kept("Then We spoke.", "My name is We"), ["We"])


class TheCueEdges(unittest.TestCase):
    def test_a_quoted_name_is_cued_and_a_quoted_pronoun_is_still_a_pronoun(self) -> None:
        self.assertTrue(nn.keep("She", "a gnome named 'She' waved"))
        self.assertTrue(nn.keep("She", "a gnome named ‘She’ waved"))
        self.assertTrue(nn.is_not_name("'We'"))
        self.assertTrue(nn.is_not_name("‘We’"))
        self.assertTrue(nn.is_not_name("we'll"))

    def test_the_word_after_named_must_be_a_name_not_an_object_or_filler(self) -> None:
        self.assertFalse(nn.keep("Him", "They named him Bob"))
        self.assertFalse(nn.keep("It", "They named it Fred"))
        self.assertEqual(nn.cued_names("He was named after the king"), frozenset())
