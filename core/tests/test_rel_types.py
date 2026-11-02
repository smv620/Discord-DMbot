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
        import dataclasses

        t = TYPES[0]
        changed = dataclasses.replace(t, is_text=t.is_text + " One more word.")
        self.assertNotEqual(rel_types.text_hash(t), rel_types.text_hash(changed))
        self.assertEqual(len({rel_types.text_hash(x) for x in TYPES}), len(TYPES))


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
        got = rel_types.decide("serves", m, threshold=0.80)
        self.assertEqual((got.kind, got.source, got.reason), ("serves", "ai", "below_threshold"))

    def test_close_to_the_second_the_readers_own_type_stands(self) -> None:
        m = rel_types.Match("kin_of", 0.9, "knows", 0.89)
        self.assertEqual(rel_types.decide("serves", m, threshold=0.80).reason, "close_second")

    def test_at_the_bar_exactly_it_is_used(self) -> None:
        m = rel_types.Match("kin_of", 0.80, "knows", 0.5)
        self.assertEqual(rel_types.decide("serves", m, threshold=0.80).kind, "kin_of")

    def test_no_match_keeps_the_readers_type(self) -> None:
        self.assertEqual(rel_types.decide("serves", None).source, "ai")

    def test_a_form_of_address_the_reader_itself_found_is_not_stored_either(self) -> None:
        weak = rel_types.Match("kin_of", 0.5, "knows", 0.4)
        self.assertFalse(rel_types.decide(TERM_OF_ADDRESS, weak).stored)
        self.assertFalse(rel_types.decide(TERM_OF_ADDRESS, None).stored)

    def test_a_number_that_is_not_finite_never_passes_as_a_confident_match(self) -> None:
        nan = float("nan")
        self.assertEqual(
            rel_types.decide("serves", rel_types.Match("kin_of", nan, "knows", 0.1)).source, "ai"
        )
        self.assertIsNone(
            rel_types.nearest([1.0, 0.0], {"kin_of": [nan, 0.0], "knows": [1.0, 0.0]})
        )

    def test_a_vector_of_another_length_or_too_few_types_is_no_match(self) -> None:
        self.assertIsNone(rel_types.nearest([1.0, 0.0, 0.0], self.VECS))
        self.assertIsNone(rel_types.nearest([1.0, 0.0], {"kin_of": [1.0, 0.0]}))

    def test_legacy_and_place_keys_are_never_a_target(self) -> None:
        vecs = {
            "ally_of": [1.0, 0.0],
            "located_in": [1.0, 0.0],
            "kin_of": [0.6, 0.8],
            "knows": [0.0, 1.0],
        }
        got = rel_types.nearest([1.0, 0.0], vecs)
        assert got is not None
        self.assertEqual((got.key, got.second_key), ("kin_of", "knows"))

    def test_equal_scores_are_ordered_by_key_not_by_input_order(self) -> None:
        a = rel_types.nearest([1.0, 0.0], {"owns": [1.0, 0.0], "carries": [1.0, 0.0]})
        b = rel_types.nearest([1.0, 0.0], {"carries": [1.0, 0.0], "owns": [1.0, 0.0]})
        self.assertEqual(a, b)

    def test_the_margin_is_a_boundary_with_exact_values(self) -> None:
        enough = rel_types.Match("kin_of", 1.0, "knows", 0.75)
        self.assertEqual(rel_types.decide("serves", enough, margin=0.25).kind, "kin_of")
        short = rel_types.Match("kin_of", 1.0, "knows", 0.76)
        self.assertEqual(rel_types.decide("serves", short, margin=0.25).reason, "close_second")

    def test_the_owners_eighty_percent_is_the_precision_not_a_cosine(self) -> None:
        self.assertEqual(rel_types.PRECISION_TARGET, 0.80)
        self.assertNotEqual(rel_types.THRESHOLD, rel_types.PRECISION_TARGET)


def sc(key: str, score: float, second: float, expected: str) -> rel_types.Scored:
    return rel_types.Scored(key, score, second, expected)


class TheChoice(unittest.TestCase):
    """`choose` with a toy set: scores that rise with confidence, wrong ones among the low ones."""

    CASES = (
        sc("kin_of", 0.95, 0.40, "kin_of"), sc("knows", 0.90, 0.40, "knows"),
        sc("serves", 0.85, 0.50, "serves"), sc("owns", 0.80, 0.40, "owns"),
        sc("mentors", 0.75, 0.70, "serves"),  # wrong, but a close second
        sc("leads", 0.70, 0.30, "member_of"),  # wrong, a clear lead at a low score
        sc("carries", 0.65, 0.30, "carries"),
    )  # fmt: skip

    def test_it_takes_the_loosest_pair_that_still_meets_the_target(self) -> None:
        got = rel_types.choose(self.CASES)
        assert got is not None
        # the margin keeps out the close second; one wrong clear lead in six is 83%, enough
        self.assertGreaterEqual(got.precision, rel_types.PRECISION_TARGET)
        self.assertEqual((got.used, round(got.precision, 2)), (6, 0.83))
        self.assertEqual(got.threshold, 0.65)

    def test_a_stricter_target_uses_fewer_matches(self) -> None:
        loose = rel_types.choose(self.CASES, target=0.80)
        strict = rel_types.choose(self.CASES, target=1.0)
        assert loose is not None and strict is not None
        self.assertLessEqual(strict.used, loose.used)
        self.assertEqual((strict.precision, strict.used), (1.0, 4))

    def test_when_no_pair_meets_the_target_nothing_is_used(self) -> None:
        wrong = (sc("kin_of", 0.9, 0.1, "knows"), sc("knows", 0.8, 0.1, "kin_of"))
        self.assertIsNone(rel_types.choose(wrong))

    def test_non_finite_scores_are_ignored_and_no_cases_is_none(self) -> None:
        self.assertIsNone(rel_types.choose(()))
        self.assertIsNone(rel_types.choose((sc("kin_of", float("nan"), 0.1, "kin_of"),)))


class TheStance(unittest.TestCase):
    def test_a_stance_is_stored_as_one_of_the_nine(self) -> None:
        for word in ("at war", "allies", "partners in a plan", "companions in a cause"):
            with self.subTest(word):
                self.assertIn(rel_types.nine_word(word), reputation_words.WORDS)

    def test_the_nine_words_are_themselves(self) -> None:
        for word in reputation_words.WORDS:
            self.assertEqual(rel_types.nine_word(word), word)

    def test_the_mapped_words_are_the_owners(self) -> None:
        want = {
            "at war": "hostile", "allies": "friendly", "partners in a plan": "trusts",
            "companions in a cause": "trusts", "sworn enemies": "sworn enemy",
        }  # fmt: skip
        for word, nine in want.items():
            self.assertEqual(rel_types.nine_word(word), nine)
        self.assertEqual(rel_types.nine_word("At-War"), "hostile")

    def test_something_that_is_not_text_stores_nothing(self) -> None:
        self.assertIsNone(rel_types.nine_word(None))
        self.assertIsNone(rel_types.nine_word(7))

    def test_anything_else_stores_nothing(self) -> None:
        self.assertIsNone(rel_types.nine_word("it is complicated"))


class TheGoldenSet(unittest.TestCase):
    def test_it_is_big_enough_and_every_expectation_is_known(self) -> None:
        self.assertGreaterEqual(len(rel_golden.CASES), 60)
        allowed = {*BY_KEY, rel_golden.NONE, rel_golden.ALIAS}
        for case in rel_golden.CASES:
            self.assertIn(case.expected, allowed, case.description)

    def test_every_not_this_line_has_a_case_that_lands_on_the_type_it_names(self) -> None:
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

    def test_the_epithet_and_surname_rules_have_their_cases(self) -> None:
        notes = {c.covers for c in rel_golden.CASES}
        self.assertIn("note6", notes)
        self.assertIn("note7", notes)
        self.assertEqual({c.expected for c in rel_golden.CASES if c.covers == "note6"}, {"alias"})

    def test_a_shared_surname_alone_must_not_land_on_kin(self) -> None:
        unrelated = [c for c in rel_golden.CASES if c.covers == "note7" and c.expected != "kin_of"]
        self.assertGreaterEqual(len(unrelated), 2)
        self.assertTrue(all(c.expected == rel_golden.NONE for c in unrelated))


class TheGuardian(unittest.TestCase):
    def test_a_guardian_is_a_family_role_of_its_own(self) -> None:
        self.assertIn("guardian", kin.ROLES)
        self.assertEqual(kin.role_of("guardian"), "guardian")

    def test_a_guardian_alone_gives_nothing_and_beside_a_real_parent_nothing_extra(self) -> None:
        alone = [kin_derive.Stated("1", "toris", "guardian", "pip")]
        self.assertEqual(kin_derive.derive(alone), [])
        both = [*alone, kin_derive.Stated("2", "bran", "parent", "pip"),
                kin_derive.Stated("3", "pip", "sibling", "wren")]  # fmt: skip
        got = {(d.a_id, d.role, d.b_id) for d in kin_derive.derive(both)}
        self.assertEqual(
            got, {("bran", "parent", "wren")}
        )  # (only the real parent's, none for toris)

    def test_the_lore_reader_is_told_which_way_a_guardian_runs(self) -> None:
        from dmbot.memory import lore_read

        text = " ".join(lore_read.shape_instructions().split())
        self.assertIn("guardian = a is the guardian of b", text)

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
