"""Relationship typing by meaning (#3244): the owner's twelve types as data, the match and its
bar, the stance mapping, the golden set's coverage and the guardian rule. Pure: the embedder is a
stand-in, so these say nothing about how good Voyage's numbers are (dev1's measured run does)."""

from __future__ import annotations

import unittest
from typing import ClassVar

from dmbot.memory import kin, kin_derive, rel_golden, rel_types, reputation_words
from dmbot.memory.rel_type_texts import BY_KEY, TERM_OF_ADDRESS, TYPES


class TheTypes(unittest.TestCase):
    def test_there_are_twelve_with_eight_examples_and_the_owners_keys(self) -> None:
        self.assertEqual(
            [t.key for t in TYPES],
            ["kin_of", "knows", "rival_of", "serves", "sworn_to", "mentors", "leads", "member_of",
             "held_captive_by", "owns", "carries", "term_of_address"],
        )  # fmt: skip
        self.assertTrue(all(len(t.examples) == 8 for t in TYPES))

    def test_every_description_has_the_words_to_embed_well(self) -> None:
        for t in TYPES:
            with self.subTest(t.key):
                self.assertGreaterEqual(len(t.is_text.split()), 60)

    def test_ally_of_and_enemy_of_are_not_targets(self) -> None:
        self.assertNotIn("ally_of", BY_KEY)
        self.assertNotIn("enemy_of", BY_KEY)

    def test_not_this_is_never_embedded(self) -> None:
        for t in TYPES:
            text = rel_types.embed_text(t)
            for line in t.not_this:
                self.assertNotIn(line, text)

    def test_only_a_form_of_address_is_not_stored(self) -> None:
        self.assertEqual([t.key for t in TYPES if not t.stored], [TERM_OF_ADDRESS])

    def test_a_changed_text_has_another_hash(self) -> None:
        hashes = {rel_types.text_hash(t) for t in TYPES}
        self.assertEqual(len(hashes), len(TYPES))


class TheMatch(unittest.TestCase):
    VECS: ClassVar[dict[str, list[float]]] = {
        "kin_of": [1.0, 0.0],
        "knows": [0.9, 0.436],
        "term_of_address": [0.0, 1.0],
    }

    def test_the_nearest_and_the_second_are_found(self) -> None:
        m = rel_types.nearest([1.0, 0.0], self.VECS)
        assert m is not None
        self.assertEqual((m.key, m.second_key), ("kin_of", "knows"))

    def test_a_clear_close_match_decides(self) -> None:
        m = rel_types.nearest([0.0, 1.0], self.VECS)
        got = rel_types.decide("knows", m)
        self.assertEqual((got.kind, got.source, got.stored), (TERM_OF_ADDRESS, "embedding", False))

    def test_a_form_of_address_is_counted_not_stored(self) -> None:
        m = rel_types.Match(TERM_OF_ADDRESS, 0.95, "kin_of", 0.5)
        self.assertFalse(rel_types.decide("kin_of", m).stored)

    def test_below_the_bar_the_readers_own_type_stands(self) -> None:
        m = rel_types.Match("kin_of", 0.79, "knows", 0.1)
        got = rel_types.decide("serves", m)
        self.assertEqual((got.kind, got.source, got.reason), ("serves", "ai", "below_bar"))

    def test_close_to_the_second_the_readers_own_type_stands(self) -> None:
        m = rel_types.Match("kin_of", 0.9, "knows", 0.89)
        self.assertEqual(rel_types.decide("serves", m).reason, "close_second")

    def test_at_the_bar_exactly_it_is_used(self) -> None:
        m = rel_types.Match("kin_of", 0.80, "knows", 0.5)
        self.assertEqual(rel_types.decide("serves", m).kind, "kin_of")

    def test_no_match_keeps_the_readers_type(self) -> None:
        self.assertEqual(rel_types.decide("serves", None).source, "ai")

    def test_the_bar_is_the_owners_eighty_percent(self) -> None:
        self.assertEqual(rel_types.BAR, 0.80)


class TheStance(unittest.TestCase):
    def test_a_stance_is_stored_as_one_of_the_nine(self) -> None:
        for word in ("at war", "allies", "partners in a plan", "companions in a cause"):
            with self.subTest(word):
                self.assertIn(rel_types.nine_word(word), reputation_words.WORDS)

    def test_the_nine_words_are_themselves(self) -> None:
        for word in reputation_words.WORDS:
            self.assertEqual(rel_types.nine_word(word), word)

    def test_anything_else_stores_nothing(self) -> None:
        self.assertIsNone(rel_types.nine_word("it is complicated"))


class TheGoldenSet(unittest.TestCase):
    def test_it_is_big_enough_and_every_expectation_is_known(self) -> None:
        self.assertGreaterEqual(len(rel_golden.CASES), 60)
        allowed = {*BY_KEY, rel_golden.NONE, rel_golden.ALIAS}
        for case in rel_golden.CASES:
            self.assertIn(case.expected, allowed, case.description)

    def test_every_not_this_line_has_a_case_that_lands_on_the_type_it_names(self) -> None:
        covered = {c.covers: c.expected for c in rel_golden.CASES if isinstance(c.covers, tuple)}
        for t in TYPES:
            for i, line in enumerate(t.not_this):
                with self.subTest(f"{t.key}: {line}"):
                    cases = [c for c in rel_golden.CASES if c.covers == (t.key, i)]
                    self.assertTrue(cases, "no case covers this line")
                    target = line.split("→")[-1].strip()
                    wanted = {k for k in BY_KEY if k in target}
                    if any(k in target for k in ("located_in", "travels_with", "unlinked")):
                        wanted.add(rel_golden.NONE)
                    self.assertTrue({c.expected for c in cases} & wanted, line)
        self.assertTrue(covered)

    def test_the_epithet_and_surname_rules_have_their_cases(self) -> None:
        notes = {c.covers for c in rel_golden.CASES}
        self.assertIn("note6", notes)
        self.assertIn("note7", notes)
        self.assertEqual({c.expected for c in rel_golden.CASES if c.covers == "note6"}, {"alias"})

    def test_a_shared_surname_alone_must_not_land_on_kin(self) -> None:
        trade = [c for c in rel_golden.CASES if "trade surname" in c.description]
        self.assertTrue(trade)
        self.assertTrue(all(c.expected == rel_golden.NONE for c in trade))


class TheGuardian(unittest.TestCase):
    def test_a_guardian_is_a_family_role_of_its_own(self) -> None:
        self.assertIn("guardian", kin.ROLES)
        self.assertEqual(kin.role_of("guardian"), "guardian")

    def test_a_guardian_is_never_a_parent_for_the_worked_out_rules(self) -> None:
        links = [
            kin_derive.Stated("1", "toris", "guardian", "pip"),
            kin_derive.Stated("2", "pip", "sibling", "wren"),
            kin_derive.Stated("3", "pip", "parent", "tam"),
        ]
        got = {(d.a_id, d.role, d.b_id) for d in kin_derive.derive(links)}
        self.assertNotIn(("toris", "parent", "wren"), got)  # no "parent of a sibling"
        self.assertNotIn(("toris", "grandparent", "tam"), got)  # no grandparent through one


if __name__ == "__main__":
    unittest.main()
