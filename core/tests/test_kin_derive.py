"""What follows from stated family links (#2959 item 2): the parent-of-a-sibling and grandparent
rules, the half-sibling case, the links never worked out (already stated, a self-link, a cycle),
the words that are not sources, and that the answer does not depend on the order of the input.
Invented names only."""

from __future__ import annotations

import random
import unittest

from dmbot.memory.kin_derive import Derived, Stated, derive


def stated(link_id: str, a_id: str, role: str, b_id: str) -> Stated:
    return Stated(link_id=link_id, a_id=a_id, role=role, b_id=b_id)


class TheParentOfASibling(unittest.TestCase):
    def test_a_parent_and_a_sibling_give_a_parent(self) -> None:
        # Mira is the parent of Aldric, Aldric is the sibling of Bryn: Mira is Bryn's parent.
        links = [stated("1", "mira", "parent", "aldric"), stated("2", "aldric", "sibling", "bryn")]

        self.assertEqual(
            derive(links),
            [Derived("mira", "parent", "bryn", ("1", "2"))],
        )

    def test_a_sibling_link_reads_both_ways(self) -> None:
        # The sibling is stated the other way round, and the parent still reaches it.
        links = [stated("1", "mira", "parent", "aldric"), stated("2", "bryn", "sibling", "aldric")]

        self.assertEqual(
            derive(links),
            [Derived("mira", "parent", "bryn", ("1", "2"))],
        )

    def test_the_owners_example_gives_the_sister_as_a_child_word_not_a_gender(self) -> None:
        # A mother has a son, and the son has a sister. The sister is the mother's child; the
        # gendered words are read as the neutral ones, so the derived role is "parent" only.
        links = [stated("1", "mira", "mother", "aldric"), stated("2", "aldric", "sister", "bryn")]

        self.assertEqual(
            derive(links),
            [Derived("mira", "parent", "bryn", ("1", "2"))],
        )

    def test_a_child_link_is_a_parent_link_read_from_the_other_end(self) -> None:
        # "Aldric is the child of Mira" is the same fact as "Mira is the parent of Aldric".
        links = [stated("1", "aldric", "child", "mira"), stated("2", "aldric", "sibling", "bryn")]

        self.assertEqual(
            derive(links),
            [Derived("mira", "parent", "bryn", ("1", "2"))],
        )

    def test_a_half_sibling_is_the_child_of_the_parent_the_sibling_link_reaches(self) -> None:
        # Aldric's parent is Mira and Aldric is the sibling of Bryn, so by the rule Mira is Bryn's
        # parent. Bryn's own parent Tovin then reaches Aldric the same way. The rule can be wrong
        # for half-siblings (here Bryn may share only Tovin), and that is documented in the module:
        # a derived link comes only from a stated parent and a stated sibling.
        links = [
            stated("1", "mira", "parent", "aldric"),
            stated("2", "aldric", "sibling", "bryn"),
            stated("3", "tovin", "parent", "bryn"),
        ]

        self.assertEqual(
            derive(links),
            [
                Derived("mira", "parent", "bryn", ("1", "2")),
                Derived("tovin", "parent", "aldric", ("3", "2")),
            ],
        )

    def test_a_shared_parent_alone_gives_no_sibling_link(self) -> None:
        # Two children of one parent are not worked out as siblings: siblings are only stated.
        links = [stated("1", "mira", "parent", "aldric"), stated("2", "mira", "parent", "bryn")]

        self.assertEqual(derive(links), [])


class TheGrandparent(unittest.TestCase):
    def test_a_parent_of_a_parent_is_a_grandparent(self) -> None:
        links = [stated("1", "gorran", "parent", "mira"), stated("2", "mira", "parent", "aldric")]

        self.assertEqual(
            derive(links),
            [Derived("gorran", "grandparent", "aldric", ("1", "2"))],
        )

    def test_a_grandparent_already_stated_is_not_worked_out_again(self) -> None:
        links = [
            stated("1", "gorran", "parent", "mira"),
            stated("2", "mira", "parent", "aldric"),
            stated("3", "gorran", "grandparent", "aldric"),
        ]

        self.assertEqual(derive(links), [])

    def test_a_grandchild_stated_the_other_way_is_already_known(self) -> None:
        links = [
            stated("1", "gorran", "parent", "mira"),
            stated("2", "mira", "parent", "aldric"),
            stated("3", "aldric", "grandchild", "gorran"),
        ]

        self.assertEqual(derive(links), [])


class TheLinksNeverWorkedOut(unittest.TestCase):
    def test_an_already_stated_parent_link_is_not_repeated(self) -> None:
        links = [
            stated("1", "mira", "parent", "aldric"),
            stated("2", "aldric", "sibling", "bryn"),
            stated("3", "mira", "parent", "bryn"),
        ]

        self.assertEqual(derive(links), [])

    def test_a_parent_link_stated_as_a_child_link_is_not_repeated(self) -> None:
        links = [
            stated("1", "mira", "parent", "aldric"),
            stated("2", "aldric", "sibling", "bryn"),
            stated("3", "bryn", "child", "mira"),
        ]

        self.assertEqual(derive(links), [])

    def test_a_self_link_is_never_derived(self) -> None:
        # Mira is the parent of Aldric and Aldric is the sibling of Mira: Mira would be her own
        # parent, so nothing is worked out.
        links = [stated("1", "mira", "parent", "aldric"), stated("2", "aldric", "sibling", "mira")]

        self.assertEqual(derive(links), [])

    def test_a_cycle_gives_no_self_link_and_no_loop(self) -> None:
        links = [stated("1", "mira", "parent", "aldric"), stated("2", "aldric", "parent", "mira")]

        self.assertEqual(derive(links), [])

    def test_a_link_stated_twice_is_worked_out_once(self) -> None:
        links = [
            stated("1", "mira", "parent", "aldric"),
            stated("2", "aldric", "sibling", "bryn"),
            stated("3", "aldric", "sibling", "bryn"),
        ]

        derived = derive(links)

        self.assertEqual(derived, [Derived("mira", "parent", "bryn", ("1", "2"))])

    def test_a_derived_link_is_never_a_source_for_another(self) -> None:
        # Mira is Bryn's parent only by being derived from "Mira parent Aldric" and "Aldric sibling
        # Bryn". Gorran is Bryn's grandparent only if that derived link were used as a source, so
        # no such link must appear. Gorran is Aldric's grandparent from two stated links, and that
        # is the only grandparent link.
        links = [
            stated("1", "gorran", "parent", "mira"),
            stated("2", "mira", "parent", "aldric"),
            stated("3", "aldric", "sibling", "bryn"),
        ]

        self.assertEqual(
            derive(links),
            [
                Derived("gorran", "grandparent", "aldric", ("1", "2")),
                Derived("mira", "parent", "bryn", ("2", "3")),
            ],
        )


class TheWordsThatAreNotSources(unittest.TestCase):
    def test_spouse_aunt_uncle_niece_nephew_and_cousin_are_ignored(self) -> None:
        for word in ("spouse", "aunt", "uncle", "niece", "nephew", "cousin"):
            with self.subTest(word):
                links = [
                    stated("1", "mira", word, "aldric"),
                    stated("2", "aldric", "sibling", "bryn"),
                ]

                self.assertEqual(derive(links), [])

    def test_a_grandparent_stated_is_not_used_as_a_source(self) -> None:
        links = [
            stated("1", "gorran", "grandparent", "mira"),
            stated("2", "mira", "parent", "aldric"),
        ]

        self.assertEqual(derive(links), [])


class TheDerivedOrder(unittest.TestCase):
    def test_the_result_does_not_depend_on_the_order_of_the_input(self) -> None:
        links = [
            stated("1", "gorran", "parent", "mira"),
            stated("2", "mira", "parent", "aldric"),
            stated("3", "aldric", "sibling", "bryn"),
            stated("4", "gorran", "parent", "tovin"),
            stated("5", "tovin", "sibling", "mira"),
        ]
        expected = derive(links)

        for seed in range(20):
            shuffled = list(links)
            random.Random(seed).shuffle(shuffled)
            with self.subTest(seed):
                self.assertEqual(derive(shuffled), expected)

    def test_the_result_is_sorted_by_names_and_role(self) -> None:
        links = [
            stated("1", "mira", "parent", "aldric"),
            stated("2", "aldric", "sibling", "tovin"),
            stated("3", "mira", "parent", "bryn"),
            stated("4", "bryn", "sibling", "cora"),
        ]

        self.assertEqual(
            derive(links),
            [
                Derived("mira", "parent", "cora", ("3", "4")),
                Derived("mira", "parent", "tovin", ("1", "2")),
            ],
        )

    def test_when_two_pairs_of_sources_give_one_link_the_first_pair_is_kept(self) -> None:
        links = [
            stated("1", "mira", "parent", "aldric"),
            stated("2", "aldric", "sibling", "bryn"),
            stated("3", "mira", "parent", "cora"),
            stated("4", "cora", "sibling", "bryn"),
        ]

        self.assertEqual(
            derive(links),
            [Derived("mira", "parent", "bryn", ("1", "2"))],
        )


class TheEdgeCases(unittest.TestCase):
    def test_a_stated_self_link_is_not_used_as_a_source(self) -> None:
        # "Mira is her own parent" is not a family fact, so nothing follows from it.
        links = [stated("1", "mira", "parent", "mira"), stated("2", "mira", "sibling", "aldric")]

        self.assertEqual(derive(links), [])

    def test_a_stated_self_link_does_not_make_a_grandparent(self) -> None:
        links = [
            stated("1", "gorran", "parent", "gorran"),
            stated("2", "gorran", "parent", "aldric"),
        ]

        self.assertEqual(derive(links), [])

    def test_each_gendered_word_is_read_as_its_neutral_word(self) -> None:
        # Mira is the parent (or the child, read the other way) of Aldric, and Aldric has a sibling
        # Bryn. The derived role is always "parent", whichever gendered word the source used.
        parent_links = {
            "mother": stated("1", "mira", "mother", "aldric"),
            "father": stated("1", "mira", "father", "aldric"),
            "daughter": stated("1", "aldric", "daughter", "mira"),
            "son": stated("1", "aldric", "son", "mira"),
        }
        sibling_links = {
            "sibling": stated("2", "aldric", "sibling", "bryn"),
            "sister": stated("2", "aldric", "sister", "bryn"),
            "brother": stated("2", "aldric", "brother", "bryn"),
        }
        for parent_word, parent_link in parent_links.items():
            for sibling_word, sibling_link in sibling_links.items():
                with self.subTest(parent=parent_word, sibling=sibling_word):
                    self.assertEqual(
                        derive([parent_link, sibling_link]),
                        [Derived("mira", "parent", "bryn", ("1", "2"))],
                    )

    def test_a_son_is_read_as_a_child_of_the_parent(self) -> None:
        links = [stated("1", "aldric", "son", "mira"), stated("2", "aldric", "sibling", "bryn")]

        self.assertEqual(derive(links), [Derived("mira", "parent", "bryn", ("1", "2"))])

    def test_a_grandparent_built_from_child_words(self) -> None:
        links = [stated("1", "aldric", "child", "mira"), stated("2", "mira", "child", "gorran")]

        self.assertEqual(derive(links), [Derived("gorran", "grandparent", "aldric", ("2", "1"))])

    def test_a_contradiction_drops_the_derived_link_rather_than_guessing(self) -> None:
        # "Mira is the parent of Aldric", "Aldric and Bryn are siblings" and "Bryn is the parent of
        # Mira" contradict "Mira is the parent of Bryn", so that derived link is dropped.
        links = [
            stated("1", "mira", "parent", "aldric"),
            stated("2", "aldric", "sibling", "bryn"),
            stated("3", "bryn", "parent", "mira"),
        ]

        self.assertNotIn(
            Derived("mira", "parent", "bryn", ("1", "2")),
            derive(links),
        )

    def test_the_first_pair_of_sources_is_compared_as_text(self) -> None:
        # Source ids compare as strings, so "10" sorts before "9". The output is still the same
        # every time, which is what matters.
        links = [
            stated("9", "mira", "parent", "aldric"),
            stated("3", "aldric", "sibling", "bryn"),
            stated("10", "mira", "parent", "cora"),
            stated("2", "cora", "sibling", "bryn"),
        ]

        self.assertEqual(
            derive(links),
            [Derived("mira", "parent", "bryn", ("10", "2"))],
        )
