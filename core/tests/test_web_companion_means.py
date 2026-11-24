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
