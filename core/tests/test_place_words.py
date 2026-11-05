"""How something is tied to a place, in words (#3363): a thing that moves is never "is in".
Pure words, no database. Names are invented."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.memory import lore_read, place_words
from dmbot.memory.link_words import back_after, back_words, link_words, reads_both_ways
from dmbot.memory.models import CONFIRMED, Relation
from dmbot.ui.name_card import _rows, connection_line, sentence


class PlaceWords(unittest.TestCase):
    def test_a_place_inside_a_place_stays_is_in(self) -> None:
        self.assertEqual(link_words("located_in", "", False, "place"), "is in")
        self.assertEqual(back_words("located_in", "", False, "place"), "has")
        self.assertEqual(back_after("located_in", "", False, "place"), "inside it")

    def test_something_that_moves_with_no_word_was_last_seen(self) -> None:
        for kind in ("character", "player_character", "npc", "creature", "faction"):
            self.assertEqual(link_words("located_in", "", False, kind), "was last seen in", kind)
        self.assertEqual(back_words("located_in", "", False, "character"), "is where")
        self.assertEqual(back_after("located_in", "", False, "character"), "was last seen")

    def test_a_kind_the_caller_does_not_know_keeps_the_old_words(self) -> None:
        self.assertEqual(link_words("located_in", ""), "is in")

    def test_each_word_reads_forward_and_back(self) -> None:
        cases = {
            "lives in": ("lives in", "is home to", ""),
            "works at": ("works in", "is the workplace of", ""),
            "based in": ("works in", "is the workplace of", ""),  # a group works in a town too
            "is kept in": ("is kept in", "holds", ""),
            "grew up in": ("comes from", "is where", "comes from"),
            "visited": ("was last seen in", "is where", "was last seen"),
        }
        for detail, (forward, back, after) in cases.items():
            self.assertEqual(link_words("located_in", detail, False, "character"), forward)
            self.assertEqual(back_words("located_in", detail, False, "character"), back)
            self.assertEqual(back_after("located_in", detail, False, "character"), after)

    def test_the_past_stays_past_from_both_names(self) -> None:
        self.assertEqual(link_words("located_in", "lived in", False, "character"), "lived in")
        self.assertEqual(back_words("located_in", "lived in", False, "character"), "was home to")
        self.assertEqual(
            back_words("located_in", "worked in", False, "character"), "was the workplace of"
        )
        self.assertEqual(back_words("located_in", "was kept in", False, "item"), "held")
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

    def test_one_wording_for_each_word(self) -> None:
        self.assertEqual(place_words.canonical("works at"), "works in")
        self.assertEqual(place_words.canonical("Grew up in"), "comes from")
        self.assertEqual(place_words.canonical("lived"), "lived in")
        self.assertEqual(place_words.canonical("visited"), "was last seen in")
        self.assertIsNone(place_words.canonical("is in"))
        self.assertIsNone(place_words.canonical("knows"))

    def test_the_readers_wording_is_what_the_reader_is_told_to_say(self) -> None:
        # the six words of the instruction are the ones the code keeps (no drift between them)
        from dmbot.memory.extractor import SYSTEM

        for word in ("lives in", "lived in", "works in", "worked in", "is kept in", "was kept in",
                     "comes from", "was last seen in"):  # fmt: skip
            self.assertIn(f'"{word}"', SYSTEM)
            self.assertEqual(place_words.canonical(word), word)
        self.assertEqual(place_words.canonical("lives_in"), "lives in")  # (an ontology spelling)

    def test_the_lore_reader_is_told_the_same_words(self) -> None:
        for word in place_words.WORDS_OFFERED:
            self.assertIn(word, lore_read.shape_instructions(), word)
            self.assertIsNotNone(place_words.canonical(word), word)
        self.assertIn("worked in", lore_read.read_instructions())
        self.assertIn("was last seen in", lore_read.read_instructions())

    def test_the_sayings_a_text_uses_are_each_one_word(self) -> None:
        for said, kept in {
            "was born in": "comes from", "Born in": "comes from", "visited": "was last seen in",
            "  Worked   IN ": "worked in", "lived_in": "lived in", "is from": "comes from",
        }.items():  # fmt: skip
            self.assertEqual(place_words.canonical(said), kept, said)

    def test_a_place_word_on_a_place_still_reads_by_its_word(self) -> None:
        # a town that "lives in" another is odd, but it is shown as it was said, not "is in"
        self.assertEqual(link_words("located_in", "lives in", False, "place"), "lives in")

    def test_an_item_is_only_ever_kept_in_a_place(self) -> None:
        # (owner, 2026-11-04) it never lives, works, comes from or is last seen: it is kept
        self.assertEqual(link_words("located_in", "", False, "item"), "is kept in")
        self.assertEqual(back_words("located_in", "", False, "item"), "holds")
        for detail in ("was last seen in", "lives in", "works in", "comes from", "is kept in"):
            self.assertEqual(link_words("located_in", detail, False, "item"), "is kept in", detail)
        self.assertEqual(link_words("located_in", "lived in", False, "item"), "was kept in")
        self.assertEqual(back_words("located_in", "lived in", False, "item"), "held")
        self.assertEqual(place_words.canonical("was last seen in", "item"), "is kept in")
        self.assertEqual(place_words.canonical("was last seen in", "character"), "was last seen in")
        self.assertIsNone(place_words.canonical("is in", "item"))
        self.assertEqual(
            place_words.summary_phrases("", "item"), ("is kept there", "where it is kept")
        )
        # one place for an item whatever word was said
        self.assertEqual(place_words.slot("was last seen in", "item"), place_words.slot("", "item"))
        # and a character's place is unchanged
        self.assertEqual(link_words("located_in", "", False, "character"), "was last seen in")

    def test_the_prompts_tell_an_item_it_is_only_kept(self) -> None:
        from dmbot.memory.extractor import SYSTEM

        self.assertIn("An item is only ever", SYSTEM)
        for text in (lore_read.read_instructions(), lore_read.shape_instructions()):
            self.assertIn("an item is only ever kept", text)
            self.assertNotIn("group or item say whether", text)

    def test_an_item_with_no_word_or_is_in_is_kept_and_a_place_is_never_kept(self) -> None:
        self.assertEqual(place_words.forward("is in", "item"), "is kept in")
        self.assertEqual(place_words.forward("", "place"), "is in")
        self.assertEqual(place_words.forward("", "event"), "took place in")

    def test_an_event_took_place_in_a_town(self) -> None:
        self.assertEqual(link_words("located_in", "", False, "event"), "took place in")
        self.assertEqual(
            place_words.summary_phrases("", "event"), ("took place there", "where it took place")
        )
        self.assertEqual(
            place_words.summary_phrases("", "character"),
            ("was last seen there", "where they were last seen"),
        )

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
            entities={e.id: e for e in (self.tavin, self.town, self.region)},
            guarded=frozenset(),
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
            [(r.words, r.after) for r in rows],
            [("is home to", ""), ("was home to", ""), ("is where", "was last seen")],
        )

    def test_every_back_reading_on_the_towns_card_and_in_its_lines(self) -> None:
        silver = SimpleNamespace(
            id="s" * 32, name="Silver Lanterns", type="faction", status=CONFIRMED
        )
        sword = SimpleNamespace(id="w" * 32, name="Heartseeker", type="item", status=CONFIRMED)
        self.lookup.entities.update({silver.id: silver, sword.id: sword})
        rows = _rows(
            self.lookup,
            self.town.id,
            [
                self.relation(silver.id, self.town.id, "works in"),
                self.relation(sword.id, self.town.id, "is kept in"),
                self.relation(self.tavin.id, self.town.id, "grew up in"),
                self.relation(self.tavin.id, self.town.id, "worked in"),
            ],
            False,
        )
        self.assertEqual(
            [(r.words, r.other) for r in rows],
            [
                ("is the workplace of", "Silver Lanterns"),
                ("holds", "Heartseeker"),
                ("is where", "Tavin"),
                ("was the workplace of", "Tavin"),
            ],
        )
        self.assertEqual(connection_line(rows[0]), "is the workplace of **Silver Lanterns**")
        self.assertEqual(connection_line(rows[1]), "holds **Heartseeker**")
        self.assertEqual(connection_line(rows[2]), "is where **Tavin** comes from")

    def test_a_town_in_a_region_reads_has_inside_it_and_an_event_took_place(self) -> None:
        inside = self.relation(self.town.id, self.region.id)
        rows = _rows(self.lookup, self.region.id, [inside], False)
        self.assertEqual([connection_line(r) for r in rows], ["has **Aldmoor** inside it"])
        fair = SimpleNamespace(id="f" * 32, name="The Fair", type="event", status=CONFIRMED)
        self.lookup.entities[fair.id] = fair
        rows = _rows(self.lookup, self.town.id, [self.relation(fair.id, self.town.id)], False)
        self.assertEqual([connection_line(r) for r in rows], ["is where **The Fair** took place"])

    def test_a_place_with_no_kind_known_never_says_it_moves(self) -> None:
        # (the Connect menu has no kind to go by: the old words, which claim nothing)
        self.assertEqual(back_words("located_in", "", False, ""), "is linked to")

    def test_the_edit_note_does_not_turn_a_place_tie_round(self) -> None:
        self.assertFalse(reads_both_ways("located_in", "", False))


class Slots(unittest.TestCase):
    def test_a_word_and_its_tense_are_its_own_slot(self) -> None:
        self.assertEqual(place_words.slot("lives in"), (place_words.LIVES, False))
        self.assertEqual(place_words.slot("lived in"), (place_words.LIVES, True))
        self.assertEqual(place_words.slot("works at"), (place_words.WORKS, False))
        # no word, a visit and a sighting are one slot: the next one replaces the last
        self.assertEqual(place_words.slot(""), place_words.slot("visited"))
        self.assertEqual(place_words.slot("is revered in"), place_words.slot("was last seen in"))


if __name__ == "__main__":
    unittest.main()
