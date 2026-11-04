"""How something is tied to a place, in words (#3363): a thing that moves is never "is in".
Pure words, no database. Names are invented."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.memory import place_words
from dmbot.memory.link_words import back_words, link_words, reads_both_ways
from dmbot.memory.models import CONFIRMED, Relation
from dmbot.ui.name_card import _rows, sentence


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
            "based in": ("works in", "is the workplace of"),  # a group works in a town too
            "is kept in": ("is kept in", "is where you keep"),
            "grew up in": ("comes from", "is the origin of"),
            "visited": ("was last seen in", "is where you last saw"),
        }
        for detail, (forward, back) in cases.items():
            self.assertEqual(link_words("located_in", detail, False, "character"), forward)
            self.assertEqual(back_words("located_in", detail, False, "character"), back)

    def test_the_past_stays_past_from_both_names(self) -> None:
        self.assertEqual(link_words("located_in", "lived in", False, "character"), "lived in")
        self.assertEqual(back_words("located_in", "lived in", False, "character"), "was home to")
        self.assertEqual(
            back_words("located_in", "worked in", False, "character"), "was the workplace of"
        )
        self.assertEqual(
            back_words("located_in", "was kept in", False, "item"), "was where you kept"
        )
        way, now = place_words.way_of("lived in"), place_words.way_of("lives in")
        self.assertIsNotNone(way)
        self.assertIsNotNone(now)
        assert way is not None and now is not None
        # one word, two tenses: "lived in" is history and never the present home (#3363)
        self.assertEqual((way.key, way.past), (place_words.LIVES, True))
        self.assertEqual((now.key, now.past), (place_words.LIVES, False))

    def test_a_group_works_in_a_town_like_a_character(self) -> None:
        self.assertEqual(link_words("located_in", "works in", False, "faction"), "works in")
        self.assertEqual(link_words("located_in", "is based in", False, "faction"), "works in")
        self.assertEqual(
            back_words("located_in", "works in", False, "faction"), "is the workplace of"
        )

    def test_the_word_is_read_without_case_or_spacing_and_other_details_are_no_word(self) -> None:
        self.assertEqual(link_words("located_in", "  Lives   IN ", False, "npc"), "lives in")
        for odd in ("in", "is revered in", "someone trapped in a place"):
            self.assertIsNone(place_words.way_of(odd), odd)
        for kind in ("deity", "person", "creature"):  # these move too
            self.assertEqual(link_words("located_in", "", False, kind), "was last seen in", kind)
        for kind in ("concept", "quest", ""):  # these do not, or are not known
            self.assertEqual(link_words("located_in", "", False, kind), "is in", kind)

    def test_a_node_summary_reads_by_the_word(self) -> None:
        self.assertEqual(
            place_words.summary_phrases("lives in"), ("lives there", "where they live")
        )
        self.assertEqual(
            place_words.summary_phrases("lived in"), ("lived there", "where they lived")
        )
        self.assertEqual(
            place_words.summary_phrases(""), ("tied to it", "tied to this place")
        )  # nothing claimed about when or how

    def test_other_connections_are_unchanged(self) -> None:
        self.assertEqual(link_words("owns", "", False, "character"), "owns")
        self.assertEqual(back_words("owns", "", False, "character"), "is owned by")


class OnTheCard(unittest.TestCase):
    """The Discord card says it the same way (the lookup stand-in holds only what it reads)."""

    def setUp(self) -> None:
        def entity(eid: str, name: str, kind: str) -> SimpleNamespace:
            return SimpleNamespace(id=eid, name=name, type=kind, status=CONFIRMED)

        self.tavin = entity("t" * 32, "Tavin", "character")
        self.town = entity("a" * 32, "Aldmoor", "place")
        self.region = entity("r" * 32, "The Reach", "place")
        self.lookup: Any = SimpleNamespace(
            entities={e.id: e for e in (self.tavin, self.town, self.region)}
        )

    def relation(self, subject: str, obj: str, detail: str = "") -> Relation:
        return Relation(
            "x" * 32, subject, "located_in", obj, detail, 1.0, CONFIRMED, "dm", (),
            None, None, None, None, False, 0,
        )  # fmt: skip

    def test_a_character_is_never_is_in_and_a_town_still_is(self) -> None:
        sighting = self.relation(self.tavin.id, self.town.id)
        self.assertEqual(sentence(self.lookup, sighting), "Tavin was last seen in Aldmoor")
        home = self.relation(self.tavin.id, self.town.id, "lives in")
        self.assertEqual(sentence(self.lookup, home), "Tavin lives in Aldmoor")
        inside = self.relation(self.town.id, self.region.id)
        self.assertEqual(sentence(self.lookup, inside), "Aldmoor is in The Reach")

    def test_the_towns_own_card_reads_each_back_by_its_word(self) -> None:
        rows = _rows(
            self.lookup,
            self.town.id,
            [
                self.relation(self.tavin.id, self.town.id, "lives in"),
                self.relation(self.tavin.id, self.town.id, "lived in"),
                self.relation(self.tavin.id, self.town.id),
            ],
            False,
        )
        self.assertEqual(
            [r.words for r in rows], ["is home to", "was home to", "is where you last saw"]
        )

    def test_the_edit_note_does_not_turn_a_place_tie_round(self) -> None:
        self.assertFalse(reads_both_ways("located_in", "", False))


if __name__ == "__main__":
    unittest.main()
