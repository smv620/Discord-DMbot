"""The kind of each story-graph connection (#2942): the fixed table, and that the graph sends it
on every link it already shows, to a player as to a DM."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import link_kinds
from dmbot.memory.link_kinds import ALLY, FAMILY, GROUP, OTHER, WORK, link_kind
from dmbot.memory.ontology import CORE_PREDICATES
from dmbot.web import relationships

# What every relationship type the ontology ships with is, written out so a new one is decided.
EXPECTED = {
    "kin_of": FAMILY,
    "ally_of": ALLY,
    "member_of": GROUP,
    "of_people": GROUP,
    "serves": WORK,
    "sworn_to": WORK,
    "owns": WORK,
    "carries": WORK,
    "located_in": WORK,
    "enemy_of": OTHER,
    "associated_with": OTHER,
    "knows": OTHER,
    "appears_in": OTHER,
    "did": OTHER,
    "plays": OTHER,
    "led_to": OTHER,
    "in_thread": OTHER,
    "advances": OTHER,
    "affected": OTHER,
    "resolves": OTHER,
    "promised_by": OTHER,
    "promised_to": OTHER,
    "rival_of": OTHER,  # (a rival is shown as an enemy is: not a friend, not family)
    "mentor_of": WORK,
    "leads": GROUP,
    "founded": GROUP,
    "captive_of": OTHER,
    "contains": WORK,
    "disguised_as": OTHER,  # (secret by nature; drawn like any other connection, #3091)
}


class TheKinds(unittest.TestCase):
    def test_each_relationship_type_gives_its_kind(self) -> None:
        for predicate, kind in EXPECTED.items():
            with self.subTest(predicate=predicate):
                self.assertEqual(link_kind(predicate), kind)

    def test_every_type_the_ontology_ships_is_decided_here(self) -> None:
        self.assertEqual({p.key for p in CORE_PREDICATES}, set(EXPECTED))

    def test_an_unknown_type_or_a_campaigns_own_is_other(self) -> None:
        self.assertEqual(link_kind("rivals_of_the_old_guard"), OTHER)
        self.assertEqual(link_kind(""), OTHER)

    def test_only_the_five_kinds_are_ever_sent(self) -> None:
        self.assertEqual(set(link_kinds.BY_PREDICATE.values()) | {OTHER}, set(link_kinds.KINDS))


def row(predicate: str, **more: Any) -> dict[str, Any]:
    return {
        "a": "a" * 32,
        "b": "b" * 32,
        "id": "i" * 32,
        "predicate": predicate,
        "detail": "",
        "secret": False,
        "at": 1,
        **more,
    }


class TheGraphLinks(unittest.TestCase):
    def kinds(self, *predicates: str, can_edit: bool) -> list[str]:
        rows = [row(p, id=str(i) * 32, at=i) for i, p in enumerate(predicates)]
        (edge,) = relationships.edges_from(rows, can_edit=can_edit)
        return [x["kind"] for x in edge["links"]]

    def test_a_link_carries_its_kind_for_a_dm_and_for_a_player(self) -> None:
        for can_edit in (True, False):
            with self.subTest(can_edit=can_edit):
                self.assertEqual(
                    self.kinds("kin_of", "member_of", "made_up", can_edit=can_edit),
                    [FAMILY, GROUP, OTHER],
                )

    def test_the_other_fields_of_a_link_are_unchanged(self) -> None:
        (edge,) = relationships.edges_from([row("ally_of")], can_edit=False)
        (link,) = edge["links"]
        self.assertEqual(
            set(link),
            {"id", "words", "back", "secret", "revealed", "pinned", "check", "direction", "kind"},
        )
        self.assertFalse(link["secret"])  # (still masked for a player)


if __name__ == "__main__":
    unittest.main()
