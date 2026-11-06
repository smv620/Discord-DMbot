"""How a name is said in a session claim (#3634): the words a name is judged by. Pure; invented
names."""

from __future__ import annotations

import unittest

from dmbot.memory import descriptions
from dmbot.memory.claims import Extracted, _a_kind_of_creature, tidy_ends, tidy_name


class Tidy(unittest.TestCase):
    def test_a_bracketed_note_and_a_leading_a_or_an_are_not_the_name(self) -> None:
        self.assertEqual(tidy_name("Okthandom's child (no name given yet)"), "Okthandom's child")
        self.assertEqual(tidy_name("Valyra's sibling (either Ok or Nysarra)"), "Valyra's sibling")
        self.assertEqual(tidy_name("an owlbear"), "owlbear")
        self.assertEqual(tidy_name("A beggar woman"), "beggar woman")
        self.assertEqual(tidy_name("a hooded figure [maybe two]"), "hooded figure")

    def test_the_is_part_of_a_title_and_nothing_is_stripped_to_nothing(self) -> None:
        self.assertEqual(tidy_name("The Everlasting Rime"), "The Everlasting Rime")
        self.assertEqual(tidy_name("(unknown)"), "(unknown)")
        self.assertEqual(tidy_name("a"), "a")
        self.assertEqual(
            tidy_name("Aldmoor"), "Aldmoor"
        )  # (an "a" inside a word is not an article)
        self.assertEqual(tidy_name("Anya"), "Anya")

    def test_a_claim_is_the_same_object_when_nothing_changed(self) -> None:
        claim = Extracted("Bram", "knows", "Mira", "", ("1",), "dm_said")
        self.assertIs(tidy_ends(claim), claim)
        self.assertEqual(
            tidy_ends(Extracted("Bram", "fights", "an owlbear", "", ("1",), "dm_said")).object,
            "owlbear",
        )


class Creatures(unittest.TestCase):
    def test_kinds_the_rules_hold_and_not_a_named_one(self) -> None:
        for kind in ("owlbears", "an owlbear", "owlbear", "three goblins"):
            self.assertTrue(_a_kind_of_creature(kind), kind)
        for name in ("Owlbear Mama", "Tavin", "Snot"):
            self.assertFalse(_a_kind_of_creature(name), name)


class DescribedPeople(unittest.TestCase):
    def test_described_without_a_name_go_the_unnamed_person_way(self) -> None:
        for text in (
            "maul barbarian",
            "unknown patron",
            "brewner's human daughter",
            "valyra's sibling",
            "the hooded attacker",
        ):
            self.assertTrue(descriptions.is_described_person(text), text)

    def test_a_bare_class_word_or_a_named_person_is_not(self) -> None:
        for text in ("barbarians", "Garrick", "Mira's sister Ren", "owlbears"):
            self.assertFalse(descriptions.is_described_person(text), text)
