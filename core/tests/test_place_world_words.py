"""The queue door's word lists (migration 0463) and the store's (`memory/map_facts.py`) are two
copies of one fact: SQL cannot import Python, so the door spells the land, climate and how-it-holds
words itself. This pure test fails when the two drift apart, so a word added to the store without
the door shows up here instead of as a refused press on the website (#4287; Supervisor's note on
#4315). No database."""

from __future__ import annotations

import importlib
import unittest
from types import ModuleType

from dmbot.memory import map_facts as mf

MIGRATION = "dmbot.migrations.0463_place_world_door"


def _module() -> ModuleType:  # (the name starts with digits, so it cannot be a plain import)
    return importlib.import_module(MIGRATION)


class TheDoorsWordsMatchTheStores(unittest.TestCase):
    def test_the_land_words_are_the_stores(self) -> None:
        self.assertEqual(_module().TERRAIN.split("|"), list(mf.VALUES[mf.TERRAIN]))

    def test_the_climate_words_are_the_stores(self) -> None:
        self.assertEqual(_module().CLIMATE.split("|"), list(mf.VALUES[mf.CLIMATE]))

    def test_each_how_list_appears_in_the_doors_checks_exactly_as_the_store_has_it(self) -> None:
        door = "".join(new for _old, new, _n in _module()._QUEUE_SWAPS)
        for layer in (mf.TERRAIN, mf.CLIMATE, mf.CONTROL):
            self.assertIn("(" + "|".join(mf.HOWS[layer]) + ")", door, layer)

    def test_the_four_actions_are_the_websites_and_the_bots(self) -> None:
        from dmbot.memory.web_actions import MAP_ACTIONS
        from dmbot.web.all_names import ACTIONS

        self.assertEqual(set(_module().ACTIONS), set(MAP_ACTIONS))
        self.assertTrue(set(MAP_ACTIONS) <= set(ACTIONS))


class TheWorldNotesActionsMatch(unittest.TestCase):
    def test_the_doors_world_actions_are_the_bots(self) -> None:
        # (#4285: 0481 lets the website queue them; the bot's applier must take each one)
        from dmbot.memory.web_actions import WORLD_ACTIONS

        door = importlib.import_module("dmbot.migrations.0481_world_notes_door")
        self.assertEqual(set(door.ACTIONS), set(WORLD_ACTIONS))
