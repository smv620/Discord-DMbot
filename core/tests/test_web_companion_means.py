"""What "travels with" means on the website's waiting connections and claims (#5165, the website
half of #5164): the shared words from `memory/link_words`, sent only for a companion link. No
database: the helper is pure. Invented names only."""

from __future__ import annotations

import unittest

from dmbot.memory.link_words import TRAVELS_WITH_MEANS
from dmbot.web.relationships import companion_means


class TheCompanionMeans(unittest.TestCase):
    def test_a_travels_with_connection_carries_the_shared_words(self) -> None:
        self.assertEqual(companion_means(predicate="travels_with"), {"means": TRAVELS_WITH_MEANS})

    def test_a_claim_in_the_sessions_words_carries_them_either_way_round(self) -> None:
        self.assertEqual(
            companion_means(relationship="travels with"), {"means": TRAVELS_WITH_MEANS}
        )
        self.assertEqual(
            companion_means(relationship="is joined by"), {"means": TRAVELS_WITH_MEANS}
        )

    def test_other_connections_carry_nothing(self) -> None:
        self.assertEqual(companion_means(predicate="knows"), {})
        self.assertEqual(companion_means(relationship="knows"), {})
        self.assertEqual(companion_means(), {})


class TheRefusalCodes(unittest.TestCase):
    """A keep the companion rule refused is answered with a code, and the answer read sends the
    store's own sentence for it (#5165 part 3)."""

    def test_each_rule_has_a_code_and_its_sentence(self) -> None:
        from dmbot.memory.store import (
            COMPANION_NOT_NPC,
            COMPANION_PARTY_SOURCE,
            COMPANION_TARGET,
            companion_code,
        )

        self.assertEqual(companion_code(COMPANION_NOT_NPC), "not_npc")
        self.assertEqual(companion_code(COMPANION_PARTY_SOURCE), "party_source")
        self.assertEqual(companion_code(COMPANION_TARGET), "target")
        self.assertIsNone(companion_code("Something else."))

    def test_the_answer_says_companion_and_the_rules_sentence(self) -> None:
        from dmbot.memory.store import COMPANION_PARTY_SOURCE
        from dmbot.memory.web_actions import COMPANION_ERROR
        from dmbot.web.relationships import companion_refusal_words

        self.assertEqual(
            companion_refusal_words(COMPANION_ERROR + "party_source"),
            ("companion", COMPANION_PARTY_SOURCE),
        )

    def test_any_other_error_is_left_alone(self) -> None:
        from dmbot.web.relationships import companion_refusal_words

        for error in ("", "bad_target", "companion_made_up", "companion_"):
            self.assertEqual(companion_refusal_words(error), ("", ""))
