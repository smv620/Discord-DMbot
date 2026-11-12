"""The languages on the DM's name card (#4229 slice 1b): one line that says where they come from,
and a DM-only screen to add, check or take away the ones the story gives a character with no sheet.
Invented names."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.memory.lookup import LookupCache
from dmbot.memory.models import DM as BY_DM
from dmbot.memory.models import Known
from dmbot.ui import languages_card as lc
from dmbot.ui import name_card
from dmbot.ui import names as ui
from tests.test_memory_names import DM, GUILD, MANAGER, NamesTest


class TheLine(unittest.TestCase):
    def test_none_known_is_no_line(self) -> None:
        self.assertEqual(lc.languages_line([]), "")

    def test_a_sheets_are_said_to_come_from_it(self) -> None:
        line = lc.languages_line([Known("Common", "sheet"), Known("Dwarvish", "sheet")])
        self.assertEqual(line, "\n🗣️ **Languages:** Common, Dwarvish (from the sheet)")

    def test_the_ones_dmbot_read_are_marked(self) -> None:
        line = lc.languages_line([Known("Elvish", BY_DM), Known("Sylvan", "read")])
        self.assertEqual(line, "\n🗣️ **Languages:** Elvish · not checked yet: Sylvan")
        only = lc.languages_line([Known("Sylvan", "read")])
        self.assertEqual(only, "\n🗣️ **Languages:** not checked yet: Sylvan")

    def test_a_long_list_is_cut_short(self) -> None:
        line = lc.languages_line([Known("x" * 50, BY_DM) for _ in range(10)])
        self.assertLessEqual(len(line), lc.LINE_MAX)
        self.assertTrue(line.endswith("…"))


class OnTheCard(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot.lookup = LookupCache(self.memory)
        self.npc = await ui.save_name(self.memory, self.campaign, "Brewner", "npc", [], [])

    async def card(self, user: int = DM) -> tuple[str, Any]:
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]
        it = self.it(user)
        await name_card.show_card(it, self.campaign.id, self.npc.id)
        return str(it.response.sent[0][0]), it.response.sent[0][1]["view"]

    async def screen(self) -> tuple[str, Any]:
        it = self.it()
        await lc.show_languages(it, self.campaign.id, self.npc.id)
        text, view = it.response.edited[-1]  # (the fake keeps an edit as text and view)
        return str(text), view

    async def add(self, typed: str, user: int = DM) -> Any:
        form = lc.AddLanguageForm(self.campaign.id, self.npc.id)
        form.name = SimpleNamespace(value=typed)  # type: ignore[assignment]
        it = self.it(user)
        await form.on_submit(it)
        return it

    async def stored(self) -> list[Any]:
        return await self.memory.story_languages(GUILD, self.campaign.id, self.npc.id)

    def labels(self, view: Any) -> list[str]:
        return [str(getattr(c, "label", "")) for c in view.children]

    async def test_the_button_is_for_dms_only(self) -> None:
        _text, view = await self.card()
        self.assertIn(lc.LANGUAGES_BUTTON, self.labels(view))
        _text, view = await self.card(MANAGER)
        self.assertNotIn(lc.LANGUAGES_BUTTON, self.labels(view))

    async def test_the_dm_adds_one_and_sees_it_on_the_card(self) -> None:
        it = await self.add("Dwarvish")
        self.assertIn("Added: **Dwarvish**", it.response.edited[-1][0])
        text, _view = await self.card()
        self.assertIn("🗣️ **Languages:** Dwarvish", text)
        manager_text, _ = await self.card(MANAGER)
        self.assertNotIn("Dwarvish", manager_text)  # a manager who is not a DM is not shown it

    async def test_a_manager_who_is_not_a_dm_cannot_add_one(self) -> None:
        it = await self.add("Dwarvish", user=MANAGER)
        self.assertEqual(it.response.sent[-1][0], lc.ONLY_DM)
        self.assertEqual(await self.stored(), [])

    async def test_the_dm_checks_the_ones_dmbot_read(self) -> None:
        await self.memory.add_story_language(
            GUILD, self.campaign.id, self.npc.id, "Sylvan", source="background"
        )
        text, view = await self.screen()
        self.assertIn("not checked yet: Sylvan", text)
        self.assertIn(lc.CHECKED_BUTTON, self.labels(view))
        it = self.it()
        await view._checked(it)
        self.assertIn("Marked as right: Sylvan", it.response.edited[-1][0])
        (row,) = await self.stored()
        self.assertEqual(row.given_by, BY_DM)

    async def test_a_press_by_a_manager_who_is_not_a_dm_is_refused(self) -> None:
        await self.memory.add_story_language(
            GUILD, self.campaign.id, self.npc.id, "Sylvan", source="background"
        )
        _text, view = await self.screen()
        view.pick._values = [view.pick.options[0].value]
        for press in (view._remove, view._checked):
            it = self.it(MANAGER)
            await press(it)
            self.assertEqual(it.response.sent[-1][0], lc.ONLY_DM)
        (row,) = await self.stored()
        self.assertEqual(row.given_by, "read")  # nothing changed

    async def test_a_character_with_a_sheet_is_told_to_change_it_there(self) -> None:
        pc = await ui.save_name(self.memory, self.campaign, "Testa", "player_character", [], [])
        async with self.db.guild(GUILD) as conn:  # (a typed sheet with no languages on it)
            await conn.execute(
                "INSERT INTO character_sheets (guild_id, campaign_id, entity_id, sheet, source,"
                " fetched_at, player_id) VALUES (%s, %s, %s, '{}'::jsonb, 'typed', 1, 5)",
                (GUILD, self.campaign.id, pc.id),
            )
        it = self.it()
        await lc.show_languages(it, self.campaign.id, pc.id)
        text, view = it.response.edited[-1]
        self.assertEqual(text, lc.HAS_SHEET)
        self.assertNotIn(lc.ADD_BUTTON, self.labels(view))

    async def test_the_dm_takes_one_away(self) -> None:
        await self.add("Dwarvish")
        _text, view = await self.screen()
        view.pick._values = [view.pick.options[0].value]  # (as Discord sets it on a pick)
        it = self.it()
        await view._remove(it)
        self.assertIn("Removed: **Dwarvish**", it.response.edited[-1][0])
        self.assertEqual(await self.stored(), [])
