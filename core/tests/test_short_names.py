"""A person's first name becomes their short name, by rule (#3880): the pure rule, and the store's
one undoable write. Invented names (Tran Vosk, Bruno Hale, Shela Marr)."""

from __future__ import annotations

import unittest
from collections.abc import Sequence

from dmbot.campaigns import CampaignStore
from dmbot.memory.backup import MemorySection
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.models import PROPOSED, REJECTED, SHORT_NAME_SOURCE
from dmbot.memory.short_names import short_names, used_alone
from dmbot.memory.store import MemoryStore
from dmbot.names import not_names
from tests.pg import DatabaseTest

TRAN = ("e1", "Tran Vosk")
TEXT = "Tran Vosk came to town. Later that night, Tran slept by the fire."


def rule(
    people: list[tuple[str, str]], text: str = TEXT, extra: Sequence[tuple[str, str]] = ()
) -> list[tuple[str, str]]:
    return short_names(people, [*people, *extra], [text])


class TheRule(unittest.TestCase):
    def test_a_first_name_used_alone_joins_the_full_name(self) -> None:
        self.assertEqual(rule([TRAN]), [("e1", "Tran")])

    def test_two_people_sharing_a_first_word_neither_gets_it(self) -> None:
        both = [TRAN, ("e2", "Tran Hale")]
        self.assertEqual(rule(both, TEXT + " Tran Hale waved."), [])

    def test_another_entry_called_that_word_blocks_it(self) -> None:
        for other in (("e9", "Tran"), ("e9", "Tran Keep")):
            with self.subTest(other):
                self.assertEqual(rule([TRAN], extra=[other]), [])

    def test_a_title_is_never_a_short_name(self) -> None:
        for name in ("Lord Neverember", "Major Ursa", "Captain Bruno", "Old Shela", "The Bruno"):
            with self.subTest(name):
                first = name.split()[0]
                self.assertEqual(rule([("e1", name)], f"{name} left. Then {first} spoke."), [])

    def test_a_creature_kind_a_rules_word_or_table_word_is_not_one(self) -> None:
        for name in ("Goblin Grik", "Dragon Bruno", "Wizard Shela", "Dwarf Tran", "Monday Hale"):
            with self.subTest(name):
                first = name.split()[0]
                self.assertEqual(rule([("e1", name)], f"{name} left. Then {first} spoke."), [])

    def test_a_one_word_name_is_left_alone(self) -> None:
        self.assertEqual(rule([("e1", "Bruno")], "Then Bruno spoke."), [])

    def test_a_first_word_never_used_alone_is_not_added(self) -> None:
        self.assertEqual(rule([TRAN], "Tran Vosk came. Then Tran Vosk left. TRAN VOSK!"), [])
        self.assertEqual(rule([TRAN], "We met Shela."), [])

    def test_too_short_or_lower_case_is_not_one(self) -> None:
        self.assertEqual(rule([("e1", "Al Vosk")], "Then Al spoke."), [])
        self.assertEqual(rule([("e1", "unnamed - Tran's sister")], "Then unnamed spoke."), [])

    def test_a_person_described_by_another_s_name_gets_nothing_and_takes_nothing(self) -> None:
        kin = ("e2", "Tran's grandfather")
        self.assertEqual(rule([kin], "Tran's grandfather came. Then Tran's left."), [])
        self.assertEqual(rule([TRAN, kin], TEXT), [("e1", "Tran")])

    def test_already_there_or_turned_down_is_not_added_again(self) -> None:
        self.assertEqual(rule([TRAN], extra=[("e1", "Tran")]), [])

    def test_a_possessive_counts_as_alone(self) -> None:
        self.assertEqual(rule([TRAN], "Tran Vosk came. We took Tran's horse."), [("e1", "Tran")])

    def test_an_everyday_word_counts_only_mid_sentence(self) -> None:
        will = [("e1", "Will Hale")]
        self.assertEqual(rule(will, "Will Hale came. Will you help? I will."), [])
        self.assertEqual(rule(will, "Will Hale came. I will ask Will."), [("e1", "Will")])
        self.assertFalse(used_alone("Hope", "Marr", "Hope Marr. We hope so. Hope is gone."))

    def test_the_title_list_is_one_list(self) -> None:
        for word in ("Lord", "lady", "Dr.", "Sister", "Major", "Captain", "friend"):
            with self.subTest(word):
                self.assertTrue(not_names.is_title_word(word))
        self.assertFalse(not_names.is_title_word("Claude"))
        single = {w for w in not_names.ADDRESS_ALONE if " " not in w}
        self.assertTrue(single <= not_names.TITLE_WORDS)


GUILD, DM = 1, 7


class TheStore(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(MemorySection())
        self.memory = MemoryStore(self.db)
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)

    async def person(self, name: str) -> str:
        written = await self.memory.add_entity(
            GUILD, self.campaign.id, type="character", name=name, source=DM_SOURCE
        )
        return written.value.id

    async def shorts(self, entity_id: str) -> list[tuple[str, str, str]]:
        found = await self.memory.aliases(GUILD, self.campaign.id, entity_id=entity_id)
        return [(a.text, a.kind, a.status) for a in found if a.kind == "short"]

    async def test_claude_joins_claude_ambere_and_undo_takes_it_back(self) -> None:
        tran = await self.person("Tran Vosk")
        written = await self.memory.add_short_names(GUILD, self.campaign.id, [TEXT])
        self.assertEqual([(a.text, a.entity_id) for a in written.value], [("Tran", tran)])
        self.assertEqual(await self.shorts(tran), [("Tran", "short", PROPOSED)])
        rows = await self.memory.aliases(GUILD, self.campaign.id, entity_id=tran)
        self.assertEqual({a.source for a in rows if a.kind == "short"}, {SHORT_NAME_SOURCE})
        self.assertIn("tran", await self.memory.known_keys(GUILD, self.campaign.id))
        again = await self.memory.add_short_names(GUILD, self.campaign.id, [TEXT])
        self.assertEqual(again.value, [])  # (it has it now)
        assert written.batch is not None
        await self.memory.undo(GUILD, self.campaign.id, written.batch)
        self.assertEqual(await self.shorts(tran), [])

    async def test_two_people_sharing_a_first_word_neither_gets_it(self) -> None:
        a, b = await self.person("Tran Vosk"), await self.person("Tran Hale")
        written = await self.memory.add_short_names(GUILD, self.campaign.id, [TEXT])
        self.assertEqual(written.value, [])
        self.assertEqual((await self.shorts(a), await self.shorts(b)), ([], []))

    async def test_a_short_name_the_dm_turned_down_is_never_added_again(self) -> None:
        tran = await self.person("Tran Vosk")
        written = await self.memory.add_short_names(GUILD, self.campaign.id, [TEXT])
        await self.memory.update_alias(
            GUILD, self.campaign.id, written.value[0].id, source=DM_SOURCE, status=REJECTED
        )
        again = await self.memory.add_short_names(GUILD, self.campaign.id, [TEXT])
        self.assertEqual(again.value, [])
        self.assertEqual(await self.shorts(tran), [])

    async def test_lord_neverember_gets_no_lord(self) -> None:
        await self.person("Lord Neverember")
        text = "Lord Neverember spoke. Then Lord answered."
        self.assertEqual(
            (await self.memory.add_short_names(GUILD, self.campaign.id, [text])).value, []
        )


if __name__ == "__main__":
    unittest.main()
