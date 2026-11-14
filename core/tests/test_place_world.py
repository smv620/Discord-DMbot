"""A place's land, climate and who rules it, on its card in Discord (#4256): the card's lines (the
ux-critic's words on #4256), and the DM's screen, which stays open after each pick. Invented
names."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.memory import map_facts as mf
from dmbot.memory.lookup import LookupCache
from dmbot.memory.map_fact_store import Read
from dmbot.ui import name_card
from dmbot.ui import names as ui
from dmbot.ui import place_world as pw
from tests.test_memory_names import DM, GUILD, MANAGER, NamesTest


def fact(layer: str, value: str, how: str = "", status: str = mf.CONFIRMED) -> mf.Fact:
    return mf.Fact("f" + value, "p", layer, value, "", how, "", status, mf.FROM_DM, True)


class TheLines(unittest.TestCase):
    def test_nothing_known_is_no_line(self) -> None:
        self.assertEqual(pw.card_lines({}, {}, dm=True), "")

    def test_labelled_parts_and_the_ruler_on_its_own_line(self) -> None:
        held = {
            mf.TERRAIN: mf.Held((fact(mf.TERRAIN, "mountain", "mostly"),)),
            mf.CLIMATE: mf.Held((fact(mf.CLIMATE, "cold", "dry"),), "dale"),
            mf.CONTROL: mf.Held((fact(mf.CONTROL, "council", "claims"),)),
        }
        names = {"dale": "Icewind Dale", "council": "the Council"}
        self.assertEqual(
            pw.card_lines(held, names, dm=False),
            "\n🗺️ **Land:** mountains · **Climate:** cold and dry (same as Icewind Dale)"
            "\n👑 **Claimed by:** the Council",
        )

    def test_partly_is_shown_and_a_read_value_is_marked_for_the_dm_only(self) -> None:
        held = {mf.TERRAIN: mf.Held((fact(mf.TERRAIN, "forest", "partly", mf.READ),))}
        dm = pw.card_lines(held, {}, dm=True)
        self.assertIn("**Land:** partly forest (DMbot read this)", dm)
        self.assertIn(f"Tap {pw.BUTTON} to check.", dm)
        self.assertNotIn("DMbot read", pw.card_lines(held, {}, dm=False))

    def test_a_ruler_this_viewer_cant_see_is_left_out(self) -> None:
        held = {mf.CONTROL: mf.Held((fact(mf.CONTROL, "secret-cult", "rules"),))}
        self.assertEqual(pw.card_lines(held, {}, dm=False), "")

    def test_the_menus_fit_discord(self) -> None:
        self.assertLessEqual(len(pw.land_options({}, {})), 25)
        self.assertLessEqual(len(pw.climate_options({})), 25)
        groups = [(f"g{i}", f"Group {i}") for i in range(40)]
        names = dict(groups)
        options, left_out = pw.ruler_options({}, names, groups)
        self.assertEqual((len(options), left_out), (pw.GROUPS_SHOWN + 1, 40 - pw.GROUPS_SHOWN))


class OnTheCard(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot.lookup = LookupCache(self.memory)
        self.dale = await ui.save_name(self.memory, self.campaign, "Icewind Dale", "place", [], [])
        await self.memory.set_place_level(
            GUILD, self.campaign.id, self.dale.id, "region", source="dm"
        )
        self.council = await ui.save_name(
            self.memory, self.campaign, "Council of Speakers", "faction", [], []
        )

    async def card(self, user: int = DM) -> tuple[str, Any]:
        self.bot.lookup.mark_all_stale()  # type: ignore[union-attr]
        it = self.it(user)
        await name_card.show_card(it, self.campaign.id, self.dale.id)
        return str(it.response.sent[0][0]), it.response.sent[0][1]["view"]

    async def screen(self, user: int = DM) -> tuple[str, Any]:
        it = self.it(user)
        await pw.show_world(it, self.campaign.id, self.dale.id)
        if not it.response.edited:
            return str(it.response.sent[-1][0]), None
        text, view = it.response.edited[-1]
        return str(text), view

    def labels(self, view: Any) -> list[str]:
        return [str(getattr(c, "label", "")) for c in view.children]

    async def test_the_button_is_for_dms_only(self) -> None:
        _text, view = await self.card()
        self.assertIn(pw.BUTTON, self.labels(view))
        _text, view = await self.card(MANAGER)
        self.assertNotIn(pw.BUTTON, self.labels(view))
        text, _view = await self.screen(MANAGER)
        self.assertEqual(text, pw.ONLY_DM)

    async def test_the_dm_sets_land_and_climate_and_the_screen_stays(self) -> None:
        _text, view = await self.screen()
        view.land._values = ["mountain"]  # (as Discord sets it on a pick)
        it = self.it()
        await view._land_picked(it)
        text, view = it.response.edited[-1]
        self.assertIn("🗺️ Noted: **Icewind Dale** is mostly mountains.", text)
        view.climate._values = ["cold:dry"]
        it = self.it()
        await view._climate_picked(it)
        text, view = it.response.edited[-1]
        self.assertIn("is cold and dry. Places inside it are too", text)
        card, _ = await self.card()
        self.assertIn("🗺️ **Land:** mountains · **Climate:** cold and dry", card)

    async def test_the_ruler_and_how_it_holds_it(self) -> None:
        _text, view = await self.screen()
        view.ruler._values = [self.council.id]
        it = self.it()
        await view._ruler_picked(it)
        text, view = it.response.edited[-1]
        self.assertIn("👑 Noted: **Council of Speakers** rules **Icewind Dale**.", text)
        self.assertIn("🚩 Claims it", self.labels(view))
        it = self.it()
        await view._how("claims")(it)
        card, _ = await self.card()
        self.assertIn("👑 **Claimed by:** Council of Speakers", card)

    async def test_players_see_only_what_the_dm_kept_and_showed(self) -> None:
        await self.memory.add_read_map_facts(
            GUILD, self.campaign.id, [Read(self.dale.id, mf.TERRAIN, "arctic")],
            source="background",
        )  # fmt: skip
        dm_card, _ = await self.card()
        self.assertIn("(DMbot read this)", dm_card)
        player_card, _ = await self.card(MANAGER)
        self.assertNotIn("Land:", player_card)
        await self.memory.set_map_fact(
            GUILD, self.campaign.id, self.dale.id, mf.TERRAIN, "arctic", source="dm"
        )
        _text, view = await self.screen()
        self.assertIn(pw.SHOW_BUTTON, self.labels(view))  # (hidden until the DM shows it)
        it = self.it()
        await view._shown(it)
        self.assertIn("👁️ Noted: players who can see", it.response.edited[-1][0])
        player_card, _ = await self.card(MANAGER)
        self.assertIn("**Land:** ice and snow", player_card)
