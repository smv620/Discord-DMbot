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

    def test_a_half_sibling_gets_the_parent_of_the_one_parent_they_share(self) -> None:
        # Aldric and Bryn share one stated parent (Mira), so Bryn is Mira's child, by the rule.
        # This is the documented case where the rule can be wrong for half-siblings: a derived
        # link is only ever worked out from a stated parent and a stated sibling, never from a
        # shared parent.
        links = [
            stated("1", "mira", "parent", "aldric"),
            stated("2", "aldric", "sibling", "bryn"),
            stated("3", "tovin", "parent", "bryn"),
        ]

        derived = derive(links)

        self.assertIn(Derived("mira", "parent", "bryn", ("1", "2")), derived)
        self.assertIn(Derived("tovin", "parent", "aldric", ("3", "2")), derived)

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
        # Gorran is Mira's parent, and Mira is Aldric's parent. Gorran's grandchild is Aldric, which
        # is the one grandparent link. Aldric's sibling Bryn is not worked out as Gorran's
        # grandchild: that would need a derived link as a source.
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

        derived = derive(links)

        keys = [(d.a_id, d.role, d.b_id) for d in derived]
        self.assertEqual(keys, sorted(keys))

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
