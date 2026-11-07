"""A known word whose two ends don't fit keeps its own words on the catch-all instead of being
dropped (#3033). Pure; invented words."""

from __future__ import annotations

import unittest

from dmbot.memory.claims import place_relationship
from dmbot.memory.ontology import FALLBACK_PREDICATE, Ontology

ONTO = Ontology.build()


def placed(text: str, subject: str, obj: str) -> tuple[str, bool, str]:
    term, from_object, label = place_relationship(ONTO, text, subject, obj)
    return term.key, from_object, label


class WhenTheEndsDoNotFit(unittest.TestCase):
    def test_a_known_word_between_the_wrong_kinds_keeps_its_own_words(self) -> None:
        self.assertEqual(
            placed("performed", "character", "character"), (FALLBACK_PREDICATE, False, "performed")
        )

    def test_the_same_word_between_the_right_kinds_is_still_the_kind(self) -> None:
        self.assertEqual(placed("performed", "faction", "deed"), ("did", False, ""))

    def test_said_from_the_other_end_is_judged_with_the_ends_turned(self) -> None:
        # ("was done by" reads from the deed's side: the ends are swapped before the check)
        self.assertEqual(placed("was done by", "deed", "faction"), ("did", True, ""))

    def test_a_companion_link_is_left_for_the_stores_own_rule(self) -> None:
        # (a place as the object is refused by `judge` as before, not turned into the catch-all)
        key, _from_object, _label = placed("travels with", "character", "place")
        self.assertEqual(key, "travels_with")
