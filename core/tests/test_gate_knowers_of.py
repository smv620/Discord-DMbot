"""`gate.knowers_of` (#3228) gives the characters `gate.reach` gives an answer for, from a map of
each group's members built once. Pure: no database. Invented names throughout."""

from __future__ import annotations

import random
import unittest

from dmbot.memory import gate
from dmbot.memory.gate import ALIAS, CONNECTION, ENTRY, PROPERTY, Statements


def slow(data: gate.Statements, kind: str, statement: str) -> set[str]:
    """The answer asked of `reach` for every character that could know (what the website did)."""
    listed = data.knowers.get((kind, statement), {})
    sure = gate._sure(listed)
    return {c for c in {*sure, *data.groups} if gate.reach(c, sure, data.groups) is not None}


class KnowersOf(unittest.TestCase):
    def data(self, seed: int) -> Statements:
        rng = random.Random(seed)
        people = [f"c{i}" for i in range(30)]
        groups = [f"g{i}" for i in range(5)]
        member_of = {
            p: frozenset(rng.sample(groups, rng.randrange(0, 3)))
            for p in people
            if rng.random() < 0.6
        }
        knowers: dict[tuple[str, str], dict[str, float] | frozenset[str]] = {}
        for n in range(80):
            kind = rng.choice([ENTRY, PROPERTY, CONNECTION, ALIAS])
            holders = rng.sample([*people, *groups], rng.randrange(0, 6))
            if rng.random() < 0.5:
                knowers[(kind, f"s{n}")] = frozenset(holders)
            else:
                knowers[(kind, f"s{n}")] = {h: rng.choice([0.0, 0.3, 1.0]) for h in holders}
        return Statements(
            knowers=knowers, groups=member_of, player_characters=frozenset(people[:4])
        )

    def test_the_same_characters_as_asking_reach_of_everyone(self) -> None:
        for seed in range(25):
            data = self.data(seed)
            who = gate.knowers_of(data)
            for kind, statement in [*data.knowers, (ENTRY, "unlisted")]:
                with self.subTest(seed=seed, kind=kind):
                    self.assertEqual(who(kind, statement), slow(data, kind, statement))

    def test_a_group_member_knows_what_the_group_knows_and_a_friend_does_not(self) -> None:
        data = Statements(
            knowers={(ENTRY, "x"): {"guild": 1.0, "tran": 1.0}},
            groups={"clyde": frozenset({"guild"}), "bruno": frozenset()},
        )
        self.assertEqual(gate.knowers_of(data)(ENTRY, "x"), {"guild", "tran", "clyde"})

    def test_a_statement_nobody_lists_is_known_by_no_one(self) -> None:
        self.assertEqual(gate.knowers_of(Statements(knowers={}))(ENTRY, "x"), set())
