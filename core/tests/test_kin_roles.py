"""Family roles on a family connection (#2457): which words, how each reads from each side, the
typed words that give a role, and that an old family connection with no role still reads "is family
of" both ways. The pure parts first, then the card, the relationship web and recall. Invented
names only."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.memory import kin
from dmbot.memory.link_words import (
    back_words,
    link_words,
    predicate_for,
    predicate_for_either,
    reads_both_ways,
)
from dmbot.ui import name_card
from dmbot.web import relationships


class TheWords(unittest.TestCase):
    def test_every_word_has_a_role_and_reads_both_sides_with_the_same_word(self) -> None:
        for word in kin.ROLES:
            with self.subTest(word):
                self.assertEqual(kin.role_of(word), word)
                self.assertEqual(kin.forward(word), f"is the {word} of")
                self.assertTrue(kin.backward(word).startswith("has a"))
                self.assertTrue(kin.backward(word).endswith(word))

    def test_the_article_fits_the_word(self) -> None:
        self.assertEqual(kin.backward("niece"), "has a niece")
        self.assertEqual(kin.backward("aunt"), "has an aunt")
        self.assertEqual(kin.backward("uncle"), "has an uncle")

    def test_no_gender_is_guessed_for_the_other_side(self) -> None:
        # "Mira is the niece of Aldric" says nothing of whether Aldric is an uncle or an aunt
        text = kin.backward("niece")
        for word in ("uncle", "aunt", "he", "she", "his", "her"):
            self.assertNotIn(word, text.split())

    def test_other_words_are_no_role(self) -> None:
        for detail in ("", "family", "my kin", "nieces", "sworn"):
            self.assertIsNone(kin.role_of(detail))
        self.assertEqual(kin.role_of("  Niece "), "niece")  # case and spaces do not matter

    def test_typed_words_give_the_role_and_which_side_they_read_from(self) -> None:
        for text in ("is the niece of", "niece of", "is a niece of", "Is The Niece Of", "niece to"):
            self.assertEqual(kin.parse(text), ("niece", False), text)
        self.assertEqual(kin.parse("is the grandmother of"), ("grandmother", False))  # not mother
        self.assertEqual(kin.parse("has a niece"), ("niece", True))
        self.assertEqual(kin.parse("has an uncle"), ("uncle", True))
        for text in ("is family of", "is the niece", "knows", "is the boss of", "has a friend"):
            self.assertIsNone(kin.parse(text), text)


class TheConnectionWords(unittest.TestCase):
    def test_a_role_reads_with_the_role_and_an_empty_one_as_before(self) -> None:
        self.assertEqual(link_words("kin_of", "niece"), "is the niece of")
        self.assertEqual(link_words("kin_of", ""), "is family of")
        self.assertEqual(link_words("kin_of", "my favourite relative"), "is family of")
        self.assertEqual(back_words("kin_of", "niece"), "has a niece")
        self.assertEqual(back_words("kin_of", ""), None)
        self.assertEqual(back_words("member_of", ""), "has as a member")

    def test_a_role_is_true_from_one_side_only(self) -> None:
        self.assertFalse(reads_both_ways("kin_of", "niece"))
        self.assertTrue(reads_both_ways("kin_of", ""))
        self.assertTrue(reads_both_ways("ally_of", "niece"))
        self.assertFalse(reads_both_ways("knows", ""))

    def test_typed_words_become_family_with_a_role(self) -> None:
        self.assertEqual(predicate_for("is the niece of"), ("kin_of", "niece"))
        self.assertEqual(predicate_for("is family of"), ("kin_of", ""))
        self.assertEqual(predicate_for("sworn on the ashes of")[0], "associated_with")

    def test_words_read_from_the_other_side_are_taken_only_where_the_names_can_swap(self) -> None:
        self.assertEqual(predicate_for("has a niece")[0], "associated_with")  # as before
        self.assertEqual(predicate_for_either("has a niece"), ("kin_of", "niece", True))
        self.assertEqual(predicate_for_either("is the niece of"), ("kin_of", "niece", False))
        self.assertEqual(predicate_for_either("knows"), ("knows", "", False))


class TheWebEdges(unittest.TestCase):
    def row(self, tag: str, a: str, b: str, detail: str) -> dict[str, Any]:
        return {"id": tag, "a": a, "b": b, "predicate": "kin_of", "detail": detail,
                "secret": False, "at": 1}  # fmt: skip

    def test_a_role_edge_has_a_direction_and_both_readings(self) -> None:
        (edge,) = relationships.edges_from([self.row("1", "b", "a", "niece")], can_edit=True)
        (link,) = edge["links"]
        self.assertEqual((link["words"], link["back"]), ("is the niece of", "has a niece"))
        self.assertEqual(link["direction"], "b-a")  # b is the niece: the words read b to a

    def test_an_old_family_edge_reads_both_ways_as_before(self) -> None:
        (edge,) = relationships.edges_from([self.row("1", "b", "a", "")], can_edit=True)
        (link,) = edge["links"]
        self.assertEqual((link["words"], link["direction"]), ("is family of", "both"))
        self.assertIsNone(link["back"])


def relation(subject: str, object_: str, detail: str) -> Any:
    return SimpleNamespace(
        id="r1", subject_id=subject, object_id=object_, predicate="kin_of", detail=detail,
        status="confirmed", secret=False,
    )  # fmt: skip


def entity(entity_id: str, name: str) -> Any:
    return SimpleNamespace(id=entity_id, name=name, status="confirmed", kind_key="npc")


class TheCard(unittest.TestCase):
    lookup = SimpleNamespace(
        entities={"mira": entity("mira", "Mira"), "aldric": entity("aldric", "Aldric")}
    )

    def words(self, card_of: str, detail: str) -> str:
        rows = name_card._rows(self.lookup, card_of, [relation("mira", "aldric", detail)], True)  # type: ignore[arg-type]
        (row,) = rows
        return f"{row.words} {row.other}"

    def test_each_card_reads_the_role_its_own_way(self) -> None:
        self.assertEqual(self.words("mira", "niece"), "is the niece of Aldric")
        self.assertEqual(self.words("aldric", "niece"), "has a niece Mira")

    def test_a_family_connection_with_no_role_reads_the_same_on_both_cards(self) -> None:
        self.assertEqual(self.words("mira", ""), "is family of Aldric")
        self.assertEqual(self.words("aldric", ""), "is family of Mira")

    def test_the_sentence_says_the_role(self) -> None:
        got = name_card.sentence(self.lookup, relation("mira", "aldric", "niece"))  # type: ignore[arg-type]
        self.assertEqual(got, "Mira is the niece of Aldric")
