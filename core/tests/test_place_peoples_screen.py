"""A place's culture and who lives there on its card and its own screen (#4257, MapBot 3a; the
ux-critic's words on #4257): the card's lines, the DM's screen with its menus and buttons, DMbot's
read marked for the DM alone, a player never seeing a read, and the words never "people",
"species" or "lineage". Invented names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import map_facts as mf
from dmbot.memory.lookup import LookupCache
from dmbot.memory.map_fact_store import Read
from dmbot.ui import name_card, world_notes
from dmbot.ui import names as ui
from dmbot.ui import place_peoples as pp
from dmbot.ui import place_world as pw
from tests.test_memory_names import DM, GUILD, MANAGER, NamesTest


def fact(
    layer: str, value: str, how: str = "", status: str = mf.CONFIRMED, part: str = ""
) -> mf.Fact:
    return mf.Fact("f" + value, "p", layer, value, "", how, part, status, mf.FROM_DM, True)


class TheLines(unittest.TestCase):
    def test_culture_and_who_lives_here_in_plain_words(self) -> None:
        held = {
            mf.CULTURE: mf.Held((fact(mf.CULTURE, "dwarf", "strong"),
                                 fact(mf.CULTURE, "elf", "traces"))),
            mf.POPULATION: mf.Held((fact(mf.POPULATION, "halfling", "most"),
                                    fact(mf.POPULATION, "human"))),
        }  # fmt: skip
        self.assertEqual(
            pw.card_lines(held, {}, dm=False),
            "\n🏛️ **Culture:** strongly dwarven, a little elven"
            "\n🏘️ **Who lives here:** mostly halflings, humans",
        )

    def test_a_part_and_once(self) -> None:
        held = {mf.CULTURE: mf.Held((fact(mf.CULTURE, "gnome", "once", part="the old quarter"),))}
        self.assertIn("once gnomish (in the old quarter)", pw.card_lines(held, {}, dm=False))

    def test_a_read_is_marked_for_the_dm_with_its_own_hint(self) -> None:
        held = {mf.POPULATION: mf.Held((fact(mf.POPULATION, "elf", "many", mf.READ),))}
        dm = pw.card_lines(held, {}, dm=True)
        self.assertIn("many elves (DMbot read this)", dm)
        self.assertIn(f"Tap {pw.PEOPLES_BUTTON} to check.", dm)
        self.assertNotIn(f"Tap {pw.BUTTON}", dm)  # (nothing on the 🗺️ screen to check)
        self.assertNotIn("DMbot read", pw.card_lines(held, {}, dm=False))

    def test_never_people_species_or_lineage(self) -> None:
        words = " ".join(
            [pp.HELP, pp.ONLY_DM, pp.BUTTON, *pp.STRENGTH_BUTTONS.values(),
             *pp.SHARE_LABELS.values(), world_notes.LAYER[mf.CULTURE],
             world_notes.LAYER[mf.POPULATION]]
            + [o.label for o in pp.culture_options(None) + pp.people_options(None)]
        ).casefold()  # fmt: skip
        for banned in ("people", "species", "lineage"):
            self.assertNotIn(banned, words)
        self.assertLessEqual(len(pp.culture_options(None)), 25)


class OnTheScreen(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot.lookup = LookupCache(self.memory)
        self.bree = await ui.save_name(self.memory, self.campaign, "Bree", "place", [], [])

    async def card(self, user: int = DM) -> tuple[str, Any]:
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]
        it = self.it(user)
        await name_card.show_card(it, self.campaign.id, self.bree.id)
        return str(it.response.sent[0][0]), it.response.sent[0][1]["view"]

    async def screen(self, user: int = DM) -> tuple[str, Any]:
        it = self.it(user)
        await pp.show_peoples(it, self.campaign.id, self.bree.id)
        if not it.response.edited:
            return str(it.response.sent[-1][0]), None
        text, view = it.response.edited[-1]
        return str(text), view

    def labels(self, view: Any) -> list[str]:
        return [str(getattr(c, "label", "")) for c in view.children]

    async def test_the_button_and_screen_are_for_dms_only(self) -> None:
        _text, view = await self.card()
        self.assertIn(pw.PEOPLES_BUTTON, self.labels(view))
        _text, view = await self.card(MANAGER)
        self.assertNotIn(pw.PEOPLES_BUTTON, self.labels(view))
        text, _view = await self.screen(MANAGER)
        self.assertEqual(text, pp.ONLY_DM)

    async def test_the_dm_sets_a_culture_its_strength_and_who_lives_there(self) -> None:
        text, view = await self.screen()
        self.assertIn("Nothing set yet.", text)
        view.culture_pick._values = ["dwarf"]  # (as Discord sets it on a pick)
        it = self.it()
        await view._culture_picked(it)
        text, view = it.response.edited[-1]
        self.assertIn("🏛️ Noted: **Bree**'s culture: strongly dwarven.", text)
        self.assertIn("A little", self.labels(view))
        it = self.it()
        await view._strength("traces")(it)
        text, view = it.response.edited[-1]
        self.assertIn("culture: a little dwarven.", text)
        view.people_pick._values = ["halfling"]
        it = self.it()
        await view._people_picked(it)
        text, view = it.response.edited[-1]
        self.assertIn("🏘️ Noted: who lives in **Bree**: halflings.", text)
        view.share_pick._values = ["most"]
        it = self.it()
        await view._share_picked(it)
        card, _ = await self.card()
        self.assertIn("🏛️ **Culture:** a little dwarven", card)
        self.assertIn("🏘️ **Who lives here:** mostly halflings", card)

    async def test_players_never_see_a_read_and_keep_makes_it_the_dms(self) -> None:
        await self.memory.add_read_map_facts(
            GUILD, self.campaign.id, [Read(self.bree.id, mf.POPULATION, "elf", "many")],
            source="background",
        )  # fmt: skip
        dm_card, _ = await self.card()
        self.assertIn("many elves (DMbot read this)", dm_card)
        player_card, _ = await self.card(MANAGER)
        self.assertNotIn("elves", player_card)
        _text, view = await self.screen()
        it = self.it()
        await view._keep(it)
        text, _view = it.response.edited[-1]
        self.assertIn("✅ Noted: what DMbot read about **Bree** is right.", text)
        dm_card, _ = await self.card()
        self.assertNotIn("DMbot read", dm_card)


if __name__ == "__main__":
    unittest.main()
