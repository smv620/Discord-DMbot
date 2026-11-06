"""The candidates for a name that waits on "Who is this?" (#3602, slice 1). Pure; invented names."""

from __future__ import annotations

import unittest

from dmbot.memory import who_ranking as wr
from dmbot.memory.who_ranking import Ask, Candidate


def cand(
    i: str, *names: str, kind: str = "character", ties: tuple[str, ...] = (), hidden: bool = False
) -> Candidate:
    return Candidate(i, kind, names, frozenset(ties), hidden)


class Words(unittest.TestCase):
    def test_articles_owner_words_case_and_accents_are_left_out(self) -> None:
        self.assertEqual(wr.words_of("The Brewner's daughter"), {"brewner", "daughter"})
        self.assertEqual(wr.words_of("Zoë Ka'zeth"), {"zoe", "kazeth"})

    def test_a_long_word_that_starts_another_is_the_same_word(self) -> None:
        self.assertTrue(wr._same_word("brewner", "brew"))
        self.assertFalse(wr._same_word("bre", "brewner"))  # (too short a stem)
        self.assertFalse(wr._same_word("mara", "marek"))


class Ranking(unittest.TestCase):
    def test_a_shared_word_outranks_a_mention(self) -> None:
        ask = Ask("the barkeep Vale", "character", session=frozenset({"b"}))
        got = wr.rank(ask, [cand("a", "Mara Vale"), cand("b", "Tavin")])
        self.assertEqual([r.candidate.id for r in got], ["a", "b"])
        self.assertEqual(got[0].reasons[0], wr.WHY_WORD)

    def test_the_scene_and_the_owner_count(self) -> None:
        ask = Ask(
            "brewner's daughter", "character", scene=frozenset({"s"}), owner=frozenset({"brewner"})
        )
        daughter = cand("d", "Ilsa", ties=("brewner",))
        stranger = cand("s", "Orrin")
        got = wr.rank(ask, [stranger, daughter])
        self.assertEqual({r.candidate.id for r in got}, {"d", "s"})
        best = got[0]
        self.assertEqual(best.candidate.id, "d")
        self.assertIn(wr.WHY_BELONGS, best.reasons)
        self.assertEqual(got[1].reasons, (wr.WHY_SCENE, wr.WHY_KIND))

    def test_connections_to_the_scene_count_up_to_three(self) -> None:
        ask = Ask("old friend", "character", scene=frozenset("12345"))
        many = cand("m", "Corvin", ties=tuple("12345"))
        one = cand("o", "Pell", ties=("1",))
        got = wr.rank(ask, [one, many])
        self.assertEqual([r.candidate.id for r in got], ["m", "o"])
        self.assertAlmostEqual(got[0].score - got[1].score, 2 * wr.ONE_CONNECTION)

    def test_a_kind_that_cannot_be_it_is_left_out_and_unsure_is_open(self) -> None:
        place = cand("p", "Vale Keep", kind="place")
        person = cand("c", "Vale")
        self.assertEqual(
            [r.candidate.id for r in wr.rank(Ask("Vale", "character"), [place, person])], ["c"]
        )
        self.assertEqual(
            {r.candidate.id for r in wr.rank(Ask("Vale", "concept"), [place, person])}, {"p", "c"}
        )

    def test_a_candidate_with_no_sign_is_never_offered_on_its_kind_alone(self) -> None:
        self.assertEqual(wr.rank(Ask("the stranger", "character"), [cand("x", "Orrin")]), [])

    def test_only_the_best_five_and_a_steady_order(self) -> None:
        ask = Ask("Vale", "character")
        pool = [cand(f"c{i}", f"Vale {'x' * i}") for i in range(8)]
        got = wr.rank(ask, pool)
        self.assertEqual(len(got), wr.LIMIT)
        self.assertEqual(
            [r.candidate.id for r in got], ["c0", "c1", "c2", "c3", "c4"]
        )  # (shorter first)
        self.assertEqual(got, wr.rank(ask, list(reversed(pool))))  # (the same whatever the order)

    def test_a_hidden_candidate_is_ranked_and_marked(self) -> None:
        got = wr.rank(Ask("Vale", "character"), [cand("h", "Vale", hidden=True)])
        self.assertTrue(got[0].candidate.hidden)

    def test_an_empty_name_matches_by_the_scene_only(self) -> None:
        got = wr.rank(Ask("", "", scene=frozenset({"a"})), [cand("a", "Mara"), cand("b", "Tavin")])
        self.assertEqual([r.candidate.id for r in got], ["a"])
