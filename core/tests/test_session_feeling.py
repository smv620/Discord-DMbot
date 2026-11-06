"""How a session's claim about two characters' feelings is read (#3181): friend, ally and enemy are
`knows` plus one of the nine reputation words, an off-list word is dropped, and the extractor's
prompt offers exactly those words. Pure: no database. Invented names throughout."""

from __future__ import annotations

import unittest

from dmbot.memory import extractor, reputation_words
from dmbot.memory.claims import feeling_of, place_relationship
from dmbot.memory.models import NPC, PLAYER_CHARACTER
from dmbot.memory.ontology import Ontology
from dmbot.memory.voice import OWN, PARTY, voice


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


class Ends(unittest.TestCase):
    def test_a_feeling_is_only_between_two_characters(self) -> None:
        for ends in (("faction", "faction"), ("character", "item"), ("place", "place")):
            with self.subTest(ends=ends):
                self.assertIsNone(feeling_of(onto(), "enemy of", "", ends))
                self.assertIsNone(feeling_of(onto(), "close", "", ends))
                self.assertIsNone(feeling_of(onto(), "knows", "trusts", ends))

    def test_two_groups_at_war_stay_enemy_of(self) -> None:
        term, _, label = place_relationship(onto(), "enemy of", "faction", "faction")
        self.assertEqual((term.key, label), ("enemy_of", ""))

    def test_a_place_close_to_a_place_is_not_a_feeling(self) -> None:
        term, _, _ = place_relationship(onto(), "close", "place", "place")
        self.assertNotEqual(term.key, "knows")


class WhoSpeaksForTheFeeling(unittest.TestCase):
    def test_a_player_saying_an_npcs_feeling_is_what_they_believe(self) -> None:
        self.assertEqual(voice(NPC, "knows", "player_said", {7}, None, {1}, feeling=True), PARTY)
        self.assertEqual(voice(NPC, "knows", "dm_said", {1}, None, {1}, feeling=True), OWN)

    def test_a_pcs_own_player_speaks_for_its_feeling(self) -> None:
        self.assertEqual(
            voice(PLAYER_CHARACTER, "knows", "player_said", {7}, 7, {1}, feeling=True), OWN
        )

    def test_a_plain_meeting_is_nobodys_to_guard(self) -> None:
        self.assertEqual(voice(NPC, "knows", "player_said", {7}, None, {1}), OWN)


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
