"""🐾 Travels with… on a character's name card (#3505): who it may travel with, what pressing
saves, and the checks every step makes. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import CONFIRMED
from dmbot.ui import companion, name_card
from dmbot.ui import names as ui
from tests import test_memory_names as base

GUILD, DM, MANAGER = base.GUILD, base.DM, base.MANAGER


class TravelsWith(base.NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from dmbot.memory.lookup import LookupCache

        self.bot.lookup = LookupCache(self.memory)
        self.sled = await ui.save_name(self.memory, self.campaign, "Sled dog", "npc", [], [])

    def fresh(self) -> None:
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]

    async def pc(self, name: str, user: int) -> Any:
        made = await ui.save_name(self.memory, self.campaign, name, "npc", [], [])
        await self.memory.confirm_entity(
            GUILD, self.campaign.id, made.id, "player_character", source="dm", played_by=user
        )
        self.fresh()
        return made

    async def menu(self, who: int = DM, entity: Any = None) -> Any:
        it = self.it(who)
        await companion.start(it, self.campaign.id, (entity or self.sled).id)
        return it

    async def kept(self) -> list[Any]:
        found = await self.memory.relations(
            GUILD, self.campaign.id, entity_id=self.sled.id, include_secret=True
        )
        return [r for r in found if r.predicate == "travels_with"]

    async def test_the_menu_lists_the_player_characters_and_never_itself(self) -> None:
        await self.pc("Mara", 1001)
        await self.pc("Tavin", 1002)
        it = await self.menu()
        text, view = it.response.edited[0]
        self.assertIn("Who does Sled dog travel with?", text)
        select = view.children[0]
        self.assertEqual(sorted(o.label for o in select.options), ["Mara", "Tavin"])

    async def test_pressing_a_name_saves_the_confirmed_link_and_says_so(self) -> None:
        mara = await self.pc("Mara", 1001)
        await self.menu()
        it = self.it()
        await companion.save(it, self.campaign.id, self.sled.id, mara.id)
        (kept,) = await self.kept()
        self.assertEqual((kept.object_id, kept.status), (mara.id, CONFIRMED))
        self.assertIn("Saved: **Sled dog** travels with **Mara**", str(it.response.edited[0][0]))

    async def test_the_party_is_offered_only_once_the_campaign_has_one(self) -> None:
        mara = await self.pc("Mara", 1001)
        _, view = (await self.menu()).response.edited[0]
        self.assertNotIn(companion.PARTY_LABEL, [o.label for o in view.children[0].options])
        await self.memory.party_on_assignment(GUILD, self.campaign.id)
        self.fresh()
        _, view = (await self.menu()).response.edited[0]
        labels = [o.label for o in view.children[0].options]
        self.assertEqual(labels[0], "Mara")
        self.assertEqual(len(labels), 2)
        party = next(o.value for o in view.children[0].options if o.value != mara.id)
        it = self.it()
        await companion.save(it, self.campaign.id, self.sled.id, party)
        (kept,) = await self.kept()
        self.assertEqual(kept.object_id, party)
        self.assertIn("travels with **the party**", str(it.response.edited[0][0]))

    async def test_a_manager_who_is_not_a_dm_is_refused_and_nothing_is_saved(self) -> None:
        mara = await self.pc("Mara", 1001)
        refused = await self.menu(MANAGER)
        self.assertIn(companion.ONLY_DM, str(refused.response.sent or refused.followup.send))
        await companion.save(self.it(MANAGER), self.campaign.id, self.sled.id, mara.id)
        self.assertEqual(await self.kept(), [])

    async def test_a_name_that_was_not_offered_saves_nothing(self) -> None:
        npc = await ui.save_name(self.memory, self.campaign, "Old Mill", "place", [], [])
        await self.pc("Mara", 1001)
        await companion.save(self.it(), self.campaign.id, self.sled.id, npc.id)  # forged pick
        self.assertEqual(await self.kept(), [])

    async def test_a_player_character_cannot_be_the_companion(self) -> None:
        mara = await self.pc("Mara", 1001)
        it = await self.menu(entity=mara)
        self.assertIn(companion.NOT_A_COMPANION, str(it.response.sent or it.followup.send))

    async def test_no_player_characters_yet_says_what_to_do(self) -> None:
        it = await self.menu()
        self.assertIn(companion.NOBODY, str(it.response.sent or it.followup.send))

    async def test_the_card_has_the_button_only_when_asked(self) -> None:
        def labels(view: Any) -> list[str]:
            return [getattr(c, "label", "") for c in view.children]

        shown = name_card.NameCard(self.campaign.id, self.sled.id, companion=True)
        plain = name_card.NameCard(self.campaign.id, self.sled.id)
        self.assertIn(companion.BUTTON, labels(shown))
        self.assertNotIn(companion.BUTTON, labels(plain))

    async def test_a_refused_press_leaves_the_card_working(self) -> None:
        shown = name_card.NameCard(self.campaign.id, self.sled.id, companion=True)
        it = self.it()
        await shown._travels(it)  # no player characters yet: told, not replaced
        self.assertFalse(shown.is_finished())
        await self.pc("Mara", 1001)
        await shown._travels(self.it())
        self.assertTrue(shown.is_finished())

    async def test_only_the_group_the_sessions_made_is_the_party(self) -> None:
        await self.pc("Mara", 1001)
        await ui.save_name(self.memory, self.campaign, "The party", "faction", [], [])
        self.fresh()
        _, view = (await self.menu()).response.edited[0]
        self.assertEqual([o.label for o in view.children[0].options], ["Mara"])

    async def test_a_menu_never_holds_more_than_25_and_keeps_the_party(self) -> None:
        for i in range(30):
            await self.pc(f"Hero {i:02d}", 2000 + i)
        await self.memory.party_on_assignment(GUILD, self.campaign.id)
        self.fresh()
        _, view = (await self.menu()).response.edited[0]
        labels = [o.label for o in view.children[0].options]
        self.assertEqual(len(labels), 25)
        self.assertEqual(labels[-1], companion.PARTY_LABEL)

    async def test_a_merged_player_character_saves_nothing(self) -> None:
        mara = await self.pc("Mara", 1001)
        tavin = await self.pc("Mara the Bold", 1001)
        await self.menu()
        await self.memory.merge(
            GUILD, self.campaign.id, tavin.id, mara.id, source="dm", dm_said_same=True
        )
        self.fresh()
        await companion.save(self.it(), self.campaign.id, self.sled.id, mara.id)
        self.assertEqual(await self.kept(), [])
