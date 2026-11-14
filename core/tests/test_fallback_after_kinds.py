"""The catch-all comes last (#3033): a word an existing kind already covers is placed on that kind,
and the catch-all only gets what no kind fits. Pure; invented words only."""

from __future__ import annotations

import unittest

from dmbot.memory.claims import place_relationship, resolve_relationship
from dmbot.memory.ontology import FALLBACK_PREDICATE, Ontology

ONTO = Ontology.build()


def placed(text: str, subject: str, obj: str) -> tuple[str, bool, str]:
    term, from_object, label = place_relationship(ONTO, text, subject, obj)
    return term.key, from_object, label


class ExistingKindsFirst(unittest.TestCase):
    def test_the_words_of_a_deed_go_on_the_deed_kinds(self) -> None:
        self.assertEqual(placed("performed", "faction", "deed"), ("did", False, ""))
        self.assertEqual(placed("touched", "deed", "place"), ("affected", False, ""))

    def test_every_word_a_kind_names_is_placed_on_it_never_on_the_catch_all(self) -> None:
        for term in ONTO.predicates.values():
            if term.status != "active" or term.key == FALLBACK_PREDICATE:
                continue
            for word in (term.label, *term.synonyms):  # (the inverse words are a different path)
                with self.subTest(term=term.key, word=word):
                    found = resolve_relationship(ONTO, word)
                    self.assertIsNotNone(found)
                    assert found is not None
                    self.assertNotEqual(found[0].key, FALLBACK_PREDICATE)

    def test_words_no_kind_covers_keep_their_own_words_on_the_catch_all(self) -> None:
        key, _from_object, label = placed("swore on the ashes of", "character", "character")
        self.assertEqual((key, label), (FALLBACK_PREDICATE, "swore on the ashes of"))
