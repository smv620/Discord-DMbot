"""📇 Names on ⚙️ Manage DMbot, and the waiting reviews on the names home (#1986)."""

import dataclasses
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.campaigns import Campaign
from dmbot.dm_screen.dm_bot_buttons import role_view
from dmbot.dm_screen.dm_bot_panel import Role
from dmbot.dm_screen.names_button import NamesButton
from dmbot.dm_screen.settings import settings_view
from dmbot.ui import names as names_ui
from dmbot.ui.names import NamesHome, Reviews, review_line
from tests.test_dm_screen_settings import CAMPAIGN, DM, GUILD, PLAYER, campaign

DISCORD_MAX_BUTTONS = 25


def _labels(view: discord.ui.View) -> list[str]:
    items: list[Any] = list(view.children)  # (a DynamicItem wraps its button in .item)
    return [str(getattr(i, "label", None) or i.item.label) for i in items]


def _has_names(view: discord.ui.View) -> bool:
    return any(isinstance(i, NamesButton) for i in view.children)


class WhoGetsTheButton(unittest.TestCase):
    def test_no_role_has_it_on_the_main_menu_any_more(self) -> None:
        for role in Role:  # (it waits on ⚙️ Manage DMbot until the website has it, #2986)
            with self.subTest(role):
                self.assertFalse(_has_names(role_view(role, campaign())))

    def test_manage_dmbot_no_longer_has_it_the_website_has_names(self) -> None:
        for viewer in (DM, PLAYER, 999):  # (#2986: the Names page is on the website)
            with self.subTest(viewer):
                self.assertFalse(_has_names(settings_view(campaign(), None, viewer)))


class TheButtonCount(unittest.TestCase):
    def test_settings_never_passes_discords_25_for_any_viewer(self) -> None:
        owned = dataclasses.replace(campaign(), owner_user_id=DM)
        unowned = campaign()
        for label, camp in (("owner", owned), ("no owner", unowned)):
            for viewer in (DM, PLAYER, 999):
                with self.subTest(label, viewer=viewer):
                    view = settings_view(camp, None, viewer)
                    self.assertLessEqual(len(view.children), DISCORD_MAX_BUTTONS)

    def test_the_game_master_card_is_small(self) -> None:
        view = role_view(Role.GAME_MASTER, campaign())
        self.assertLessEqual(len(view.children), DISCORD_MAX_BUTTONS)


def _press(user: int, *, found: Campaign | None, manager: bool = False) -> Any:
    store = MagicMock()
    store.get = AsyncMock(return_value=found)
    it = MagicMock()
    it.client = MagicMock(campaigns=store, memory=MagicMock(), lookup=MagicMock())
    it.guild = MagicMock(spec=discord.Guild, id=GUILD)
    it.user = MagicMock(spec=discord.Member, id=user)
    it.user.guild_permissions = MagicMock(manage_guild=manager)
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.response.is_done = MagicMock(return_value=False)
    it.followup.send = AsyncMock()
    return it


class PressingIt(unittest.IsolatedAsyncioTestCase):
    async def test_a_dm_gets_the_names_home(self) -> None:
        shown = AsyncMock()
        real, names_ui.show_home = names_ui.show_home, shown
        try:
            it = _press(DM, found=campaign())
            await NamesButton(CAMPAIGN).callback(it)
        finally:
            names_ui.show_home = real
        shown.assert_awaited_once_with(it, CAMPAIGN)

    async def test_anyone_else_is_refused_privately(self) -> None:
        it = _press(PLAYER, found=campaign())
        await NamesButton(CAMPAIGN).callback(it)
        sent = it.response.send_message
        sent.assert_awaited_once()
        self.assertTrue(sent.await_args.kwargs.get("ephemeral"))
        self.assertNotIn("view", sent.await_args.kwargs)


class WaitingReviews(unittest.TestCase):
    def test_nothing_waiting_says_nothing(self) -> None:
        self.assertIsNone(review_line(Reviews()))

    def test_each_kind_shows_with_its_count(self) -> None:
        line = review_line(Reviews(heard=1, people=2, claims=3)) or ""
        self.assertIn("1 name to double-check", line)
        self.assertIn("2 questions about who is who", line)
        self.assertIn("3 things said to check", line)

    def test_only_what_waits_is_named(self) -> None:
        line = review_line(Reviews(people=1)) or ""
        self.assertIn("1 question about who is who", line)
        self.assertNotIn("word", line)

    def test_a_button_for_each_kind_that_waits_and_none_at_zero(self) -> None:
        none = NamesHome(CAMPAIGN, 0, None, Reviews())
        self.assertFalse([x for x in _labels(none) if "Check" in x and "new names" not in x])
        some = NamesHome(CAMPAIGN, 0, None, Reviews(heard=2, people=0, claims=4))
        labels = _labels(some)
        self.assertIn("❓ Check names (2)", labels)
        self.assertIn("🗣️ Check what was said (4)", labels)
        self.assertFalse([x for x in labels if "who is who" in x])

    def test_a_huge_count_keeps_the_label_short(self) -> None:
        labels = _labels(NamesHome(CAMPAIGN, 0, None, Reviews(claims=1234)))
        self.assertIn("🗣️ Check what was said (99+)", labels)

    def test_the_home_card_stays_within_discords_limits(self) -> None:
        view = NamesHome(
            CAMPAIGN,
            3,
            [("e1", "Belleros", "character")],
            Reviews(heard=1, people=1, claims=1),
        )
        self.assertLessEqual(len(view.children), DISCORD_MAX_BUTTONS)
