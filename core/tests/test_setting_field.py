"""The "Campaign setting" line and ✅ Keep it on the ⚙️ Manage DMbot card (#4291)."""

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.dm_screen import setting_field
from dmbot.dm_screen.setting_field import KeepSettingButton, SettingField
from dmbot.dm_screen.settings import settings_text, settings_view
from dmbot.memory.models import MemoryRuleError
from tests.test_dm_screen_settings import CAMPAIGN, DM, PLAYER, campaign, press

PLACE = "b" * 32


class Words(unittest.TestCase):
    def test_a_kept_setting_is_just_named(self) -> None:
        text = settings_text(campaign(), None, DM, SettingField(PLACE, "Faerûn", False))
        self.assertIn("**Campaign setting:** Faerûn", text)
        self.assertNotIn("guessed", text)

    def test_a_guess_says_so_and_asks(self) -> None:
        text = settings_text(campaign(), None, DM, SettingField(PLACE, "Faerûn", True))
        self.assertIn("Faerûn (DMbot's guess from your adventure).", text)

    def test_no_setting_says_where_to_pick_one(self) -> None:
        text = settings_text(campaign(), None, DM, SettingField(None, None, False))
        self.assertIn("**Campaign setting:** not set yet.", text)
        self.assertIn("🌍 Make it the setting", text)

    def test_an_unreadable_setting_draws_no_line(self) -> None:
        self.assertNotIn("Campaign setting", settings_text(campaign(), None, DM, None))

    def test_players_never_see_the_line(self) -> None:
        field = SettingField(PLACE, "Faerûn", True)
        self.assertNotIn("Campaign setting", settings_text(campaign(), None, PLAYER, field))


class Buttons(unittest.TestCase):
    def keeps(self, viewer: int, field: SettingField | None) -> list[Any]:
        view = settings_view(campaign(), None, viewer, "", None, field)
        return [i for i in view.children if isinstance(i, KeepSettingButton)]

    def test_only_a_guess_gets_a_keep_button_and_only_for_a_dm(self) -> None:
        guess = SettingField(PLACE, "Faerûn", True)
        self.assertEqual(len(self.keeps(DM, guess)), 1)
        self.assertEqual(self.keeps(PLAYER, guess), [])
        self.assertEqual(self.keeps(DM, SettingField(PLACE, "Faerûn", False)), [])
        self.assertEqual(self.keeps(DM, SettingField(None, None, False)), [])
        self.assertEqual(self.keeps(DM, None), [])

    def test_the_card_still_fits(self) -> None:
        view = settings_view(campaign(), None, DM, "", None, SettingField(PLACE, "X", True))
        self.assertLessEqual(len(view.children), 25)
        rows: dict[int, int] = {}
        for item in view.children:
            row = item.row or 0
            rows[row] = rows.get(row, 0) + 1
        self.assertLessEqual(max(rows.values()), 5)

    def test_the_button_names_its_place_and_is_plain(self) -> None:
        button = KeepSettingButton(CAMPAIGN, PLACE)
        self.assertEqual(button.item.custom_id, f"dmbot:setkeep:{CAMPAIGN}:{PLACE}")
        self.assertEqual(button.item.label, "Keep it")


class Loading(unittest.IsolatedAsyncioTestCase):
    async def test_the_three_states(self) -> None:
        it = press(DM, found=campaign())
        it.client.memory.setting = AsyncMock(return_value=None)
        self.assertEqual(await setting_field.load(it, 1, CAMPAIGN), SettingField(None, None, False))
        place = MagicMock(id=PLACE, setting_guessed=True)
        place.name = "Faerûn"
        it.client.memory.setting = AsyncMock(return_value=place)
        self.assertEqual(
            await setting_field.load(it, 1, CAMPAIGN), SettingField(PLACE, "Faerûn", True)
        )

    async def test_a_failed_read_is_none_not_not_set(self) -> None:
        it = press(DM, found=campaign())
        it.client.memory.setting = AsyncMock(side_effect=RuntimeError("down"))
        self.assertIsNone(await setting_field.load(it, 1, CAMPAIGN))
        it.client.memory = None
        self.assertIsNone(await setting_field.load(it, 1, CAMPAIGN))


class Pressing(unittest.IsolatedAsyncioTestCase):
    async def test_a_dm_keeps_the_guess_once(self) -> None:
        it = press(DM, found=campaign())
        it.client.memory = MagicMock()
        it.client.memory.keep_setting = AsyncMock()
        it.client.memory.setting = AsyncMock(return_value=None)
        await KeepSettingButton(CAMPAIGN, PLACE).callback(it)
        it.client.memory.keep_setting.assert_awaited_once()
        self.assertEqual(it.client.memory.keep_setting.await_args.args[2], PLACE)
        self.assertEqual(it.client.memory.keep_setting.await_args.kwargs["source"], "dm")
        it.edit_original_response.assert_awaited_once()  # (the card is drawn again)

    async def test_anyone_else_changes_nothing(self) -> None:
        for manager in (False, True):
            it = press(PLAYER, found=campaign(), manager=manager)
            it.client.memory = MagicMock()
            it.client.memory.keep_setting = AsyncMock()
            await KeepSettingButton(CAMPAIGN, PLACE).callback(it)
            it.client.memory.keep_setting.assert_not_awaited()
            it.response.send_message.assert_awaited_once()

    async def test_a_guess_that_changed_since_is_not_an_error_for_the_dm(self) -> None:
        it = press(DM, found=campaign())
        it.client.memory = MagicMock()
        it.client.memory.keep_setting = AsyncMock(side_effect=MemoryRuleError("gone"))
        it.client.memory.setting = AsyncMock(return_value=None)
        await KeepSettingButton(CAMPAIGN, PLACE).callback(it)
        it.followup.send.assert_awaited_once()  # (told, and the card is drawn as it is now)
        it.edit_original_response.assert_awaited_once()

    async def test_a_store_failure_says_so(self) -> None:
        it = press(DM, found=campaign())
        it.client.memory = MagicMock()
        it.client.memory.keep_setting = AsyncMock(side_effect=RuntimeError("down"))
        await KeepSettingButton(CAMPAIGN, PLACE).callback(it)
        it.followup.send.assert_awaited_once()


class Template(unittest.IsolatedAsyncioTestCase):
    async def test_the_custom_id_round_trips(self) -> None:
        match = KeepSettingButton.__discord_ui_compiled_template__.fullmatch(
            f"dmbot:setkeep:{CAMPAIGN}:{PLACE}"
        )
        assert match is not None
        item = await KeepSettingButton.from_custom_id(MagicMock(), MagicMock(), match)
        self.assertEqual((item.campaign_id, item.entity_id), (CAMPAIGN, PLACE))
