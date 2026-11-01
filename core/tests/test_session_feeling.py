"""How a session's claim about two characters' feelings is read (#3181): friend, ally and enemy are
`knows` plus one of the nine reputation words, an off-list word is dropped, and the extractor's
prompt offers exactly those words. Pure: no database. Invented names throughout."""

from __future__ import annotations

import unittest

from dmbot.memory import extractor, reputation_words
from dmbot.memory.claims import feeling_of, place_relationship
from dmbot.memory.ontology import Ontology


def onto() -> Ontology:
    return Ontology.build()


class Feelings(unittest.TestCase):
    def test_knows_with_a_word_keeps_the_word(self) -> None:
        for word in reputation_words.WORDS:
            with self.subTest(word=word):
                found = feeling_of(onto(), "knows", word)
                assert found is not None
                self.assertEqual((found[0].key, found[1]), ("knows", word))

    def test_a_word_off_the_list_is_dropped_not_guessed(self) -> None:
        found = feeling_of(onto(), "knows", "fond of")
        assert found is not None
        self.assertEqual((found[0].key, found[1]), ("knows", ""))

    def test_knows_with_no_details_is_a_plain_meeting(self) -> None:
        found = feeling_of(onto(), "knows", "")
        assert found is not None
        self.assertEqual(found[1], "")

    def test_the_old_kinds_are_read_as_the_feeling_they_meant(self) -> None:
        for text, word in (
            ("friend of", "friendly"),
            ("is a friend of", "friendly"),
            ("ally of", "friendly"),
            ("is an ally of", "friendly"),
            ("enemy of", "hostile"),
            ("is an enemy of", "hostile"),
        ):
            with self.subTest(text=text):
                found = feeling_of(onto(), text)
                assert found is not None
                self.assertEqual((found[0].key, found[1]), ("knows", word))

    def test_a_word_said_as_the_relationship_is_that_feeling(self) -> None:
        found = feeling_of(onto(), "Distrusts")
        assert found is not None
        self.assertEqual((found[0].key, found[1]), ("knows", "distrusts"))

    def test_other_relationships_are_not_a_feeling(self) -> None:
        for text in ("owns", "serves", "is a rival of", "member of"):
            with self.subTest(text=text):
                self.assertIsNone(feeling_of(onto(), text))

    def test_placing_gives_the_word_as_the_label(self) -> None:
        term, from_object, label = place_relationship(
            onto(), "knows", "character", "character", details="trusts"
        )
        self.assertEqual((term.key, from_object, label), ("knows", False, "trusts"))
        term, _, label = place_relationship(onto(), "enemy of", "character", "character")
        self.assertEqual((term.key, label), ("knows", "hostile"))


class ThePrompt(unittest.TestCase):
    def test_it_offers_all_nine_words(self) -> None:
        for word in reputation_words.WORDS:
            with self.subTest(word=word):
                self.assertIn(word, extractor.SYSTEM)

    def test_it_never_asks_for_a_friend_or_enemy_relationship(self) -> None:
        self.assertIn(
            'Never use "ally", "friend" or "enemy" as a relationship',
            " ".join(extractor.SYSTEM.split()),
        )
