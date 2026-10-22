"""📜 Upload backstory on the 👤 DMbot Main Menu (#2380): only the campaign's DMs get it, it opens
the same backstory menu as 📝 Names > From my story, and the press asks again."""

import unittest
from typing import Any
from unittest.mock import AsyncMock

import discord

from dmbot.dm_screen import dm_bot_panel as panel
from dmbot.dm_screen.backstory_button import BackstoryButton
from dmbot.dm_screen.dm_bot_buttons import role_view
from dmbot.dm_screen.dm_bot_panel import Role
from dmbot.ui import name_background as ui_bg
from tests.test_dm_screen_settings import CAMPAIGN, DM, PLAYER, campaign
from tests.test_names_button import _press

ROWS, PER_ROW = 5, 5


class WhoGetsIt(unittest.TestCase):
    def test_only_the_game_master_card_has_it(self) -> None:
        for role in Role:
            with self.subTest(role):
                view = role_view(role, campaign(), sheets=True)
                has = any(isinstance(i, BackstoryButton) for i in view.children)
                self.assertEqual(has, role is Role.GAME_MASTER)
                self.assertEqual(panel.may_press(role, panel.Button.BACKSTORY), has)

    def test_the_card_fits_five_rows_of_five(self) -> None:
        view = role_view(Role.GAME_MASTER, campaign(), sheets=True)
        self.assertLessEqual(len(view.children), ROWS * PER_ROW)

    def test_it_is_labelled_in_plain_words(self) -> None:
        button = BackstoryButton(CAMPAIGN)
        self.assertEqual(button.item.label, "Upload backstory")
        self.assertEqual(str(button.item.emoji), "📜")


class Pressing(unittest.IsolatedAsyncioTestCase):
    async def test_a_dm_gets_the_same_menu_as_the_names_page(self) -> None:
        it = _press(DM, found=campaign())
        await BackstoryButton(CAMPAIGN).callback(it)
        sent: Any = it.response.send_message
        sent.assert_awaited_once()
        self.assertIsInstance(sent.await_args.kwargs["view"], ui_bg.BackgroundMenu)
        self.assertTrue(sent.await_args.kwargs.get("ephemeral"))

    async def test_anyone_else_is_refused_privately_and_gets_no_menu(self) -> None:
        it = _press(PLAYER, found=campaign())
        await BackstoryButton(CAMPAIGN).callback(it)
        sent: Any = it.response.send_message
        sent.assert_awaited_once()
        self.assertNotIn("view", sent.await_args.kwargs)
        self.assertIn("Only", sent.await_args.args[0])

    async def test_a_server_manager_who_is_no_dm_is_refused_too(self) -> None:
        it = _press(PLAYER, found=campaign(), manager=True)
        await BackstoryButton(CAMPAIGN).callback(it)
        sent: Any = it.response.send_message
        self.assertNotIn("view", sent.await_args.kwargs)
        self.assertIn("Only a DM", sent.await_args.args[0])

    async def test_the_names_page_opens_the_same_menu(self) -> None:
        from dmbot.ui import name_lists

        opened = AsyncMock()
        real, ui_bg.open_background = ui_bg.open_background, opened
        try:
            view = name_lists.AddMany.__new__(name_lists.AddMany)
            view.campaign_id = CAMPAIGN
            it = _press(DM, found=campaign())
            await view._background(it)
        finally:
            ui_bg.open_background = real
        opened.assert_awaited_once_with(it, CAMPAIGN)


_ = discord
