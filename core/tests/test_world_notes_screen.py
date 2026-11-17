"""World notes in Discord (#4285 slice 1b): the words a phone can show, the pick-then-see flow, ✅
and 🚫 each one Undo with ↩ Undo, Keep all for the lines shown, a stale press redrawn, and only a
DM. Invented names. The flow needs Postgres."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import map_facts as mf
from dmbot.memory import world_notes as wn
from dmbot.memory.lookup import LookupCache
from dmbot.memory.map_fact_store import Read
from dmbot.ui import names as ui
from dmbot.ui import world_notes as screen
from tests.test_memory_names import DM, GUILD, MANAGER, NamesTest


def place_note(**kw: Any) -> wn.Note:
    fact = mf.Fact("f", "p", mf.TERRAIN, "arctic", "", "", "", mf.READ, mf.FROM_TEXT, False)
    base: dict[str, Any] = {
        "id": "place:f", "kind": wn.PLACE, "entity_id": "p", "name": "Icewind Dale",
        "value": "arctic", "layer": mf.TERRAIN, "session_at": 5, "fact": fact,
    }  # fmt: skip
    return wn.Note(**{**base, **kw})


def language_note() -> wn.Note:
    return wn.Note("language:l", wn.LANGUAGE, "c", "Brewner", "Giant")


class TheWords(unittest.TestCase):
    def test_a_menu_choice_is_short_with_the_guess_first(self) -> None:
        got = screen.option(place_note())
        self.assertEqual(
            (got.label, got.description), ("Icewind Dale: ice and snow", "Land · from a session")
        )
        lang = screen.option(language_note())
        self.assertEqual(
            (lang.label, lang.description), ("Brewner: Giant", "Language · from your lore")
        )
        long = screen.option(place_note(name="N" * 200))
        self.assertLessEqual(len(long.label), 100)

    def test_not_right_says_it_wont_come_back_for_each_kind(self) -> None:
        # (#4362: a language the DM says no to is remembered, as a place is)
        self.assertIn(screen.NEVER_AGAIN, screen.done_text(place_note(), kept=False))
        self.assertIn(screen.NEVER_AGAIN, screen.done_text(language_note(), kept=False))

    def test_the_list_fits_and_says_how_many_are_shown(self) -> None:
        many = [place_note(id=f"place:{n}", name=f"Place {n} " + "x" * 60) for n in range(60)]
        text = screen.list_text(many)
        self.assertLessEqual(len(text), 2000)
        self.assertIn("of 60", text)
        self.assertEqual(screen.list_text([]), screen.EMPTY)
        with_note = f"{screen.STALE}\n\n{text}"
        self.assertLessEqual(len(with_note), 2000)

    def test_text_menu_and_keep_all_share_one_shown_set(self) -> None:
        # (#4285: Keep all never keeps a line the DM didn't see; a later group never skips ahead)
        many = [place_note(id=f"place:{n}", name=f"Place {n} " + "x" * 40) for n in range(40)]
        many.append(language_note())
        shown = screen.shown_notes(many)
        self.assertLessEqual(len(shown), screen.MENU_MAX)
        self.assertNotIn(language_note(), shown)
        text = screen.list_text(many)
        for n in shown:
            self.assertIn(screen.full_line(n), text)
        menu_ids = [n.id for n in shown]
        self.assertEqual(wn.of_kind(shown, wn.PLACE), menu_ids)

    def test_keep_all_counts_one_and_many(self) -> None:
        self.assertEqual(screen.keep_all_label(1, wn.PLACE), "✅ Keep the 1 place")
        self.assertEqual(screen.keep_all_label(12, wn.LANGUAGE), "✅ Keep all 12 languages")
        self.assertLessEqual(len(screen.keep_all_label(999, wn.LANGUAGE)), 80)


class TheScreen(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot.lookup = LookupCache(self.memory)
        self.dale = await ui.save_name(self.memory, self.campaign, "Icewind Dale", "place", [], [])
        await self.memory.set_place_level(
            GUILD, self.campaign.id, self.dale.id, "region", source="dm"
        )
        await self.memory.add_read_map_facts(
            GUILD, self.campaign.id, [Read(self.dale.id, mf.TERRAIN, "arctic")], source="background"
        )

    async def shown(self, user: int = DM) -> tuple[str, Any]:
        it = self.it(user)
        await screen.show_world_notes(it, self.campaign.id)
        return self.last(it)

    def last(self, it: Any) -> tuple[str, Any]:
        if it.response.edited:
            text, view = it.response.edited[-1]
            return str(text), view
        text, kw = it.response.sent[-1]
        return str(text), kw.get("view") if isinstance(kw, dict) else None

    async def test_only_a_dm_sees_them(self) -> None:
        text, _ = await self.shown(MANAGER)
        self.assertIn(screen.ONLY_DM, text)
        self.assertNotIn("Icewind Dale", text)

    async def test_pick_then_keep_then_undo(self) -> None:
        text, menu = await self.shown()
        self.assertIn("**Icewind Dale** · land: ice and snow", text)
        menu.pick._values = [menu.notes[0].id]
        it = self.it()
        await menu._picked(it)
        text, one = self.last(it)
        self.assertIn("DMbot guessed this from your lore.", text)
        it = self.it()
        await one._keep(it)
        text, done = self.last(it)
        self.assertIn("✅ Kept: **Icewind Dale** · land: ice and snow.", text)
        self.assertEqual(await self.memory.world_notes(GUILD, self.campaign.id), [])
        it = self.it()
        await done._undo(it)
        text, _ = self.last(it)
        self.assertIn(screen.UNDONE, text)
        self.assertEqual(len(await self.memory.world_notes(GUILD, self.campaign.id)), 1)

    async def test_not_right_on_a_place_says_it_wont_come_back(self) -> None:
        _, menu = await self.shown()
        one = screen.OneNote(self.campaign.id, menu.notes[0])
        it = self.it()
        await one._not_right(it)
        text, _ = self.last(it)
        self.assertIn("🚫 Taken out:", text)
        self.assertIn(screen.NEVER_AGAIN, text)

    async def test_keep_all_keeps_the_lines_shown(self) -> None:
        _, menu = await self.shown()
        labels = [getattr(c, "label", "") for c in menu.children]
        self.assertIn("✅ Keep the 1 place", labels)
        it = self.it()
        await menu._keep_all(wn.PLACE)(it)
        text, _ = self.last(it)
        self.assertIn("✅ Kept 1 place.", text)

    async def test_a_stale_press_redraws_the_list(self) -> None:
        _, menu = await self.shown()
        one = screen.OneNote(self.campaign.id, menu.notes[0])
        await self.memory.keep_world_notes(GUILD, self.campaign.id, [menu.notes[0].id], source="dm")
        it = self.it()
        await one._keep(it)
        text, _ = self.last(it)
        self.assertIn(screen.STALE, text)
        self.assertIn(screen.EMPTY, text)

    async def test_a_player_pressing_an_old_button_changes_nothing(self) -> None:
        _, menu = await self.shown()
        one = screen.OneNote(self.campaign.id, menu.notes[0])
        it = self.it(MANAGER)
        await one._keep(it)
        self.assertIn(screen.ONLY_DM, self.last(it)[0])
        self.assertEqual(len(await self.memory.world_notes(GUILD, self.campaign.id)), 1)
        menu.pick._values = [menu.notes[0].id]
        it = self.it(MANAGER)
        await menu._picked(it)  # (a pick is checked too)
        self.assertIn(screen.ONLY_DM, self.last(it)[0])
        kept = await self.memory.keep_world_notes(
            GUILD, self.campaign.id, [menu.notes[0].id], source="dm"
        )
        it = self.it(MANAGER)
        await screen.Done(self.campaign.id, kept.batch)._undo(it)
        self.assertIn(screen.ONLY_DM, self.last(it)[0])
        self.assertEqual(await self.memory.world_notes(GUILD, self.campaign.id), [])

    async def test_keep_all_never_keeps_a_line_added_after_the_list(self) -> None:
        _, menu = await self.shown()
        lake = await ui.save_name(self.memory, self.campaign, "Maer Dualdon", "place", [], [])
        await self.memory.set_place_level(GUILD, self.campaign.id, lake.id, "region", source="dm")
        await self.memory.add_read_map_facts(
            GUILD, self.campaign.id, [Read(lake.id, mf.CLIMATE, "cold")], source="background"
        )
        it = self.it()
        await menu._keep_all(wn.PLACE)(it)
        self.assertIn("✅ Kept 1 place.", self.last(it)[0])
        left = await self.memory.world_notes(GUILD, self.campaign.id)
        self.assertEqual([n.entity_id for n in left], [lake.id])

    async def test_a_failure_says_so_once_and_changes_nothing(self) -> None:
        _, menu = await self.shown()
        one = screen.OneNote(self.campaign.id, menu.notes[0])

        async def broken(*_: Any, **__: Any) -> Any:
            raise RuntimeError("boom")

        self.memory.keep_world_notes = broken  # type: ignore[method-assign]
        it = self.it()
        await one._keep(it)
        replies = [t for t, _ in it.response.edited] + [t for t, _ in it.response.sent]
        self.assertEqual([str(t) for t in replies if screen.FAILED in str(t)], [screen.FAILED])

    async def test_a_language_note_keeps_and_comes_back_with_undo(self) -> None:
        brewner = await ui.save_name(self.memory, self.campaign, "Brewner", "npc", [], [])
        await self.memory.add_story_language(
            GUILD, self.campaign.id, brewner.id, "Giant", source="background"
        )
        notes = await self.memory.world_notes(GUILD, self.campaign.id)
        lang = next(n for n in notes if n.kind == wn.LANGUAGE)
        it = self.it()
        await screen.OneNote(self.campaign.id, lang)._not_right(it)
        text, done = self.last(it)
        self.assertIn(screen.NEVER_AGAIN, text)
        self.assertEqual(await self.memory.story_languages(GUILD, self.campaign.id, brewner.id), [])
        it = self.it()
        await done._undo(it)
        langs = await self.memory.story_languages(GUILD, self.campaign.id, brewner.id)
        self.assertEqual([lg.name for lg in langs], ["Giant"])
