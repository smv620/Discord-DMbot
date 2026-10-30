"""Offers to join an unnamed entry with a named one (#3037, slice 1): ranked by evidence, never a
guess, never twice after a no. Invented names."""

from __future__ import annotations

import unittest

from dmbot.memory.same_person import (
    CONNECTIONS,
    MAX_OFFERS_PER_ENTRY,
    STATED,
    WORDS,
    Named,
    Unnamed,
    meaningful,
    offers,
)

CAPTAIN = "captain"
GATE = "gate"
GUARD = Unnamed("u1", "character", "guard at the gate")
KEN = Named("n1", "character", "Ken the guard, who keeps the north gate")
OTHER = Named("n2", "character", "Mira the baker")


class TheEvidence(unittest.TestCase):
    def test_a_text_that_says_so_gives_one_offer_with_its_reason(self) -> None:
        (offer,) = offers([GUARD], [OTHER, KEN], stated={("u1", "n2")})
        self.assertEqual((offer.unnamed_id, offer.named_id, offer.reasons), ("u1", "n2", (STATED,)))

    def test_shared_words_are_a_reason_and_filler_is_not(self) -> None:
        self.assertEqual(meaningful("the guard at the gate (name unknown)"), {"guard", "gate"})
        (offer,) = offers([GUARD], [OTHER, KEN])
        self.assertEqual((offer.named_id, offer.reasons), ("n1", (WORDS,)))

    def test_shared_connections_are_a_reason(self) -> None:
        links = frozenset({("works_for", CAPTAIN, True)})
        u = Unnamed("u1", "character", "watchman", links)
        n = Named("n1", "character", "Ken", links)
        (offer,) = offers([u], [n, OTHER])
        self.assertEqual(offer.reasons, (CONNECTIONS,))

    def test_a_connection_the_other_way_round_is_not_shared(self) -> None:
        u = Unnamed("u1", "character", "watchman", frozenset({("works_for", CAPTAIN, True)}))
        n = Named("n1", "character", "Ken", frozenset({("works_for", CAPTAIN, False)}))
        self.assertEqual(offers([u], [n]), [])

    def test_no_evidence_is_no_offer(self) -> None:
        self.assertEqual(offers([GUARD], [OTHER]), [])

    def test_a_different_kind_is_never_offered(self) -> None:
        place = Named("p1", "place", "the guard tower at the gate")
        self.assertEqual(offers([GUARD], [place]), [])


class TheOrder(unittest.TestCase):
    def test_a_text_that_says_who_leaves_out_the_weaker_guesses_for_that_entry(self) -> None:
        got = offers([GUARD], [KEN, OTHER], stated={("u1", "n2")})
        self.assertEqual(
            [o.named_id for o in got], ["n2"]
        )  # (Ken shares words, but the text spoke)

    def test_the_best_evidence_comes_first(self) -> None:
        links = frozenset({("works_for", CAPTAIN, True)})
        u = Unnamed("u1", "character", "guard at the gate", links)
        by_words = Named("n1", "character", "Ken the guard", frozenset())
        by_links = Named("n2", "character", "Bram", links)
        got = offers([u], [by_words, by_links])
        self.assertEqual([o.named_id for o in got], ["n2", "n1"])  # (links beat words)
        self.assertEqual([o.score for o in got], [10, 5])

    def test_two_unnamed_entries_and_one_name_make_two_offers_in_order(self) -> None:
        second = Unnamed("u2", "character", "guard on the wall")
        got = offers([second, GUARD], [KEN])
        self.assertEqual({(o.unnamed_id, o.named_id) for o in got}, {("u1", "n1"), ("u2", "n1")})
        self.assertGreater(got[0].score, got[1].score)  # (gate and guard beats guard alone)
        self.assertEqual(got[0].unnamed_id, "u1")

    def test_the_same_input_gives_the_same_order(self) -> None:
        names = [Named(f"n{i}", "character", "the guard at the gate") for i in range(6)]
        first = offers([GUARD], names)
        self.assertEqual(first, offers([GUARD], list(reversed(names))))
        self.assertEqual(len(first), MAX_OFFERS_PER_ENTRY)  # (a long list is a guess)

    def test_a_crowded_entry_does_not_win_on_connections_alone(self) -> None:
        many = frozenset({(f"r{i}", f"x{i}", True) for i in range(30)})
        u = Unnamed("u1", "character", "watchman", many)
        n = Named("n1", "character", "Ken", many)
        (offer,) = offers([u], [n])
        self.assertLessEqual(offer.score, 40)


class TheNo(unittest.TestCase):
    def test_a_declined_pair_is_never_offered_again(self) -> None:
        got = offers([GUARD], [KEN], declined={("u1", "n1")}, stated={("u1", "n1")})
        self.assertEqual(got, [])

    def test_a_no_for_one_pair_leaves_the_others(self) -> None:
        other = Named("n3", "character", "Bram, guard of the gate")
        got = offers([GUARD], [KEN, other], declined={("u1", "n1")})
        self.assertEqual([o.named_id for o in got], ["n3"])
