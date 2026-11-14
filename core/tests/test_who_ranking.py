"""The candidates for a name that waits on "Who is this?" (#3602, slice 1). Pure; invented names."""

from __future__ import annotations

import unittest

from dmbot.memory import who_ranking as wr
from dmbot.memory.who_ranking import Ask, Candidate


def cand(
    i: str,
    *names: str,
    kind: str = "character",
    ties: tuple[str, ...] = (),
    hidden: bool = False,
) -> Candidate:
    return Candidate(i, kind, names, frozenset(ties), hidden)


def ids(got: list[wr.Ranked]) -> list[str]:
    return [r.candidate.id for r in got]


class Words(unittest.TestCase):
    def test_articles_owner_words_case_and_accents_are_left_out(self) -> None:
        self.assertEqual(wr.words_of("The Brewner's daughter"), {"brewner", "daughter"})
        self.assertEqual(wr.words_of("Zoë Ka'zeth"), {"zoe", "kazeth"})
        self.assertEqual(wr.words_of("James' hall"), {"james", "hall"})
        self.assertEqual(wr.words_of("Brewnerʼs son"), {"brewner", "son"})
        self.assertEqual(wr.words_of("..."), frozenset())
        self.assertEqual(wr.words_of("'s"), frozenset())

    def test_a_long_word_that_starts_another_is_a_weak_match_and_a_short_stem_none(self) -> None:
        self.assertEqual(wr._match("brewner", "brewner"), 1.0)
        self.assertEqual(wr._match("brew", "brewner"), wr.PREFIX_WORD)
        self.assertEqual(wr._match("bre", "brewner"), 0.0)  # (too short a stem)
        self.assertEqual(wr._match("brewner", "brew"), 0.0)  # (only the name's word may be shorter)
        self.assertEqual(wr._match("mara", "marek"), 0.0)


class Ranking(unittest.TestCase):
    def test_a_shared_word_outranks_a_mention(self) -> None:
        ask = Ask("the barkeep Vale", "character", session=frozenset({"b"}))
        got = wr.rank(ask, [cand("a", "Mara Vale"), cand("b", "Tavin")])
        self.assertEqual(ids(got), ["a", "b"])
        self.assertEqual(got[0].reasons[0], wr.WHY_WORD)

    def test_more_of_the_words_is_better_than_one(self) -> None:
        ask = Ask("Old Tom", "character")
        got = wr.rank(ask, [cand("a", "Old Mara"), cand("b", "Old Tom")])
        self.assertEqual(ids(got), ["b", "a"])

    def test_a_word_in_many_names_counts_a_third(self) -> None:
        ask = Ask("captain Vale", "character")
        pool = [cand(f"c{i}", f"Captain Name{i}") for i in range(4)] + [cand("v", "Vale")]
        got = wr.rank(ask, pool)
        self.assertEqual(got[0].candidate.id, "v")  # (the rare word beats the common one)

    def test_a_prefix_is_weaker_than_the_whole_word(self) -> None:
        ask = Ask("Brew", "character")
        got = wr.rank(ask, [cand("p", "Brewner"), cand("w", "Brew")])
        self.assertEqual(ids(got), ["w", "p"])

    def test_the_scene_and_the_owner_count_and_the_owner_is_never_the_answer(self) -> None:
        ask = Ask(
            "brewner's daughter", "character", scene=frozenset({"s"}), owner=frozenset({"brewner"})
        )
        daughter = cand("d", "Ilsa", ties=("brewner",))
        owner = cand("brewner", "Brewner")
        stranger = cand("s", "Orrin")
        got = wr.rank(ask, [owner, stranger, daughter])
        self.assertEqual(ids(got), ["d", "s"])  # (Brewner shares the word but is who it belongs to)
        self.assertIn(wr.WHY_BELONGS, got[0].reasons)
        self.assertEqual(got[1].reasons, (wr.WHY_SCENE, wr.WHY_KIND))

    def test_connections_to_the_scene_count_up_to_three_and_not_the_session(self) -> None:
        ask = Ask("old friend", "character", scene=frozenset("12345"), session=frozenset("67"))
        many = cand("m", "Corvin", ties=tuple("12345"))
        one = cand("o", "Pell", ties=("1",))
        session_only = cand("s", "Rook", ties=("6", "7"))
        got = wr.rank(ask, [one, many, session_only])
        self.assertEqual(ids(got), ["m", "o"])  # (a tie into the session alone is no sign)
        self.assertAlmostEqual(got[0].score - got[1].score, 2 * wr.ONE_CONNECTION)

    def test_a_session_mention_counts_alone_and_with_the_scene(self) -> None:
        ask = Ask("the guard", "character", scene=frozenset({"a"}), session=frozenset({"a", "b"}))
        got = wr.rank(ask, [cand("a", "Mara"), cand("b", "Tavin")])
        self.assertEqual(ids(got), ["a", "b"])
        self.assertEqual(got[1].reasons, (wr.WHY_SESSION, wr.WHY_KIND))


class Kinds(unittest.TestCase):
    def test_a_kind_that_cannot_be_it_is_left_out_and_unsure_is_open(self) -> None:
        place = cand("p", "Vale Keep", kind="place")
        person = cand("c", "Vale")
        self.assertEqual(ids(wr.rank(Ask("Vale", "character"), [place, person])), ["c"])
        self.assertEqual(set(ids(wr.rank(Ask("Vale", "concept"), [place, person]))), {"p", "c"})
        self.assertEqual(set(ids(wr.rank(Ask("Vale", ""), [place, person]))), {"p", "c"})

    def test_the_older_kinds_and_a_player_or_dm_are_characters_and_people_are_a_group(self) -> None:
        for ask_kind in ("character", "npc", "player_character"):
            for kind in ("character", "npc", "player_character", "deity", "creature", "person"):
                with self.subTest((ask_kind, kind)):
                    self.assertEqual(
                        ids(wr.rank(Ask("Vale", ask_kind), [cand("x", "Vale", kind=kind)])), ["x"]
                    )
        self.assertEqual(
            ids(wr.rank(Ask("Vale", "faction"), [cand("g", "Vale", kind="people")])), ["g"]
        )
        self.assertEqual(wr.rank(Ask("Vale", "character"), [cand("g", "Vale", kind="people")]), [])
        self.assertEqual(wr.rank(Ask("Vale", "item"), [cand("c", "Vale")]), [])

    def test_a_candidate_with_no_sign_is_never_offered_on_its_kind_alone(self) -> None:
        self.assertEqual(wr.rank(Ask("the stranger", "character"), [cand("x", "Orrin")]), [])


class Order(unittest.TestCase):
    def test_only_the_best_five_and_a_steady_order(self) -> None:
        ask = Ask("Vale", "character")
        pool = [cand(f"c{i}", f"Vale {'x' * i}") for i in range(8)]
        got = wr.rank(ask, pool)
        self.assertEqual(ids(got), ["c0", "c1", "c2", "c3", "c4"])  # (shorter name first)
        self.assertEqual(got, wr.rank(ask, list(reversed(pool))))
        self.assertEqual(len(wr.rank(ask, pool, limit=2)), 2)

    def test_a_repeated_id_counts_once_and_a_candidate_with_no_names_is_fine(self) -> None:
        ask = Ask("Vale", "character", scene=frozenset({"n"}))
        got = wr.rank(ask, [cand("a", "Vale"), cand("a", "Vale Again"), cand("n")])
        self.assertEqual(ids(got), ["a", "n"])

    def test_an_empty_name_matches_by_the_scene_only(self) -> None:
        got = wr.rank(Ask("", "", scene=frozenset({"a"})), [cand("a", "Mara"), cand("b", "Tavin")])
        self.assertEqual(ids(got), ["a"])


class Hidden(unittest.TestCase):
    def test_a_hidden_candidate_is_left_out_unless_the_dm_asks_for_it(self) -> None:
        pool = [cand("h", "Vale the Grey", hidden=True), cand("v", "Vale")]
        self.assertEqual(ids(wr.rank(Ask("Vale", "character"), pool)), ["v"])
        got = wr.rank(Ask("Vale", "character"), pool, include_hidden=True)
        self.assertEqual(set(ids(got)), {"h", "v"})
        self.assertTrue(next(r for r in got if r.candidate.id == "h").candidate.hidden)
