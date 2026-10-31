"""The family role menu driven through the database (#3102): "is family of" picks a role, the
saved link reads the same role on each person's card, and a mother with a brother's link offers
the parent as a worked-out suggestion. Goes through FamilyRole._picked and the real store; invented
names only."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from dmbot.memory.lookup import CampaignLookup
from dmbot.ui import name_card
from dmbot.ui import names as ui
from tests.test_memory_names import GUILD, NamesTest


class TheFamilyRoleFlow(NamesTest):
    async def name(self, text: str) -> Any:
        return await ui.save_name(self.memory, self.campaign, text, "npc", [], [])

    async def pick(self, first: Any, other: Any, role: str) -> None:
        """The DM says "is family of" from first's card, then picks the role in the menu."""
        menu = name_card.FamilyRole(
            self.campaign.id, first.id, other.id, "kin_of", (first.name, other.name),
            secret=False, forced=False,
        )  # fmt: skip
        menu.role = SimpleNamespace(values=[role])  # type: ignore[assignment]
        await menu._picked(self.it())

    async def card_words(self, card_of: Any, other: Any) -> str:
        """The words the link reads on card_of's card, as the card shows them."""
        names = CampaignLookup.build(await self.memory.lookup_data(GUILD, self.campaign.id))
        rels = await self.memory.relations(GUILD, self.campaign.id, entity_id=card_of.id)
        (row,) = name_card._rows(names, card_of.id, rels, True)
        return f"{row.words} {row.other}"

    async def test_a_role_picked_from_the_menu_is_saved_with_its_direction(self) -> None:
        mira, aldric = await self.name("Mira"), await self.name("Aldric")
        await self.pick(mira, aldric, "grandson")
        (rel,) = await self.memory.relations(GUILD, self.campaign.id, entity_id=mira.id)
        self.assertEqual((rel.detail, rel.directional), ("grandson", True))

    async def test_a_niece_reads_as_one_name_on_one_card_and_the_other_on_the_other(self) -> None:
        mira, aldric = await self.name("Mira"), await self.name("Aldric")
        await self.pick(mira, aldric, "niece")
        self.assertEqual(await self.card_words(mira, aldric), "is the niece of Aldric")
        self.assertEqual(await self.card_words(aldric, mira), "has a niece Mira")

    async def test_a_cousin_reads_the_same_on_both_cards(self) -> None:
        mira, aldric = await self.name("Mira"), await self.name("Aldric")
        await self.pick(mira, aldric, "cousin")
        self.assertEqual(await self.card_words(mira, aldric), "is the cousin of Aldric")
        self.assertEqual(await self.card_words(aldric, mira), "is the cousin of Mira")

    async def test_just_family_saves_no_role_and_reads_as_family_on_both_cards(self) -> None:
        mira, aldric = await self.name("Mira"), await self.name("Aldric")
        await self.pick(mira, aldric, name_card.JUST_FAMILY)
        (rel,) = await self.memory.relations(GUILD, self.campaign.id, entity_id=mira.id)
        self.assertEqual(rel.detail, "")
        self.assertEqual(await self.card_words(mira, aldric), "is family of Aldric")
        self.assertEqual(await self.card_words(aldric, mira), "is family of Mira")

    async def test_a_mother_and_a_brother_offer_the_parent_as_a_worked_out_suggestion(self) -> None:
        aldric, bryn, mira = (
            await self.name("Aldric"),
            await self.name("Bryn"),
            await self.name("Mira"),
        )
        await self.pick(aldric, bryn, "mother")
        await self.pick(bryn, mira, "brother")
        await self.memory.rework_family(GUILD, self.campaign.id)
        waiting = await self.memory.worked_out_waiting(GUILD, self.campaign.id)
        self.assertEqual(
            [(s.subject, s.role, s.object) for s in waiting],
            [("Aldric", "parent", "Mira")],
        )
        self.assertEqual(
            waiting[0].from_words,
            ("Aldric is the mother of Bryn", "Bryn is the brother of Mira"),
        )
