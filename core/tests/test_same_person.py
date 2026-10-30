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

    def test_filler_words_are_not_words(self) -> None:
        self.assertEqual(meaningful("she was there, and had not been at the inn"), {"inn"})

    def test_plurals_are_folded_so_gate_guard_matches_guards_the_gate(self) -> None:
        self.assertEqual(meaningful("guards the gates"), meaningful("gate guard"))
        u = Unnamed("u1", "character", "gate guard")
        (offer,) = offers([u], [Named("n1", "character", "Ken, who guards the gate")])
        self.assertEqual(offer.reasons, (WORDS,))

    def test_two_shared_connections_are_a_reason(self) -> None:
        links = frozenset({("works_for", CAPTAIN, True), ("lives_in", "keep", True)})
        u = Unnamed("u1", "character", "watchman", links)
        n = Named("n1", "character", "Ken", links)
        (offer,) = offers([u], [n, OTHER])
        self.assertEqual(offer.reasons, (CONNECTIONS,))

    def test_one_shared_word_alone_is_not_an_offer(self) -> None:
        u = Unnamed("u1", "character", "the woman at the inn")
        self.assertEqual(offers([u], [Named("n1", "character", "Mira, who owns the inn")]), [])

    def test_one_shared_connection_alone_is_not_an_offer(self) -> None:
        links = frozenset({("works_for", CAPTAIN, True)})
        u = Unnamed("u1", "character", "watchman", links)
        self.assertEqual(offers([u], [Named("n1", "character", "Ken", links)]), [])

    def test_one_word_and_one_connection_together_are_enough(self) -> None:
        links = frozenset({("works_for", CAPTAIN, True)})
        u = Unnamed("u1", "character", "guard", links)
        (offer,) = offers([u], [Named("n1", "character", "Ken the guard", links)])
        self.assertEqual(offer.reasons, (CONNECTIONS, WORDS))  # (strongest first)

    def test_filler_alone_gives_no_offer(self) -> None:
        u = Unnamed("u1", "character", "the woman who was at the inn")
        names = [Named(f"n{i}", "character", f"Person {i}, who was kind") for i in range(4)]
        self.assertEqual(offers([u], names), [])

    def test_a_connection_many_share_is_not_evidence(self) -> None:
        town = frozenset({("lives_in", "town", True), ("works_for", "mayor", True)})
        u = Unnamed("u1", "character", "watchman", town)
        names = [Named(f"n{i}", "character", f"Person {i}", town) for i in range(10)]
        self.assertEqual(offers([u], names), [])

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
        links = frozenset({("works_for", CAPTAIN, True), ("lives_in", "keep", True)})
        u = Unnamed("u1", "character", "guard at the gate", links)
        by_words = Named("n1", "character", "Ken the guard at the gate", frozenset())
        by_links = Named("n2", "character", "Bram the guard", links)
        got = offers([u], [by_words, by_links])
        self.assertEqual([o.named_id for o in got], ["n2", "n1"])  # (links beat words)
        self.assertEqual([o.score for o in got], [25, 10])

    def test_two_unnamed_entries_and_one_name_make_two_offers_in_order(self) -> None:
        second = Unnamed("u2", "character", "guard at the north wall")
        first = Unnamed("u1", "character", "guard at the north gate")
        got = offers([second, first], [KEN])
        self.assertEqual({(o.unnamed_id, o.named_id) for o in got}, {("u1", "n1"), ("u2", "n1")})
        self.assertGreater(got[0].score, got[1].score)  # (three shared words beat two)
        self.assertEqual(got[0].unnamed_id, "u1")

    def test_the_same_input_gives_the_same_order(self) -> None:
        names = [Named(f"n{i}", "character", "the guard at the gate") for i in range(3)]
        first = offers([GUARD], names)
        self.assertEqual(first, offers([GUARD], list(reversed(names))))
        self.assertEqual(len(first), MAX_OFFERS_PER_ENTRY)

    def test_a_tie_past_the_limit_is_cant_tell_not_a_pick_by_id(self) -> None:
        names = [Named(f"n{i}", "character", "the guard at the gate") for i in range(6)]
        self.assertEqual(offers([GUARD], names), [])

    def test_a_stronger_match_survives_a_tie_below_it(self) -> None:
        names = [Named(f"n{i}", "character", "the guard at the gate") for i in range(5)]
        best = Named("n9", "character", "the guard at the north gate, old guard")
        strong = Unnamed("u1", "character", "guard at the north gate")
        got = offers([strong], [*names, best])
        self.assertEqual([o.named_id for o in got], ["n9"])

    def test_a_crowded_entry_does_not_win_on_connections_alone(self) -> None:
        many = frozenset({(f"r{i}", f"x{i}", True) for i in range(30)})
        u = Unnamed("u1", "character", "watchman", many)
        n = Named("n1", "character", "Ken", many)
        (offer,) = offers([u], [n])
        self.assertLessEqual(offer.score, 40)

    def test_the_reasons_are_strongest_first(self) -> None:
        links = frozenset({("works_for", CAPTAIN, True), ("lives_in", "keep", True)})
        u = Unnamed("u1", "character", "guard at the gate", links)
        n = Named("n1", "character", "Ken the guard at the gate", links)
        (offer,) = offers([u], [n])
        self.assertEqual(offer.reasons, (CONNECTIONS, WORDS))  # (20 points, then 10)


class TheNo(unittest.TestCase):
    def test_a_declined_pair_is_never_offered_again(self) -> None:
        got = offers([GUARD], [KEN], declined={("u1", "n1")}, stated={("u1", "n1")})
        self.assertEqual(got, [])

    def test_a_text_naming_someone_the_dm_declined_gives_no_offer(self) -> None:
        got = offers([GUARD], [KEN, OTHER], stated={("u1", "n2")}, declined={("u1", "n2")})
        self.assertEqual([o.named_id for o in got], ["n1"])  # (the text no longer settles it)

    def test_a_no_for_one_pair_leaves_the_others(self) -> None:
        other = Named("n3", "character", "Bram, guard of the gate")
        got = offers([GUARD], [KEN, other], declined={("u1", "n1")})
        self.assertEqual([o.named_id for o in got], ["n3"])
