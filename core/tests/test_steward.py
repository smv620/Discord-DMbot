"""The ontology steward's fixed rules (#1298): rules words are never terms, D&D words that plain
English misleads on, and one `decide_term`. No database, Discord or network."""

from __future__ import annotations

import logging
import unittest

from dmbot.memory.models import CONDITIONS, MemoryRuleError
from dmbot.memory.ontology import (
    CORE_PREDICATES,
    FALLBACK_PREDICATE,
    REASON_BETWEEN,
    REASON_CLOSE_MEANING,
    REASON_EXACT,
    REASON_RULES_WORD,
    REASON_SHAPE,
    REASON_SPELLING,
    REASON_SYNONYM,
    RULES_SYNONYMS,
    RULES_WORDS,
    SYNONYMS,
    Accepted,
    Ontology,
    Proposal,
    Refused,
    Reused,
    Tally,
    decide_term,
    rules_word_for,
    rules_words,
)

ONTO = Ontology.build()


def ask(key: str, label: str = "", **kw: object) -> Proposal:
    return Proposal(
        key, label or key.replace("_", " "), subject_types=("character",),
        object_types=("character",), **kw,  # type: ignore[arg-type]
    )  # fmt: skip


class RulesWords(unittest.TestCase):
    def test_every_rules_word_is_refused_by_name_label_or_synonym(self) -> None:
        for word in sorted(RULES_WORDS):
            decision = decide_term(ONTO, ask(word.replace(" ", "_")))
            self.assertEqual(decision, Refused(REASON_RULES_WORD, decision.fallback), word)  # type: ignore[union-attr]
            by_label = decide_term(ONTO, ask("some_new_term", word.title()))
            self.assertIsInstance(by_label, Refused, word)

    def test_the_srd_condition_names_come_from_the_rules_data(self) -> None:
        names = rules_words()
        for condition in ("blinded", "grappled", "unconscious", "exhaustion", "prone"):
            self.assertIn(condition, names)
        self.assertIsInstance(decide_term(ONTO, ask("poisoned")), Refused)

    def test_the_story_state_words_are_not_rules_words(self) -> None:
        for state in CONDITIONS:
            self.assertNotIn(state, rules_words(), state)

    def test_each_dnd_word_that_english_misleads_on_is_the_rules_concept(self) -> None:
        for word, meant in RULES_SYNONYMS.items():
            self.assertEqual(rules_word_for(word), meant, word)
            decision = decide_term(ONTO, ask(word))
            self.assertEqual(
                (type(decision), getattr(decision, "reason", None)), (Refused, REASON_RULES_WORD)
            )
        for needed in ("check", "save", "turn", "concentration", "advantage", "rest", "attunement"):
            self.assertTrue(needed in RULES_SYNONYMS or needed in rules_words(), needed)

    def test_a_synonym_of_a_story_relationship_is_a_reuse_not_a_refusal(self) -> None:
        for word, term in SYNONYMS.items():
            self.assertIn(term, {p.key for p in CORE_PREDICATES})
            self.assertEqual(decide_term(ONTO, ask(word)), Reused(term, REASON_SYNONYM), word)

    def test_the_existing_new_term_check_refuses_a_rules_word_too(self) -> None:
        with self.assertRaisesRegex(MemoryRuleError, "rules"):
            ONTO.check_new_term("saving_throw", "Saving throw", "x", ["x"], "x")
        with self.assertRaisesRegex(MemoryRuleError, "rules"):
            ONTO.check_new_term("turn", "Something else", "x", ["x"], "x")  # as a key

    def test_an_everyday_word_is_a_fair_label_for_a_story_relationship(self) -> None:
        ONTO.check_new_term("keeps_watch_over", "Reach", "x", ["x"], "x")  # no error: a label


class Deciding(unittest.TestCase):
    def test_the_same_name_or_label_reuses_the_term(self) -> None:
        self.assertEqual(decide_term(ONTO, ask("owns")), Reused("owns", REASON_EXACT))
        self.assertEqual(
            decide_term(ONTO, ask("brand_new_thing", "Is a member of")),
            Reused("member_of", REASON_EXACT),
        )

    def test_a_near_spelling_is_reused(self) -> None:
        self.assertEqual(decide_term(ONTO, ask("locatedin")), Reused("located_in", REASON_SPELLING))
        self.assertEqual(decide_term(ONTO, ask("serve")), Reused("serves", REASON_SPELLING))

    def test_an_unrelated_word_with_a_valid_domain_and_range_is_accepted(self) -> None:
        got = decide_term(ONTO, ask("born_in"))
        self.assertEqual(got, Accepted("born_in", meaning_checked=False))

    def test_a_kind_of_thing_is_judged_among_kinds(self) -> None:
        got = decide_term(ONTO, Proposal("ship", "Ship", kind="type"))
        self.assertEqual(got, Accepted("ship"))
        self.assertIsInstance(decide_term(ONTO, Proposal("item", "Item", kind="type")), Reused)

    def test_a_relationship_that_connects_nothing_known_is_refused_with_its_fact_kept(self) -> None:
        for bad in (
            Proposal("born_in", "Born in"),
            Proposal("born_in", "Born in", subject_types=("character",), object_types=("zzz",)),
        ):
            got = decide_term(ONTO, bad)
            assert isinstance(got, Refused)
            self.assertEqual(got.reason, REASON_SHAPE)

    def test_a_refusal_keeps_the_fact_on_the_catch_all_with_the_words_as_its_label(self) -> None:
        got = decide_term(ONTO, ask("check", "Check"))
        assert isinstance(got, Refused)
        self.assertEqual(
            (got.fallback.predicate, got.fallback.label), (FALLBACK_PREDICATE, "Check")
        )
        between = decide_term(ONTO, ask("bound_by", "Bound by"), lambda *_: ("between", None))
        assert isinstance(between, Refused)
        self.assertEqual(between.reason, REASON_BETWEEN)
        self.assertEqual(between.fallback.label, "Bound by")  # never loses the fact

    def test_the_meaning_check_can_say_close_or_far(self) -> None:
        self.assertEqual(
            decide_term(ONTO, ask("bound_by"), lambda *_: ("close", "sworn_to")),
            Reused("sworn_to", REASON_CLOSE_MEANING),
        )
        self.assertEqual(
            decide_term(ONTO, ask("bound_by"), lambda *_: ("far", None)),
            Accepted("bound_by", meaning_checked=True),
        )

    def test_the_same_proposal_always_gets_the_same_answer(self) -> None:
        for key in ("born_in", "check", "owns", "locatedin", "bound_by"):
            answers = {repr(decide_term(Ontology.build(), ask(key))) for _ in range(5)}
            self.assertEqual(len(answers), 1, key)
        # And a campaign's own extension changes nothing for another campaign's ontology.
        self.assertEqual(
            decide_term(ONTO, ask("born_in")), decide_term(Ontology.build(), ask("born_in"))
        )


class Health(unittest.TestCase):
    def test_the_counts_say_how_many_and_why_and_never_a_term(self) -> None:
        tally = Tally()
        for key in ("check", "save", "owns", "born_in"):
            tally.record(decide_term(ONTO, ask(key)))
        self.assertEqual((tally.reused, tally.accepted, tally.unchecked), (1, 1, 1))
        self.assertEqual(dict(tally.refused), {REASON_RULES_WORD: 2})
        line = tally.line()
        self.assertIn("2 rules word", line)
        self.assertNotIn("born_in", line)
        with self.assertLogs("dmbot.memory.ontology", level=logging.INFO) as logged:
            tally.write_to_log()
        self.assertIn("reused 1", logged.output[0])
