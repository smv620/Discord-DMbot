"""⚙️ Settings on the DM screen (#515), with a stand-in Discord interaction."""

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.campaigns import Campaign, CampaignStore
from dmbot.campaigns.models import CampaignError
from dmbot.dm_screen import VisibilityButton
from dmbot.dm_screen.settings import (
    ONLY_DM,
    LevelButton,
    SettingsButton,
    settings_text,
    settings_view,
)

GUILD, DM, PLAYER = 1234, 7, 8
CAMPAIGN = "c" * 32


def campaign(level: str = "normal", name: str = "Frostmaiden") -> Campaign:
    return Campaign(
        id=CAMPAIGN,
        guild_id=GUILD,
        name=name,
        created_at=0,
        last_played_at=None,
        target_ruleset="2024",
        fallback_ruleset="2014",
        optional_rules_default=True,
        dm_user_ids=frozenset({DM}),
        dm_screen_channel_id=None,
        last_voice_channel_id=None,
        dm_screen_visibility="peek",
        dm_screen_level=level,
    )


def press(user: int, *, found: Campaign | None = None, manager: bool = False) -> Any:
    store = MagicMock(spec=CampaignStore)
    store.get = AsyncMock(return_value=found)
    it = MagicMock()
    it.client = MagicMock(campaigns=store)
    it.guild = MagicMock(id=GUILD)
    it.user = MagicMock(spec=discord.Member, id=user)
    it.user.guild_permissions = MagicMock(manage_guild=manager)
    it.response.send_message = AsyncMock()
    it.response.edit_message = AsyncMock()
    return it


class SettingsCardTest(unittest.TestCase):
    def test_plain_words(self) -> None:
        text = settings_text(campaign(name="Rime_of_*the*"))
        self.assertIn("Rime\\_of\\_\\*the\\*", text)  # a name can't break the formatting
        self.assertIn("**Normal** (recommended): how much DMbot says", text)
        self.assertIn("Warnings always show", text)
        self.assertIn("Players can choose to peek", text)
        self.assertIn("`/transcript`", text)
        self.assertNotIn("recommended", settings_text(campaign("quiet")))

    def test_buttons_fit_a_phone_and_survive_a_restart(self) -> None:
        view = settings_view(campaign("quiet"))
        items: list[Any] = list(view.children)
        rows = [(i.row, i.item.label) for i in items]
        self.assertEqual([r for r, _ in rows], [0, 0, 1, 1, 1])
        self.assertEqual([label for _, label in rows[:2]], ["✓ Quiet", "Normal"])  # no Chatty
        self.assertTrue(items[0].item.disabled)  # the current one
        for item in items:
            self.assertLessEqual(len(item.item.label), 25)
            template = (
                LevelButton if isinstance(item, LevelButton) else VisibilityButton
            ).__discord_ui_compiled_template__
            self.assertIsNotNone(template.fullmatch(str(item.item.custom_id)))
        self.assertIsNotNone(
            SettingsButton.__discord_ui_compiled_template__.fullmatch(
                str(SettingsButton(CAMPAIGN).item.custom_id)
            )
        )


class SettingsButtonsTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_dm_or_a_manager_opens_it(self) -> None:
        it = press(PLAYER, found=campaign())
        await SettingsButton(CAMPAIGN).callback(it)
        self.assertEqual(it.response.send_message.await_args.args[0], ONLY_DM)
        for who, manager in [(DM, False), (PLAYER, True)]:
            it = press(who, found=campaign(), manager=manager)
            await SettingsButton(CAMPAIGN).callback(it)
            kwargs = it.response.send_message.await_args.kwargs
            self.assertTrue(kwargs["ephemeral"])  # only they see it
            self.assertIsNotNone(kwargs["view"])

    async def test_another_servers_campaign_finds_nothing(self) -> None:
        it = press(DM, found=None)
        await SettingsButton(CAMPAIGN).callback(it)
        self.assertIn("can't find that campaign", it.response.send_message.await_args.args[0])
        it.client.campaigns.get.assert_awaited_once_with(GUILD, CAMPAIGN)

    async def test_a_tap_saves_and_shows_the_new_choice(self) -> None:
        it = press(DM, found=campaign())
        it.client.set_screen_level = AsyncMock(return_value=campaign("quiet"))
        await LevelButton(CAMPAIGN, "quiet").callback(it)
        it.client.set_screen_level.assert_awaited_once_with(GUILD, CAMPAIGN, "quiet")
        self.assertIn("**Quiet**", it.response.edit_message.await_args.kwargs["content"])

    async def test_a_refused_level_says_why(self) -> None:
        it = press(DM, found=campaign())
        it.client.set_screen_level = AsyncMock(side_effect=CampaignError("Please pick…"))
        await LevelButton(CAMPAIGN, "chatty").callback(it)
        self.assertEqual(it.response.send_message.await_args.args[0], "Please pick…")
        it.response.edit_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
