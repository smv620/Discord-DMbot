"""Campaign memory rules without a database: names, the ontology core, and checks."""

import unittest
from dataclasses import replace

from dmbot.memory.checks import (
    CONTRADICTION,
    TOO_MANY,
    WRONG_OBJECT,
    WRONG_SUBJECT,
    check_relation,
    duplicate_of,
    ordered,
    same_time,
)
from dmbot.memory.models import CONFIRMED, PROPOSED, REJECTED, MemoryRuleError, Relation, name_key
from dmbot.memory.ontology import (
    CORE_PREDICATES,
    CORE_TYPES,
    SYNONYMS,
    Ontology,
    PredicateTerm,
    TypeTerm,
)

A, B, C = "a" * 32, "b" * 32, "c" * 32


def rel(
    subject: str = A,
    predicate: str = "ally_of",
    obj: str = B,
    *,
    rid: str = "f" * 32,
    status: str = PROPOSED,
    span: tuple[int | None, int | None] = (None, None),
    game: tuple[int | None, int | None] = (None, None),
) -> Relation:
    return Relation(rid, subject, predicate, obj, "", 0.9, status, "dm", (), *span, *game, False, 0)


class NameKeys(unittest.TestCase):
    def test_spelling_marks_are_ignored(self) -> None:
        self.assertEqual(name_key("Ka'zeth"), "kazeth")
        self.assertEqual(name_key("KA-ZETH"), "kazeth")
        self.assertEqual(name_key("Ka’zeth"), "kazeth")
        self.assertEqual(name_key("Éowyn"), "eowyn")
        self.assertEqual(name_key("  Oskar   Vane "), "oskar vane")

    def test_a_split_name_is_a_different_key(self) -> None:
        # "Ka Zeth" is a mishearing for the Transcript Cleaner, not the same spelling.
        self.assertNotEqual(name_key("Ka Zeth"), name_key("Ka'zeth"))


class CoreOntology(unittest.TestCase):
    def setUp(self) -> None:
        self.onto = Ontology.build()

    def test_core_is_consistent(self) -> None:
        type_keys = {t.key for t in CORE_TYPES}
        for t in CORE_TYPES:
            self.assertTrue(t.parent is None or t.parent in type_keys, t.key)
        for p in CORE_PREDICATES:
            for key in (*p.subject_types, *p.object_types):
                self.assertIn(key, type_keys, p.key)
            for other in p.conflicts_with:
                self.assertIn(other, {q.key for q in CORE_PREDICATES})
            if p.symmetric:
                self.assertEqual(set(p.subject_types), set(p.object_types), p.key)
        for synonym, target in SYNONYMS.items():
            self.assertIn(target, {q.key for q in CORE_PREDICATES}, synonym)

    def test_plain_labels(self) -> None:
        for term in (*CORE_TYPES, *CORE_PREDICATES):
            for word in ("ontology", "entity", "predicate", "graph"):
                self.assertNotIn(word, term.label.lower())
                self.assertNotIn(word, term.description.lower())

    def test_kinds_inherit(self) -> None:
        self.assertTrue(self.onto.is_a("npc", "character"))
        self.assertTrue(self.onto.is_a("player_character", "character"))
        self.assertFalse(self.onto.is_a("character", "npc"))
        self.assertFalse(self.onto.is_a("place", "character"))

    def test_unknown_or_retired_terms_are_refused(self) -> None:
        with self.assertRaises(MemoryRuleError):
            self.onto.active_type("spaceship")
        retired = TypeTerm("ship", "item", "ship", "A ship.", core=False, status="deprecated")
        with self.assertRaises(MemoryRuleError):
            Ontology.build([retired]).active_type("ship")

    def test_core_wins_over_a_campaign_term_with_the_same_key(self) -> None:
        fake = TypeTerm("npc", "place", "fake", "Not an NPC.", core=False)
        self.assertTrue(Ontology.build([fake]).types["npc"].core)


class Extensions(unittest.TestCase):
    def setUp(self) -> None:
        self.onto = Ontology.build()

    def check(self, key: str, label: str = "a label") -> None:
        self.onto.check_new_term(key, label, "What it means.", ["An example."], "Needed.")

    def test_reuse_before_creating(self) -> None:
        for key in ("friends_with", "lives_in", "worships", "ally_off"):
            with self.subTest(key=key), self.assertRaisesRegex(MemoryRuleError, "existing"):
                self.check(key)
        with self.assertRaisesRegex(MemoryRuleError, "ally_of"):
            self.check("comrade", "friend of")

    def test_a_good_new_term_passes(self) -> None:
        self.check("born_in", "was born in")
        self.check("ship", "ship")

    def test_a_new_term_needs_its_paperwork(self) -> None:
        with self.assertRaises(MemoryRuleError):
            self.onto.check_new_term("born_in", "born in", "", ["x"], "Needed.")
        with self.assertRaises(MemoryRuleError):
            self.onto.check_new_term("born_in", "born in", "Where.", [], "Needed.")
        with self.assertRaises(MemoryRuleError):
            self.onto.check_new_term("born_in", "born in", "Where.", ["x"], " ")
        with self.assertRaises(MemoryRuleError):
            self.onto.check_new_term("Born In!", "born in", "Where.", ["x"], "Needed.")

    def test_new_type_needs_a_known_parent(self) -> None:
        self.onto.check_new_type(TypeTerm("ship", "item", "ship", "A ship.", core=False))
        with self.assertRaises(MemoryRuleError):
            self.onto.check_new_type(TypeTerm("ship", None, "ship", "A ship.", core=False))
        with self.assertRaises(MemoryRuleError):
            self.onto.check_new_type(TypeTerm("ship", "vehicle", "ship", "A ship.", core=False))

    def test_new_predicate_checks_its_kinds(self) -> None:
        good = PredicateTerm("born_in", "was born in", "Birthplace.", ("character",), ("place",))
        self.onto.check_new_predicate(good)
        with self.assertRaises(MemoryRuleError):
            self.onto.check_new_predicate(replace(good, object_types=("planet",)))
        with self.assertRaises(MemoryRuleError):
            self.onto.check_new_predicate(replace(good, symmetric=True))
        with self.assertRaises(MemoryRuleError):
            self.onto.check_new_predicate(replace(good, conflicts_with=("nope",)))


BORN_IN = PredicateTerm(
    "born_in",
    "was born in",
    "Birthplace.",
    ("character",),
    ("place",),
    max_per_subject=1,
    core=False,
)


class Checks(unittest.TestCase):
    def setUp(self) -> None:
        self.onto = Ontology.build(extra_predicates=[BORN_IN])

    def test_kinds_must_fit(self) -> None:
        new = rel(A, "located_in", B)
        self.assertEqual(check_relation(self.onto, new, "npc", "place", []), [])
        kinds = [p.kind for p in check_relation(self.onto, new, "spell", "npc", [])]
        self.assertEqual(kinds, [WRONG_SUBJECT, WRONG_OBJECT])

    def test_two_birthplaces_are_flagged(self) -> None:
        first = rel(A, "born_in", B, rid="1" * 32, status=CONFIRMED)
        second = rel(A, "born_in", C)
        problems = check_relation(self.onto, second, "npc", "place", [first])
        self.assertEqual([(p.kind, p.other_id) for p in problems], [(TOO_MANY, first.id)])

    def test_one_place_at_a_time_is_fine(self) -> None:
        # Facts are history: Cerric was in Brynwater last session, Thornewick now.
        then = rel(A, "located_in", B, rid="1" * 32, span=(100, 200))
        now = rel(A, "located_in", C, span=(200, None))
        self.assertEqual(check_relation(self.onto, now, "npc", "place", [then]), [])

    def test_rejected_facts_dont_count(self) -> None:
        old = rel(A, "born_in", B, rid="1" * 32, status=REJECTED)
        self.assertEqual(check_relation(self.onto, rel(A, "born_in", C), "npc", "place", [old]), [])

    def test_ally_and_enemy_at_once_is_a_contradiction(self) -> None:
        ally = rel(A, "ally_of", B, rid="1" * 32)
        enemy = rel(B, "enemy_of", A)  # either direction
        problems = check_relation(self.onto, enemy, "npc", "npc", [ally])
        self.assertEqual([(p.kind, p.other_id) for p in problems], [(CONTRADICTION, ally.id)])

    def test_ally_then_enemy_is_history_not_a_contradiction(self) -> None:
        ally = rel(A, "ally_of", B, rid="1" * 32, span=(100, 300))
        enemy = rel(A, "enemy_of", B, span=(300, None))
        self.assertEqual(check_relation(self.onto, enemy, "npc", "npc", [ally]), [])

    def test_game_time_separates_facts_once_both_have_it(self) -> None:
        a = rel(A, "ally_of", B, rid="1" * 32, game=(0, 10))
        b = rel(A, "enemy_of", B, game=(10, None))
        self.assertFalse(same_time(a, b))
        self.assertTrue(same_time(a, replace(b, from_game_time=None)))

    def test_two_way_facts_are_stored_one_way(self) -> None:
        ally = self.onto.predicates["ally_of"]
        owns = self.onto.predicates["owns"]
        self.assertEqual(ordered(ally, B, A), (A, B))
        self.assertEqual(ordered(owns, B, A), (B, A))

    def test_same_fact_twice_is_a_duplicate(self) -> None:
        old = rel(A, "ally_of", B, rid="1" * 32)
        self.assertEqual(duplicate_of(rel(A, "ally_of", B), [old]), old)
        self.assertIsNone(duplicate_of(rel(A, "enemy_of", B), [old]))
        self.assertIsNone(duplicate_of(rel(A, "ally_of", B), [replace(old, status=REJECTED)]))
