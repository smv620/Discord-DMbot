"""What follows from stated family links (#2959, item 2): the two rules, and nothing more."""

from __future__ import annotations

import ast
import random
import unittest
from pathlib import Path

from dmbot.memory import kin_derive as kd
from dmbot.memory.kin_derive import Derived, Stated, derive


def link(link_id: str, a: str, role: str, b: str) -> Stated:
    return Stated(link_id, a, role, b)


class TheRules(unittest.TestCase):
    def test_a_parent_of_a_sibling_is_a_parent(self) -> None:
        # X parent A, A sibling B: X parent B
        out = derive([link("1", "X", "parent", "A"), link("2", "A", "sibling", "B")])
        self.assertEqual(out, [Derived("X", "parent", "B", ("1", "2"))])

    def test_a_sibling_link_reads_both_ways(self) -> None:
        out = derive([link("1", "X", "parent", "A"), link("2", "B", "sibling", "A")])
        self.assertEqual(out, [Derived("X", "parent", "B", ("1", "2"))])

    def test_a_child_link_is_read_as_the_reverse_parent_link(self) -> None:
        # "A child-of X" is "X parent A"
        out = derive([link("1", "A", "child", "X"), link("2", "A", "sibling", "B")])
        self.assertEqual(out, [Derived("X", "parent", "B", ("1", "2"))])

    def test_a_grandparent_follows_from_two_parent_links(self) -> None:
        out = derive([link("1", "G", "parent", "P"), link("2", "P", "parent", "A")])
        self.assertEqual(out, [Derived("G", "grandparent", "A", ("1", "2"))])

    def test_a_grandparent_follows_from_a_child_link_too(self) -> None:
        out = derive([link("1", "P", "child", "G"), link("2", "A", "child", "P")])
        self.assertEqual(out, [Derived("G", "grandparent", "A", ("1", "2"))])

    def test_the_owners_example_a_mother_her_son_and_his_sister(self) -> None:
        # The mother (M) is the parent of her son (S); his sister (D) is his sibling, so M is the
        # parent of D too (the rule, as the owner gave it)
        out = derive(
            [link("1", "M", "parent", "S"), link("2", "S", "sibling", "D")],
        )
        self.assertEqual(out, [Derived("M", "parent", "D", ("1", "2"))])


class TheAlreadyStated(unittest.TestCase):
    def test_a_parent_link_stated_either_way_round_is_not_offered_again(self) -> None:
        out = derive(
            [
                link("1", "X", "parent", "A"),
                link("2", "A", "sibling", "B"),
                link("3", "B", "child", "X"),  # "X parent B", stated as a child link
            ],
        )
        self.assertEqual(out, [])

    def test_a_grandparent_stated_as_a_grandchild_is_not_offered_again(self) -> None:
        out = derive(
            [
                link("1", "G", "parent", "P"),
                link("2", "P", "parent", "A"),
                link("3", "A", "grandchild", "G"),
            ],
        )
        self.assertEqual(out, [])

    def test_a_self_link_is_never_offered(self) -> None:
        # X parent A, A sibling X: would give X parent X
        out = derive([link("1", "X", "parent", "A"), link("2", "A", "sibling", "X")])
        self.assertEqual(out, [])

    def test_a_cycle_gives_no_self_grandparent(self) -> None:
        out = derive([link("1", "A", "parent", "B"), link("2", "B", "parent", "A")])
        self.assertEqual(out, [])

    def test_the_same_fact_from_two_paths_is_offered_once(self) -> None:
        out = derive(
            [
                link("1", "X", "parent", "A"),
                link("2", "A", "sibling", "B"),
                link("3", "X", "parent", "C"),
                link("4", "C", "sibling", "B"),
            ],
        )
        self.assertEqual([(d.a_id, d.role, d.b_id) for d in out], [("X", "parent", "B")])


class TheSources(unittest.TestCase):
    def test_roles_outside_the_rules_are_ignored_as_sources(self) -> None:
        out = derive(
            [
                link("1", "M", "mother", "S"),  # a gendered word is not a source
                link("2", "S", "sibling", "D"),
                link("3", "G", "spouse", "P"),
                link("4", "P", "aunt", "A"),
            ],
        )
        self.assertEqual(out, [])

    def test_a_derived_link_is_never_a_source_for_another(self) -> None:
        # A three-generation chain: the grandparent is stated for the two pairs only, and the
        # great-grandparent (which would need the derived grandparent) is not derived
        out = derive(
            [
                link("1", "G", "parent", "P"),
                link("2", "P", "parent", "A"),
                link("3", "A", "parent", "K"),
            ],
        )
        self.assertEqual(
            sorted((d.a_id, d.role, d.b_id) for d in out),
            [("G", "grandparent", "A"), ("P", "grandparent", "K")],
        )
        self.assertNotIn(("G", "grandparent", "K"), [(d.a_id, d.role, d.b_id) for d in out])

    def test_the_output_is_sorted_and_the_sources_are_the_two_links(self) -> None:
        out = derive(
            [
                link("9", "P", "parent", "A"),
                link("8", "G", "parent", "P"),
                link("7", "Z", "parent", "Q"),
                link("6", "Q", "sibling", "W"),
            ],
        )
        self.assertEqual(
            [(d.a_id, d.role, d.b_id) for d in out],
            [("G", "grandparent", "A"), ("Z", "parent", "W")],
        )
        self.assertEqual(out[0].source_ids, ("8", "9"))
        self.assertEqual(out[1].source_ids, ("7", "6"))


class TheStableOrder(unittest.TestCase):
    def test_the_same_links_in_any_order_give_the_same_result(self) -> None:
        links = [
            link("1", "M", "parent", "S"),
            link("2", "S", "sibling", "D"),
            link("3", "G", "parent", "M"),
            link("4", "G", "parent", "E"),
            link("5", "E", "sibling", "F"),
        ]
        expected = derive(links)
        rng = random.Random(7)
        for _ in range(20):
            shuffled = list(links)
            rng.shuffle(shuffled)
            self.assertEqual(derive(shuffled), expected)


class TheHalfSiblingCase(unittest.TestCase):
    def test_a_half_sibling_still_gives_the_parent_link_as_documented(self) -> None:
        # A and B share only the other parent: the rule still fires, as the module says
        out = derive(
            [
                link("1", "X", "parent", "A"),
                link("2", "A", "sibling", "B"),
            ],
        )
        self.assertEqual(out, [Derived("X", "parent", "B", ("1", "2"))])


class TheModuleIsPure(unittest.TestCase):
    def test_it_imports_nothing_from_the_database_or_the_ai(self) -> None:
        path = Path(kd.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        for name in names:
            self.assertFalse(name.startswith(("dmbot.db", "dmbot.ai", "psycopg", "discord")), name)


if __name__ == "__main__":
    unittest.main()
