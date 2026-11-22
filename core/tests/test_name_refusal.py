"""Why `add_names` would refuse one name, by a fixed label (#5056): `name_refusal` makes the same
checks, so the lore read can leave out only that name. Pure: the core kinds, no database."""

from __future__ import annotations

import unittest

from dmbot.memory import store
from dmbot.memory.models import CONFIRMED, PROPOSED, MemoryRuleError, NewName
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.ontology import CORE_PREDICATES, CORE_TYPES, Ontology
from tests.test_memory_names import GUILD, NamesTest


def onto() -> Ontology:
    return Ontology.build(list(CORE_TYPES), list(CORE_PREDICATES))


class NameRefusal(unittest.TestCase):
    def test_a_good_name_passes(self) -> None:
        self.assertEqual(store.name_refusal(onto(), NewName("Mirelle", "npc", PROPOSED)), "")

    def test_each_rule_has_its_label(self) -> None:
        cases = {
            store.REFUSED_SPELLING: NewName("Mirelle", "npc", PROPOSED, others=("x" * 300,)),
            store.REFUSED_NAME: NewName("DM", "place", PROPOSED),
            store.REFUSED_KIND: NewName("Fireball", "spell", PROPOSED),
            store.REFUSED_ROLE: NewName("Oakmere", "place", PROPOSED, role="npc"),
        }
        for label, name in cases.items():
            with self.subTest(label):
                self.assertEqual(store.name_refusal(onto(), name), label)

    def test_a_player_character_must_wait(self) -> None:
        pc = NewName("Tavin", "character", CONFIRMED, role="player_character")
        self.assertEqual(store.name_refusal(onto(), pc), store.REFUSED_ROLE)
        waiting = NewName("Tavin", "character", PROPOSED, role="player_character")
        self.assertEqual(store.name_refusal(onto(), waiting), "")

    def test_an_unknown_kind(self) -> None:
        self.assertEqual(
            store.name_refusal(onto(), NewName("Oakmere", "nowhere", PROPOSED)), store.REFUSED_KIND
        )


CASES = [
    NewName("Mirelle", "npc", PROPOSED, others=("x" * 300,)),
    NewName("DM", "place", PROPOSED),
    NewName("Fireball", "spell", PROPOSED),
    NewName("Oakmere", "place", PROPOSED, role="npc"),
    NewName("Tavin", "character", CONFIRMED, role="player_character"),
]


PASSING = [
    NewName("Mirelle", "npc", PROPOSED),
    NewName("Tavin", "character", PROPOSED, role="player_character"),
    NewName("Oakmere", "place", PROPOSED, others=("Oak Mere",), secrets=("the Drowned Town",)),
    NewName("Grimfang", "creature", PROPOSED),
]
CASES += [
    NewName("Wenna", "npc", PROPOSED, others=("   ",)),
    NewName("Brindle", "place", PROPOSED, secrets=("y" * 300,)),
]


class TheSameAsAddNames(NamesTest):
    """`name_refusal` and `add_names` agree both ways, so the two can't drift apart: every name
    one refuses the other refuses, and every name one passes the other stores."""

    async def test_each_passing_name_is_stored(self) -> None:
        onto = await self.memory.ontology(GUILD, self.campaign.id)
        for name in PASSING:
            with self.subTest(name.name):
                self.assertEqual(store.name_refusal(onto, name), "")
        written = await self.memory.add_names(GUILD, self.campaign.id, PASSING, source=DM_SOURCE)
        self.assertTrue(all(i is not None for i in written.value))

    async def test_each_refused_name_is_refused_by_the_store_too(self) -> None:
        onto = await self.memory.ontology(GUILD, self.campaign.id)
        for name in CASES:
            with self.subTest(name.name):
                self.assertTrue(store.name_refusal(onto, name))
                with self.assertRaises(MemoryRuleError):
                    await self.memory.add_names(GUILD, self.campaign.id, [name], source=DM_SOURCE)


if __name__ == "__main__":
    unittest.main()
