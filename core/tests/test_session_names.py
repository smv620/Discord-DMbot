"""How a name is said in a session claim (#3634): the words a name is judged by. Pure; invented
names."""

from __future__ import annotations

import unittest

from dmbot.memory import descriptions
from dmbot.memory.claims import Extracted, _a_kind_of_creature, tidy_ends, tidy_name
from dmbot.memory.models import name_key


class Tidy(unittest.TestCase):
    def test_a_bracketed_note_and_a_leading_a_or_an_are_not_the_name(self) -> None:
        self.assertEqual(tidy_name("Okthandom's child (no name given yet)"), "Okthandom's child")
        self.assertEqual(tidy_name("Valyra's sibling (either Ok or Nysarra)"), "Valyra's sibling")
        self.assertEqual(tidy_name("an owlbear"), "owlbear")
        self.assertEqual(tidy_name("a beggar woman"), "beggar woman")
        self.assertEqual(tidy_name("a hooded figure [maybe two]"), "hooded figure")

    def test_a_bracket_that_tells_two_things_apart_stays_and_names_are_not_stripped(self) -> None:
        self.assertEqual(tidy_name("Mira (the baker)"), "Mira (the baker)")
        self.assertEqual(tidy_name("Dragon (Red)"), "Dragon (Red)")
        self.assertEqual(tidy_name("Ren (maybe two)"), "Ren")
        self.assertEqual(tidy_name("Ren (Ok, or Nysarra)"), "Ren")
        for name in ("A. Smith", "An-Lu", "An Lu", "A Ling", "A Tavin", "A beggar woman"):
            self.assertEqual(tidy_name(name), name, name)

    def test_a_name_the_campaign_already_holds_as_said_is_left_alone(self) -> None:
        claim = Extracted("Bram", "carries", "a rusty dagger", "", ("1",), "dm_said")
        kept = tidy_ends(claim, {name_key("a rusty dagger")})
        self.assertIs(kept, claim)
        self.assertEqual(tidy_ends(claim).object, "rusty dagger")

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
        for name in ("Owlbear Mama", "Tavin", "Snot", "Wolf", "Azer", "Mimic", "Giles"):
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
        for text in (
            "barbarians", "Garrick", "Mira's sister Ren", "owlbears", "Cultist Mara", "the cultist",
        ):  # fmt: skip
            self.assertFalse(descriptions.is_described_person(text), text)
