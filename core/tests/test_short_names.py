"""A person's first name becomes their short name, by rule (#3880): the pure rule, and the store's
one undoable write. Invented names (Tran Vosk, Bruno Hale, Shela Marr)."""

from __future__ import annotations

import unittest
from collections.abc import Sequence

from dmbot.campaigns import CampaignStore
from dmbot.memory.backup import MemorySection
from dmbot.memory.extractor import heard_texts
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.models import PROPOSED, REJECTED, SHORT_NAME_SOURCE
from dmbot.memory.short_names import could_be_short_name, short_names, used_alone
from dmbot.memory.store import MemoryStore
from dmbot.names import not_names
from dmbot.transcript.models import Line
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
        for name in ("Goblin Grik", "Dragon Bruno", "Wizard Shela", "Dwarf Tran", "Monday Hale",
                     "Naga Shela", "Wraith Tran"):  # fmt: skip
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

    def test_a_capital_at_a_sentence_or_line_start_never_counts(self) -> None:
        will = [("e1", "Will Hale")]
        self.assertEqual(rule(will, "Will Hale came. Will you help? I will."), [])
        self.assertEqual(short_names(will, will, ["Will Hale came.", "Will you open it?"]), [])
        self.assertEqual(rule(will, "Will Hale came. I will ask Will."), [("e1", "Will")])
        self.assertFalse(used_alone("Hope", "Hope Marr. We hope so. Hope is gone."))

    def test_each_text_is_its_own_line(self) -> None:
        self.assertEqual(short_names([TRAN], [TRAN], ["Tran Vosk came.", "Tran left."]), [])
        self.assertEqual(
            short_names([TRAN], [TRAN], ["Tran Vosk came.", "Then Tran left."]), [("e1", "Tran")]
        )

    def test_followed_by_another_capitalised_word_is_not_alone(self) -> None:
        # (someone with no entry yet, said before the DM names them)
        self.assertEqual(rule([TRAN], "Tran Vosk came. Then Tran Hale waved."), [])
        self.assertFalse(used_alone("Red", "Then the Red Wizards arrived."))
        self.assertTrue(used_alone("Tran", "Then Tran, tired, left."))

    def test_an_epithet_is_not_a_short_name(self) -> None:
        for name in ("Red Ilsa", "Black Grim", "Mad Tom", "Iron Bruno"):
            with self.subTest(name):
                first = name.split()[0]
                self.assertEqual(rule([("e1", name)], f"{name} came. Then {first} left."), [])

    def test_an_undone_short_name_counts_as_one_the_entry_has(self) -> None:
        # (the store passes the names the DM undid as names the entry already has)
        self.assertEqual(rule([TRAN], extra=[("e1", "Tran")]), [])

    def test_the_title_list_holds_every_single_address_word(self) -> None:
        for word in ("lord", "lady", "dr", "sister", "major", "captain", "friend", "old"):
            with self.subTest(word):
                self.assertIn(word, not_names.TITLE_WORDS)
        single = {w for w in not_names.ADDRESS_ALONE if " " not in w}
        self.assertTrue(single <= not_names.TITLE_WORDS)


class BeforeARead(unittest.TestCase):
    """The run before a lore read (#4224): a word the text also writes before another name may be a
    namesake the read has yet to store, so it waits for the run after."""

    def test_a_namesake_in_the_unread_text_holds_the_word_back(self) -> None:
        people = [("pc", "Cerric Vanya'elen")]
        text = "Cerric Vanya'elen rode in. The smith Cerric Dorn waved, and Cerric laughed."
        self.assertEqual(short_names(people, people, [text], unread=True), [])
        self.assertEqual(
            short_names(people, people, [text]), [("pc", "Cerric")]
        )  # (after a read the stored owners decide, as before)

    def test_the_persons_own_full_name_is_no_namesake(self) -> None:
        people = [("pc", "Cerric Vanya'elen")]
        text = "Cerric Vanya'elen rode in, and later Cerric laughed."
        self.assertEqual(short_names(people, people, [text], unread=True), [("pc", "Cerric")])


class WhatTheReaderMayLookAt(unittest.TestCase):
    def test_only_people_who_agree_and_never_last_time_s_recap(self) -> None:
        def line(user: int, text: str) -> Line:
            return Line(started_ms=0, user_id=user, heard=text, text=text)

        lines = [line(7, "Last time, Tran left."), line(8, "Then Tran ran."), line(9, "Hi Tran.")]
        self.assertEqual(heard_texts(lines, {7, 8}, recap_lines={1}), ["Then Tran ran."])


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

    async def test_tran_joins_tran_vosk_and_undo_takes_it_back_for_good(self) -> None:
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
        after = await self.memory.add_short_names(GUILD, self.campaign.id, [TEXT])
        self.assertEqual((after.value, after.batch), ([], None))  # (undone stays undone)
        self.assertEqual(await self.shorts(tran), [])

    async def test_nothing_to_add_writes_nothing(self) -> None:
        await self.person("Tran Vosk")
        written = await self.memory.add_short_names(GUILD, self.campaign.id, ["No names here."])
        self.assertEqual((written.value, written.batch), ([], None))

    async def test_an_entry_the_dm_rejected_outright_blocks_nothing(self) -> None:
        tran = await self.person("Tran Vosk")
        scanned = await self.memory.add_entity(
            GUILD, self.campaign.id, type="character", name="Tran", source="scan"
        )
        await self.memory.set_entity_status(
            GUILD, self.campaign.id, scanned.value.id, REJECTED, source=DM_SOURCE
        )
        written = await self.memory.add_short_names(GUILD, self.campaign.id, [TEXT])
        self.assertEqual([a.entity_id for a in written.value], [tran])

    async def test_a_secret_name_of_another_entry_blocks_the_word(self) -> None:
        tran = await self.person("Tran Vosk")
        other = await self.person("Bruno Hale")
        await self.memory.add_alias(
            GUILD, self.campaign.id, other, "Tran", kind="nickname", source=DM_SOURCE, secret=True
        )
        self.assertEqual(
            (await self.memory.add_short_names(GUILD, self.campaign.id, [TEXT])).value, []
        )
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


class CouldBeAShortName(unittest.TestCase):
    def test_an_ordinary_first_name_is_one(self) -> None:
        for word in ("Cerric", "Wit", "Tran", "Éowyn", "O'Neil", "Anne-Marie"):
            with self.subTest(word=word):
                self.assertTrue(could_be_short_name(word))

    def test_a_word_that_is_not_shaped_like_a_name_is_not(self) -> None:
        for word in ("Al", "cerric", "Cerric's", "Mira,", "Tom2", ""):
            with self.subTest(word=word):
                self.assertFalse(could_be_short_name(word))

    def test_a_title_a_creature_or_an_epithet_is_not(self) -> None:
        for word in ("Captain", "Sir", "The", "Goblin", "Red", "Mad"):
            with self.subTest(word=word):
                self.assertFalse(could_be_short_name(word))


if __name__ == "__main__":
    unittest.main()
