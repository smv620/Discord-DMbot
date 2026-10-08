"""⚙️ Settings on the DM screen (#515), with a stand-in Discord interaction."""

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.campaigns import Campaign, CampaignStore
from dmbot.campaigns.models import CampaignError
from dmbot.dm_screen import VisibilityButton, card_view, messages
from dmbot.dm_screen import buttons as buttons_module
from dmbot.dm_screen import settings as settings_module
from dmbot.dm_screen.settings import (
    FAILED,
    GONE,
    LOAD_FAILED,
    ONLY_DM,
    SAVED,
    LevelButton,
    SettingsButton,
    SettingsVisibilityButton,
    settings_text,
    settings_view,
)

GUILD, DM, PLAYER = 1234, 7, 8
CAMPAIGN = "c" * 32


def campaign(level: str = "normal", name: str = "Frostmaiden", vis: str = "peek") -> Campaign:
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
        dm_screen_visibility=vis,
        dm_screen_level=level,
    )


def press(user: int, *, found: Campaign | None = None, manager: bool = False) -> Any:
    store = MagicMock(spec=CampaignStore)
    store.get = AsyncMock(return_value=found)
    store.open_offer = AsyncMock(return_value=None)  # no hand-over offer waiting
    it = MagicMock()
    it.client = MagicMock(campaigns=store, set_screen_level=AsyncMock())
    it.guild = MagicMock(spec=discord.Guild, id=GUILD)
    it.user = MagicMock(spec=discord.Member, id=user)
    it.user.guild_permissions = MagicMock(manage_guild=manager)
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.followup.send = AsyncMock()
    it.edit_original_response = AsyncMock()
    return it


class SettingsCardTest(unittest.TestCase):
    def test_plain_words(self) -> None:
        text = settings_text(campaign(name="Rime_of_*the*"))
        self.assertIn("Rime\\_of\\_\\*the\\*", text)  # a name can't break the formatting
        self.assertIn("• **How much DMbot says:** Normal (recommended). Asks about", text)
        self.assertIn("Warnings always show", text)
        self.assertIn("• **Who can see the DM screen:** The DM. Players can choose", text)
        self.assertIn("`/transcript`", text)
        quiet = settings_text(campaign("quiet"))
        self.assertIn("Quiet. Only what you ask for, so fewer misheard names get fixed.", quiet)
        self.assertNotIn("recommended", quiet)

    def test_buttons_fit_a_phone_and_survive_a_restart(self) -> None:
        view = settings_view(campaign("quiet", vis="private"))
        items: list[Any] = list(view.children)
        self.assertEqual([i.row for i in items], [0, 0, 1, 1, 1, 2])  # 🤝 Hand over (#437)
        labels = [i.item.label for i in items]
        self.assertEqual(labels[:2], ["✓ Quiet", "Normal"])  # no Chatty
        self.assertEqual(labels[2], "✓ Only the DM")  # the current one, the same way
        self.assertEqual([i.item.disabled for i in items], [True, False, True, False, False, False])
        for item in items:
            self.assertLessEqual(len(item.item.label), 25)
            template = type(item).__discord_ui_compiled_template__
            self.assertIsNotNone(template.fullmatch(str(item.item.custom_id)))
        self.assertLessEqual(len("✓ Everyone in the server"), 25)
        button = SettingsButton(CAMPAIGN)
        self.assertIsNotNone(
            SettingsButton.__discord_ui_compiled_template__.fullmatch(str(button.item.custom_id))
        )

    def test_the_help_card_has_settings_between_sessions(self) -> None:
        ids = [str(i.item.custom_id) for i in card_view(campaign()).children]  # type: ignore[attr-defined]
        self.assertIn(f"dmbot:settings:{CAMPAIGN}", ids)
        self.assertLessEqual(len(ids), 5)  # one row


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
        self.assertEqual(it.response.send_message.await_args.args[0], GONE)
        it.client.campaigns.get.assert_awaited_once_with(GUILD, CAMPAIGN)

    async def test_a_database_error_says_try_again(self) -> None:
        it = press(DM)
        it.client.campaigns.get.side_effect = RuntimeError("database down")
        with self.assertLogs("dmbot.dm_screen.buttons", "ERROR"):  # the one shared check
            await SettingsButton(CAMPAIGN).callback(it)
        self.assertEqual(it.response.send_message.await_args.args[0], LOAD_FAILED)  # not "save"

    async def test_a_card_too_old_to_redraw_still_says_saved(self) -> None:
        it = press(DM, found=campaign())
        it.client.set_screen_level.return_value = campaign("quiet")
        it.edit_original_response.side_effect = discord.NotFound(MagicMock(status=404), "gone")
        await LevelButton(CAMPAIGN, "quiet").callback(it)
        self.assertEqual(it.followup.send.await_args.args[0], SAVED)

    async def test_a_level_tap_saves_and_redraws_the_card(self) -> None:
        for who, manager in [(DM, False), (PLAYER, True)]:
            it = press(who, found=campaign(), manager=manager)
            it.client.set_screen_level.return_value = campaign("quiet")
            await LevelButton(CAMPAIGN, "quiet").callback(it)
            it.response.defer.assert_awaited_once()  # saving may take a moment
            it.client.set_screen_level.assert_awaited_once_with(
                GUILD,
                CAMPAIGN,
                "quiet",
                was="normal",  # the level the DM saw: noted if new
            )
            content = it.edit_original_response.await_args.kwargs["content"]
            self.assertIn("**How much DMbot says:** Quiet.", content)

    async def test_a_player_cant_change_the_level(self) -> None:
        it = press(PLAYER, found=campaign())
        await LevelButton(CAMPAIGN, "quiet").callback(it)
        self.assertEqual(it.response.send_message.await_args.args[0], ONLY_DM)
        it.client.set_screen_level.assert_not_awaited()
        it.edit_original_response.assert_not_awaited()

    async def test_a_failed_save_says_why_and_leaves_the_card(self) -> None:
        it = press(DM, found=campaign())
        it.client.set_screen_level.side_effect = CampaignError("Please pick…")
        await LevelButton(CAMPAIGN, "quiet").callback(it)
        self.assertEqual(it.followup.send.await_args.args[0], "Please pick…")
        it = press(DM, found=campaign())
        it.client.set_screen_level.side_effect = RuntimeError("database down")
        with self.assertLogs("dmbot.dm_screen.settings", "ERROR"):
            await LevelButton(CAMPAIGN, "quiet").callback(it)
        self.assertEqual(it.followup.send.await_args.args[0], FAILED)
        it.edit_original_response.assert_not_awaited()

    async def test_a_visibility_tap_saves_and_redraws_the_card(self) -> None:
        it = press(DM)
        allowed = (campaign(), it.guild, it.client.campaigns)
        saved = campaign(vis="private")
        with (
            patch.object(settings_module, "may_change_screen", AsyncMock(return_value=allowed)),
            patch.object(settings_module, "save_visibility", AsyncMock(return_value=saved)) as save,
        ):
            await SettingsVisibilityButton(CAMPAIGN, "private").callback(it)
        save.assert_awaited_once_with(it, *allowed, "private", quiet=True)
        it.response.defer.assert_awaited_once()
        kwargs = it.edit_original_response.await_args.kwargs
        self.assertIn("**Who can see the DM screen:** Only the DM.", kwargs["content"])
        labels = [i.item.label for i in kwargs["view"].children]
        self.assertIn("✓ Only the DM", labels)  # the card shows what's saved now

    async def test_a_refused_visibility_tap_leaves_the_card(self) -> None:
        it = press(PLAYER)
        with patch.object(settings_module, "may_change_screen", AsyncMock(return_value=None)):
            await SettingsVisibilityButton(CAMPAIGN, "open").callback(it)
        it.response.defer.assert_not_awaited()
        it.edit_original_response.assert_not_awaited()


class SettingsWordsTest(unittest.IsolatedAsyncioTestCase):
    """#553: one set of words on the card, true on every path."""

    def test_the_card_says_what_cant_be_changed_and_what_follows(self) -> None:
        text = settings_text(campaign())
        self.assertIn("`/transcript`. (This can't be changed.)", text)
        self.assertIn("If DMbot is listening now, it follows the change from now on.", text)
        self.assertNotIn("next line", text)
        self.assertEqual(LOAD_FAILED, "Sorry, something went wrong. Please try again.")

    def test_the_level_buttons_follow_the_levels_offered(self) -> None:
        from dmbot.campaigns.models import DM_SCREEN_LEVELS_OFFERED

        template = LevelButton.__discord_ui_compiled_template__
        for level in DM_SCREEN_LEVELS_OFFERED:
            self.assertIsNotNone(template.fullmatch(f"dmbot:level:{CAMPAIGN}:{level}"))
        self.assertIsNone(template.fullmatch(f"dmbot:level:{CAMPAIGN}:chatty"))

    def test_a_level_change_note_for_the_dm_screen(self) -> None:
        self.assertEqual(messages.level_changed("quiet"), "🔇 How much DMbot says: Quiet.")
        self.assertEqual(messages.level_changed("normal"), "🔔 How much DMbot says: Normal.")

    async def test_a_player_on_the_visibility_row_gets_the_cards_words(self) -> None:
        it = press(PLAYER, found=campaign())
        await SettingsVisibilityButton(CAMPAIGN, "open").callback(it)
        self.assertEqual(it.response.send_message.await_args.args[0], ONLY_DM)
        it.response.defer.assert_not_awaited()

    async def visibility_tap(self, was: str, to: str, warning: str | None) -> Any:
        """An unpatched settings-card visibility tap, with only the saving mocked."""
        it = press(DM, found=campaign(vis=was))
        result = MagicMock(campaign=campaign(vis=to), warning=warning)
        result.channel.send = AsyncMock()
        it.client.after_screen_change = AsyncMock()
        it.order = MagicMock()  # the calls, in order
        it.order.attach_mock(it.response.defer, "defer")
        it.order.attach_mock(it.followup.send, "send")
        it.order.attach_mock(it.edit_original_response, "edit")
        with patch.object(buttons_module, "setup_dm_screen", AsyncMock(return_value=result)):
            await SettingsVisibilityButton(CAMPAIGN, to).callback(it)
        return it

    async def test_a_plain_visibility_change_is_one_redraw_and_no_pop_up(self) -> None:
        it = await self.visibility_tap("peek", "open", None)
        it.response.defer.assert_awaited_once()
        it.followup.send.assert_not_awaited()  # the redrawn card is the confirmation
        it.edit_original_response.assert_awaited_once()

    async def test_a_visibility_change_with_news_still_says_it(self) -> None:
        it = await self.visibility_tap("peek", "private", None)
        self.assertIn("Players who were peeking", it.followup.send.await_args.args[0])
        # One tap, in this order: defer, the news, then the card redrawn.
        self.assertEqual([c[0] for c in it.order.mock_calls], ["defer", "send", "edit"])
        it = await self.visibility_tap("private", "open", "⚠️ A player could already see it.")
        self.assertIn("⚠️ A player could already see it.", it.followup.send.await_args.args[0])
        it.edit_original_response.assert_awaited_once()


class HelpCardVisibilityTest(unittest.IsolatedAsyncioTestCase):
    """The help card's own buttons, after sharing their check and save with Settings."""

    async def test_a_player_is_refused_without_a_defer(self) -> None:
        it = press(PLAYER, found=campaign())
        await VisibilityButton(CAMPAIGN, "open").callback(it)
        self.assertEqual(it.response.send_message.await_args.args[0], messages.NOT_THE_DM)
        it.response.defer.assert_not_awaited()

    async def test_the_dm_gets_done(self) -> None:
        it = press(DM, found=campaign())
        result = MagicMock(campaign=campaign(vis="open"), warning=None)
        it.client.after_screen_change = AsyncMock()
        with patch.object(buttons_module, "setup_dm_screen", AsyncMock(return_value=result)):
            await VisibilityButton(CAMPAIGN, "open").callback(it)
        it.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        self.assertIn("Done.", it.followup.send.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
