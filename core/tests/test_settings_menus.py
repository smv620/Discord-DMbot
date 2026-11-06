"""The Settings card's two menus (#2281): 👁️ Who can see the DM screen and 🗂️ Campaign
management, with Back, per viewer; and the words that point to them."""

import re
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.campaigns.models import HandoverOffer
from dmbot.dm_screen import settings as settings_module
from dmbot.dm_screen.settings import (
    BackToSettingsButton,
    ManagementMenuButton,
    SettingsButton,
    VisibilityMenuButton,
    management_text,
    management_view,
    settings_text,
    settings_view,
    visibility_view,
)
from tests.test_dm_screen_settings import CAMPAIGN, DM, PLAYER, campaign, press

LIMIT = 25  # Discord: five rows of five


def _label(item: Any) -> str:
    return str(getattr(item, "item", item).label)


def labels(view: discord.ui.View) -> list[str]:
    return [_label(i) for i in view.children]


class Menus(unittest.TestCase):
    def test_the_card_holds_both_menu_buttons_for_a_dm_who_has_something_in_them(self) -> None:
        c = campaign()
        self.assertEqual(labels(settings_view(c, None, DM))[2:4], [
            "Who can see the DM screen", "Campaign management",
        ])  # fmt: skip

    def test_a_player_has_neither_menu_and_no_management_button(self) -> None:
        shown = labels(settings_view(campaign(), None, PLAYER))
        self.assertIn("Who can see the DM screen", shown)  # it is read-only for them anyway
        self.assertNotIn("Campaign management", shown)  # nothing in it they could use

    def test_the_management_card_has_what_each_viewer_can_use_and_back(self) -> None:
        c = campaign()
        dm = labels(management_view(c, None, DM))
        for wanted in ("Transcript retention", "Delete this campaign"):
            self.assertTrue(any(wanted in x for x in dm), (wanted, dm))
        self.assertEqual(dm[-1], "Back to Settings")
        nobody = management_view(c, None, PLAYER)
        self.assertEqual(labels(nobody), ["Back to Settings"])

    def test_every_card_fits_discord(self) -> None:
        c = campaign()
        for view in (
            settings_view(c, None, DM),
            visibility_view(c),
            management_view(c, None, DM),
        ):
            self.assertLessEqual(len(view.children), LIMIT)
            rows = {i.row for i in view.children if i.row is not None}
            self.assertLessEqual(max(rows, default=0), 4)
            for item in view.children:
                self.assertLessEqual(len(_label(item)), 25)

    def test_the_new_buttons_match_their_templates(self) -> None:
        for cls, item in (
            (VisibilityMenuButton, VisibilityMenuButton(CAMPAIGN)),
            (ManagementMenuButton, ManagementMenuButton(CAMPAIGN)),
            (BackToSettingsButton, BackToSettingsButton(CAMPAIGN)),
        ):
            custom_id = str(item.item.custom_id)
            self.assertLessEqual(len(custom_id), 100)
            self.assertIsNotNone(cls.__discord_ui_compiled_template__.fullmatch(custom_id))

    def test_each_viewer_gets_the_management_buttons_they_can_use(
        self,
    ) -> None:
        owner, co_dm, manager = 7, 8, 9
        c = replace(campaign(), owner_user_id=owner, dm_user_ids=frozenset({owner, co_dm}))
        mine = labels(management_view(c, None, owner))
        for wanted in (
            "Hand over",
            "Pause this campaign",
            "Transcript retention",
            "Game masters",
            "Delete this campaign",
        ):
            self.assertTrue(any(wanted in x for x in mine), (wanted, mine))
        self.assertEqual(mine[-1], "Back to Settings")
        theirs = labels(management_view(c, None, co_dm))
        for wanted in ("Transcript retention", "Game masters", "Delete this campaign"):
            self.assertTrue(any(wanted in x for x in theirs), (wanted, theirs))
        for not_wanted in ("Hand over", "Pause", "Take back"):
            self.assertFalse(any(not_wanted in x for x in theirs), (not_wanted, theirs))
        # a viewer who is not a DM (a server manager, say): no 🗂️ on the main card, the 👁️
        # choices stay (the press path for a real manager is in `Presses`)
        main = labels(settings_view(c, None, manager))
        self.assertNotIn("Campaign management", main)
        self.assertIn("Who can see the DM screen", main)
        self.assertEqual(labels(management_view(c, None, manager)), ["Back to Settings"])

    def test_a_waiting_offer_can_be_taken_back_only_by_who_made_it(self) -> None:
        owner, co_dm = 7, 8
        c = replace(campaign(), owner_user_id=owner, dm_user_ids=frozenset({owner, co_dm}))
        waiting = HandoverOffer(
            1,
            c.guild_id,
            c.id,
            owner,
            9,
            "Oskar",
            "Mirelle",
            1,
            "open",
            None,
        )
        mine = labels(management_view(c, waiting, owner))
        self.assertTrue(any("Take back" in x for x in mine), mine)
        self.assertFalse(any(x.endswith("Hand over") for x in mine), mine)
        other = labels(management_view(c, waiting, co_dm))
        self.assertFalse(any("Take back" in x for x in other), other)

    def test_a_paused_campaign_says_so_on_the_main_card_and_offers_unpause(self) -> None:
        owner = 7
        c = replace(campaign(), owner_user_id=owner, paused=True)
        self.assertIn("Paused", settings_text(c, None, owner))
        self.assertNotIn("Paused", settings_text(replace(c, paused=False), None, owner))
        shown = labels(management_view(c, None, owner))
        self.assertTrue(any("Unpause" in x for x in shown), shown)
        self.assertFalse(any("Pause this campaign" in x for x in shown), shown)

    def test_the_management_card_names_the_owner_and_pause(self) -> None:
        text = management_text(campaign(), None, DM)
        self.assertIn("Campaign management", text)
        self.assertIn("transcripts", text.lower())
        self.assertIn("Back to Settings", text)
        self.assertIn("**Owner:**", text)

    def test_the_main_card_says_where_the_rest_went_only_to_who_has_something_there(self) -> None:
        self.assertIn("Campaign management", settings_text(campaign(), None, DM))
        self.assertNotIn("Campaign management", settings_text(campaign(), None, PLAYER))


class Presses(unittest.IsolatedAsyncioTestCase):
    async def test_the_menu_buttons_swap_the_card_in_place_and_back_returns(self) -> None:
        for button, expect in (
            (VisibilityMenuButton(CAMPAIGN), "Who can see the DM screen for"),
            (ManagementMenuButton(CAMPAIGN), "Campaign management for"),
        ):
            it = press(DM)
            it.response.edit_message = AsyncMock()
            allowed = (campaign(), it.guild, it.client.campaigns)
            with patch.object(settings_module, "_may", AsyncMock(return_value=allowed)):
                await button.callback(it)
            kwargs = it.response.edit_message.await_args.kwargs
            self.assertIn(expect, kwargs["content"])
            self.assertIn("Back to Settings", labels(kwargs["view"]))

            it = press(DM)
            it.response.edit_message = AsyncMock()
            with patch.object(settings_module, "_may", AsyncMock(return_value=allowed)):
                await BackToSettingsButton(CAMPAIGN).callback(it)
            self.assertIn("Settings for", it.response.edit_message.await_args.kwargs["content"])

    async def test_a_server_manager_who_is_no_dm_opens_management_with_only_the_way_back(
        self,
    ) -> None:
        it = press(PLAYER, found=campaign(), manager=True)  # (the real `_may`, not a stand-in)
        it.response.edit_message = AsyncMock()
        await ManagementMenuButton(CAMPAIGN).callback(it)
        self.assertEqual(
            labels(it.response.edit_message.await_args.kwargs["view"]), ["Back to Settings"]
        )

    async def test_an_edit_that_fails_answers_the_press_privately(self) -> None:
        it = press(DM)
        it.response.is_done = MagicMock(return_value=False)
        it.response.edit_message = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "x")
        )
        allowed = (campaign(), it.guild, it.client.campaigns)
        with patch.object(settings_module, "_may", AsyncMock(return_value=allowed)):
            await ManagementMenuButton(CAMPAIGN).callback(it)
        sent = it.response.send_message.await_args
        self.assertEqual(sent.args[0], settings_module.FAILED)
        self.assertTrue(sent.kwargs["ephemeral"])
        it.followup.send.assert_not_awaited()

    async def test_a_refused_press_changes_nothing(self) -> None:
        it = press(PLAYER)
        it.response.edit_message = AsyncMock()
        with patch.object(settings_module, "_may", AsyncMock(return_value=None)):
            await ManagementMenuButton(CAMPAIGN).callback(it)
        it.response.edit_message.assert_not_awaited()


SRC = Path(__file__).resolve().parent.parent / "src" / "dmbot"
MOVED = ("Transcript", "Game masters", "Delete", "Hand over", "Take it on", "Pause")


class Words(unittest.TestCase):
    def test_no_message_points_at_a_place_that_moved(self) -> None:
        # A message that says "Settings → Hand over" or "Settings, then Pause" sends a DM to a
        # button that is not on the first card any more; every such path goes through
        # 🗂️ Campaign management. The old name of the panel button must be gone too.
        found: list[str] = []
        steps = re.compile(r"Settings(?: → |, then | and |, )(?!🗂️|👁️)([^.\n\"]{0,40})")
        for path in SRC.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "My buttons" in text:
                found.append(f"{path.name}: My buttons")
            for m in steps.finditer(text):
                if any(w in m.group(1) for w in MOVED):
                    found.append(f"{path.name}: {m.group(0)!r}")
        self.assertEqual(found, [])

    def test_the_panel_and_the_timed_out_menus_use_the_new_words(self) -> None:
        from dmbot.dm_screen import dm_bot_panel, game_masters, handover

        self.assertIn("DMbot Main Menu", dm_bot_panel.PANEL_TEXT)
        for text in (handover.MENU_TIMED_OUT, game_masters.MENU_TIMED_OUT):
            self.assertIn("🗂️ Campaign management", text)

    def test_the_settings_button_is_still_a_dynamic_item(self) -> None:
        self.assertIsNotNone(
            SettingsButton.__discord_ui_compiled_template__.fullmatch(f"dmbot:settings:{CAMPAIGN}")
        )
