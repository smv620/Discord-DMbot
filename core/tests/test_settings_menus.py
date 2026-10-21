"""The Settings card's two menus (#2281): 👁️ Who can see the DM screen and 🗂️ Campaign
management, with Back, per viewer; and the words that point to them."""

import re
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import discord

from dmbot.dm_screen import settings as settings_module
from dmbot.dm_screen.settings import (
    BackToSettingsButton,
    ManagementMenuButton,
    SettingsButton,
    VisibilityMenuButton,
    management_text,
    management_view,
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

    def test_the_management_card_names_the_owner_and_pause(self) -> None:
        text = management_text(campaign(), None, DM)
        self.assertIn("Campaign management", text)
        self.assertIn("transcripts", text.lower())
        self.assertIn("Back to Settings", text)
        self.assertIn("**Owner:**", text)

    def test_the_main_card_says_where_the_rest_went_only_to_who_has_something_there(self) -> None:
        from dmbot.dm_screen.settings import settings_text

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

    async def test_a_refused_press_changes_nothing(self) -> None:
        it = press(PLAYER)
        it.response.edit_message = AsyncMock()
        with patch.object(settings_module, "_may", AsyncMock(return_value=None)):
            await ManagementMenuButton(CAMPAIGN).callback(it)
        it.response.edit_message.assert_not_awaited()


SRC = Path(__file__).resolve().parent.parent / "src" / "dmbot"
OLD_PATHS = ("Settings → 📜 Transcripts", "Settings → 👥 Game masters", "👤 My buttons")


class Words(unittest.TestCase):
    def test_no_message_points_at_a_place_that_moved(self) -> None:
        # "Settings → <thing that now lives under Campaign management>" would send a DM to a button
        # that is not on the first card. Every such path goes through 🗂️ Campaign management.
        found: list[str] = []
        moved = ("Transcript retention", "Game masters", "Delete", "Hand over", "Pause")
        for path in SRC.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for old in OLD_PATHS:
                if old in text:
                    found.append(f"{path.name}: {old}")
            for m in re.finditer(r"Settings\s*→ (?!🗂️|👁️)([^.\n\"]{0,40})", text):
                if any(w in m.group(1) for w in moved):
                    found.append(f"{path.name}: {m.group(0)!r}")
        self.assertEqual(found, [])

    def test_the_dm_bot_channel_calls_it_the_main_menu(self) -> None:
        from dmbot.dm_screen import dm_bot_panel

        self.assertIn("DMbot Main Menu", Path(dm_bot_panel.__file__).read_text(encoding="utf-8"))

    def test_the_settings_button_is_still_a_dynamic_item(self) -> None:
        self.assertIsNotNone(
            SettingsButton.__discord_ui_compiled_template__.fullmatch(f"dmbot:settings:{CAMPAIGN}")
        )
