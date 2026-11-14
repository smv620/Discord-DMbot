"""A lore upload's lineage or class for a character the campaign has already (#3926): asked, never
written silently. Keep it writes the link as the DM's change, Not this is remembered, Undo this
upload clears what it left, and only a confirmed character is asked about. Invented names only."""

from __future__ import annotations

import asyncio
import unittest
from collections import Counter

from dmbot.memory import trait_offers as to
from dmbot.memory import traits_read
from dmbot.memory.models import CLASS, CONFIRMED, PROPOSED, SPECIES, MemoryRuleError, NewName, Trait
from dmbot.ui import name_background as nb
from dmbot.ui import trait_questions as tq
from tests.test_memory_names import GUILD, NamesTest

DWARF = Trait(SPECIES, "Dwarf", "SRD 5.2.1", "2024", True)
CLERIC = Trait(CLASS, "Cleric", "SRD 5.2.1", "2024", True)
NOTICE = "a" * 32


class WhatChangesSomething(unittest.TestCase):
    def test_another_lineage_or_a_first_one_and_a_new_class(self) -> None:
        self.assertTrue(to.changes_something(DWARF, "Human", ()))
        self.assertTrue(to.changes_something(DWARF, None, ()))
        self.assertFalse(to.changes_something(DWARF, "dwarf", ()))
        self.assertTrue(to.changes_something(CLERIC, None, ["Fighter"]))
        self.assertFalse(to.changes_something(CLERIC, None, ["cleric"]))


class TheReaderOffers(unittest.TestCase):
    TEXT = "Torvin Ashgrove is a dwarf and a cleric of Lathander."

    def read(self, offers: list[traits_read.Found] | None) -> tuple[list[object], Counter[str]]:
        dropped: Counter[str] = Counter()
        got = traits_read.read_traits(
            ['- Torvin Ashgrove | lineage | dwarf | quote: "Torvin Ashgrove is a dwarf"',
             '- Torvin Ashgrove | lineage | elf | quote: "Torvin Ashgrove is an elf"'],
            self.TEXT, [], lambda w: ("Dwarf", "SRD 5.2.1", "2024") if w == "dwarf" else None,
            dropped, existing=("torvin ashgrove",), offers=offers,
        )  # fmt: skip
        return list(got), dropped

    def test_an_existing_name_s_checked_trait_becomes_an_offer_with_its_quote(self) -> None:
        offers: list[traits_read.Found] = []
        got, dropped = self.read(offers)
        self.assertEqual(got, [])  # never stored with the name
        (offer,) = offers  # the elf line is not in the text: dropped, not offered
        self.assertEqual((offer.trait, offer.quote), (DWARF, "Torvin Ashgrove is a dwarf"))
        self.assertEqual((dropped["trait_existing_checked"], dropped["trait_not_in_text"]), (1, 1))

    def test_without_offers_it_is_only_counted(self) -> None:
        got, dropped = self.read(None)
        self.assertEqual((got, dropped["trait_existing_name"]), ([], 2))


class TheCard(unittest.TestCase):
    def test_the_question_comes_first_then_where_the_lore_said_it(self) -> None:
        offer = to.Offer("e1", DWARF, "Torvin Ashgrove is a dwarf", "Notes, 9 Nov", "Torvin",
                         "Human")  # fmt: skip
        lines = tq.card_text(offer, 2).splitlines()
        self.assertIn("(2 waiting)", lines[0])
        self.assertEqual(lines[1], "Make **Torvin** a **dwarf**? DMbot has Torvin as **a human**.")
        self.assertEqual(lines[2:4], ['Your lore "Notes, 9 Nov" says so:',
                                      '> "Torvin Ashgrove is a dwarf"'])  # fmt: skip
        self.assertIn("Torvin becomes a dwarf instead of a human.", lines[-1])
        self.assertIn("Torvin stays human", lines[-1])

    def test_a_class_says_what_they_have_and_that_it_adds(self) -> None:
        offer = to.Offer("e1", CLERIC, entity_name="Torvin", classes=("Fighter",))
        text = tq.card_text(offer, 1)
        self.assertIn("Add **cleric** to **Torvin**'s classes? DMbot has Torvin as **fighter**.",
                      text)  # fmt: skip
        self.assertIn("the ones they have stay", text)
        first = tq.card_text(to.Offer("e1", DWARF, entity_name="Torvin"), 1)
        self.assertIn("DMbot has no lineage for Torvin yet.", first)

    def test_the_buttons_are_a_yes_and_a_no(self) -> None:
        self.assertEqual((tq.YES, tq.NO), ("✅ Yes, change it", "❌ No, leave it"))

    def test_each_answer_says_what_happened(self) -> None:
        offer = to.Offer("e1", DWARF, entity_name="Torvin", current="Human")
        self.assertIn("**Torvin** is now a **dwarf**", tq.kept_text(offer))
        self.assertIn("**Torvin** is a **human** again", tq.undone_text(offer))
        self.assertIn("won't ask about **Torvin** and **dwarf** again", tq.left_text(offer))


class TheStore(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = to.TraitOfferStore(self.db, lambda: 1_000)
        made = await self.memory.add_names(
            GUILD, self.campaign.id,
            [NewName("Torvin Ashgrove", "npc", CONFIRMED), NewName("Wren", "npc", PROPOSED)],
            source="dm",
        )  # fmt: skip
        torvin, wren = made.value
        assert torvin and wren
        self.torvin: str = torvin
        self.wren: str = wren
        await self.memory.set_rule_link(
            GUILD, self.campaign.id, torvin, SPECIES, "Human", source="dm",
            rules_source="SRD 5.2.1", edition="2024", known=True,
        )  # fmt: skip

    def offer(self, trait: Trait = DWARF, who: str | None = None) -> to.Offer:
        return to.Offer(who or self.torvin, trait, "Torvin Ashgrove is a dwarf", "Notes")

    async def test_only_a_confirmed_character_is_asked_and_waiting_shows_what_it_has(self) -> None:
        n = await self.store.offer(
            GUILD, self.campaign.id, [self.offer(), self.offer(who=self.wren)], NOTICE
        )
        self.assertEqual(n, 1)  # Wren is only proposed
        await self.memory.set_rule_link(
            GUILD, self.campaign.id, self.torvin, CLASS, "Fighter", source="dm",
            rules_source="SRD 5.2.1", edition="2024", known=True,
        )  # fmt: skip
        (waiting,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertEqual(
            (waiting.entity_name, waiting.current, waiting.classes),
            ("Torvin Ashgrove", "Human", ("Fighter",)),
        )
        self.assertEqual(await self.store.count(GUILD, self.campaign.id), 1)

    async def test_keep_replaces_the_lineage_as_one_undoable_change(self) -> None:
        await self.store.offer(GUILD, self.campaign.id, [self.offer()])
        batch = await self.store.keep(self.memory, GUILD, self.campaign.id, self.offer())
        links = await self.memory.rule_links(GUILD, self.campaign.id, {self.torvin})
        self.assertEqual([lk.name for lk in links if lk.kind == SPECIES], ["Dwarf"])
        self.assertEqual(await self.store.count(GUILD, self.campaign.id), 0)
        assert batch is not None
        await self.memory.undo(GUILD, self.campaign.id, batch)
        links = await self.memory.rule_links(GUILD, self.campaign.id, {self.torvin})
        self.assertEqual([lk.name for lk in links if lk.kind == SPECIES], ["Human"])

    async def test_not_this_is_never_asked_again(self) -> None:
        await self.store.offer(GUILD, self.campaign.id, [self.offer()])
        self.assertTrue(await self.store.never(GUILD, self.campaign.id, self.offer()))
        self.assertEqual(await self.store.offer(GUILD, self.campaign.id, [self.offer()]), 0)
        self.assertEqual(await self.store.waiting(GUILD, self.campaign.id), [])
        with self.assertRaises(MemoryRuleError):
            await self.store.keep(self.memory, GUILD, self.campaign.id, self.offer())

    async def test_undo_this_upload_clears_what_it_left_waiting(self) -> None:
        await self.store.offer(GUILD, self.campaign.id, [self.offer()], NOTICE)
        await self.store.offer(GUILD, self.campaign.id, [self.offer(CLERIC)], "b" * 32)
        self.assertEqual(await self.store.drop_for_notice(GUILD, self.campaign.id, NOTICE), 1)
        (left,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertEqual(left.trait.name, "Cleric")

    async def test_another_campaign_sees_none(self) -> None:
        await self.store.offer(GUILD, self.campaign.id, [self.offer()])
        other = (await self.campaigns.create(GUILD, "Sunken City", 7)).id
        self.assertEqual(await self.store.waiting(GUILD, other), [])
        self.assertEqual(await self.store.count(GUILD, other), 0)

    async def test_the_upload_asks_only_what_would_change_something(self) -> None:
        found = [
            traits_read.Found("Torvin Ashgrove", DWARF, "Torvin Ashgrove is a dwarf"),
            traits_read.Found("Torvin Ashgrove", Trait(SPECIES, "Human", "SRD 5.2.1", "2024",
                                                       True)),  # already his
            traits_read.Found("Wren", DWARF),  # only proposed
        ]  # fmt: skip
        n = await nb.ask_traits(self.memory, self.campaign, found, "Notes", NOTICE)
        self.assertEqual(n, 1)
        (waiting,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertEqual((waiting.trait.name, waiting.upload), ("Dwarf", "Notes"))

    async def test_two_presses_at_once_write_it_once_and_undo_gives_back_the_old(self) -> None:
        await self.store.offer(GUILD, self.campaign.id, [self.offer()])
        got = await asyncio.gather(
            self.store.keep(self.memory, GUILD, self.campaign.id, self.offer()),
            self.store.keep(self.memory, GUILD, self.campaign.id, self.offer()),
            return_exceptions=True,
        )
        won = [g for g in got if not isinstance(g, BaseException)]
        lost = [g for g in got if isinstance(g, to.NotWaiting)]
        self.assertEqual((len(won), len(lost)), (1, 1))
        assert isinstance(won[0], int)
        await self.memory.undo(GUILD, self.campaign.id, won[0])
        links = await self.memory.rule_links(GUILD, self.campaign.id, {self.torvin})
        self.assertEqual([lk.name for lk in links if lk.kind == SPECIES], ["Human"])

    async def test_a_question_already_true_is_not_shown_or_counted(self) -> None:
        await self.store.offer(GUILD, self.campaign.id, [self.offer()])
        await self.memory.set_rule_link(
            GUILD, self.campaign.id, self.torvin, SPECIES, "Dwarf", source="dm",
            rules_source="SRD 5.2.1", edition="2024", known=True,
        )  # fmt: skip
        self.assertEqual(await self.store.waiting(GUILD, self.campaign.id), [])
        self.assertEqual(await self.store.count(GUILD, self.campaign.id), 0)

    async def test_a_class_they_have_and_a_shared_name_are_not_asked(self) -> None:
        await self.memory.set_rule_link(
            GUILD, self.campaign.id, self.torvin, CLASS, "Cleric", source="dm",
            rules_source="SRD 5.2.1", edition="2024", known=True,
        )  # fmt: skip
        await self.memory.add_names(
            GUILD, self.campaign.id, [NewName("Mira", "npc", CONFIRMED)], source="dm"
        )
        await self.memory.add_alias(  # Torvin is called Mira too: two confirmed Miras
            GUILD, self.campaign.id, self.torvin, "Mira", kind="nickname", source="dm",
            status=CONFIRMED,
        )  # fmt: skip
        found = [
            traits_read.Found("Torvin Ashgrove", CLERIC),  # he has it
            traits_read.Found("Mira", DWARF),  # two confirmed Miras: whose?
        ]
        self.assertEqual(await nb.ask_traits(self.memory, self.campaign, found, "Notes", None), 0)
