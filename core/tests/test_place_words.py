"""How something is tied to a place, in words (#3363): a thing that moves is never "is in".
Pure words, no database. Names are invented."""

from __future__ import annotations

import unittest

from dmbot.memory import place_words
from dmbot.memory.link_words import back_words, link_words


class PlaceWords(unittest.TestCase):
    def test_a_place_inside_a_place_stays_is_in(self) -> None:
        self.assertEqual(link_words("located_in", "", False, "place"), "is in")
        self.assertEqual(link_words("located_in", "", False, "event"), "is in")
        self.assertEqual(back_words("located_in", "", False, "place"), "is where you find")

    def test_something_that_moves_with_no_word_was_last_seen(self) -> None:
        for kind in ("character", "player_character", "npc", "creature", "faction", "item"):
            self.assertEqual(link_words("located_in", "", False, kind), "was last seen in", kind)
        self.assertEqual(back_words("located_in", "", False, "character"), "is where you last saw")

    def test_a_kind_the_caller_does_not_know_keeps_the_old_words(self) -> None:
        self.assertEqual(link_words("located_in", ""), "is in")

    def test_each_word_reads_forward_and_back(self) -> None:
        cases = {
            "lives in": ("lives in", "is home to"),
            "works at": ("works in", "is the workplace of"),
            "based in": ("is based in", "is the base of"),
            "is kept in": ("is kept in", "is where you keep"),
            "grew up in": ("comes from", "is the origin of"),
            "visited": ("was last seen in", "is where you last saw"),
        }
        for detail, (forward, back) in cases.items():
            self.assertEqual(link_words("located_in", detail, False, "character"), forward)
            self.assertEqual(back_words("located_in", detail, False, "character"), back)

    def test_the_past_stays_past_and_is_its_own_way_to_end(self) -> None:
        self.assertEqual(link_words("located_in", "lived in", False, "character"), "lived in")
        way = place_words.way_of("lived in")
        self.assertIsNotNone(way)
        assert way is not None
        self.assertEqual((way.key, way.past), (place_words.LIVES, True))
        self.assertFalse(place_words.way_of("lives in").past)  # type: ignore[union-attr]

    def test_a_word_a_place_gives_a_place_still_reads_by_its_word(self) -> None:
        self.assertEqual(link_words("located_in", "is based in", False, "faction"), "is based in")

    def test_other_connections_are_unchanged(self) -> None:
        self.assertEqual(link_words("owns", "", False, "character"), "owns")
        self.assertEqual(back_words("owns", "", False, "character"), "is owned by")


if __name__ == "__main__":
    unittest.main()
