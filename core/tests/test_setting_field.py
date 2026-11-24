"""The "Campaign setting" line and ✅ Keep it on the ⚙️ Manage DMbot card (#4291)."""

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.dm_screen import setting_field
from dmbot.dm_screen.setting_field import (
    OWN,
    KeepSettingButton,
    OwnWorldForm,
    PickButton,
    SettingField,
    WorldMenu,
)
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
        self.assertIn(
            "Faerûn? DMbot guessed this from your adventure. Keep it, or pick another.", text
        )

    def test_no_setting_says_where_to_pick_one(self) -> None:
        text = settings_text(campaign(), None, DM, SettingField(None, None, False))
        self.assertIn("**Campaign setting:** not set. Pick one here", text)
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


class Picking(unittest.IsolatedAsyncioTestCase):
    def picks(self, viewer: int, field: SettingField | None) -> list[Any]:
        view = settings_view(campaign(), None, viewer, "", None, field)
        return [i for i in view.children if isinstance(i, PickButton)]

    def test_a_dm_may_pick_in_every_state_and_nobody_else(self) -> None:
        for field in (
            SettingField(PLACE, "Faerûn", True),
            SettingField(PLACE, "Faerûn", False),
            SettingField(None, None, False),
        ):
            self.assertEqual(len(self.picks(DM, field)), 1)
            self.assertEqual(self.picks(PLAYER, field), [])
        self.assertEqual(self.picks(DM, None), [])

    def test_no_setting_says_choose_a_world(self) -> None:
        item = self.picks(DM, SettingField(None, None, False))[0].item
        self.assertEqual(item.label, "Choose a world")
        self.assertEqual(str(item.emoji), "🌍")

    def test_a_kept_setting_shows_its_name(self) -> None:
        self.assertEqual(
            self.picks(DM, SettingField(PLACE, "Faerûn", False))[0].item.label, "Faerûn"
        )

    def test_a_long_name_is_cut_to_80_with_an_ellipsis(self) -> None:
        label = self.picks(DM, SettingField(PLACE, "a" * 120, False))[0].item.label
        self.assertEqual(len(label), 80)
        self.assertTrue(label.endswith("…"))

    def test_a_space_at_the_cut_is_kept_so_the_length_is_still_80(self) -> None:
        name = "a" * 78 + " " + "b" * 10
        label = self.picks(DM, SettingField(PLACE, name, False))[0].item.label
        self.assertEqual(label, "a" * 78 + " …")
        self.assertEqual(len(label), 80)

    def test_a_guessed_setting_is_not_shown_as_chosen(self) -> None:
        self.assertEqual(
            self.picks(DM, SettingField(PLACE, "Faerûn", True))[0].item.label, "Choose a world"
        )
        keep = [
            i
            for i in settings_view(
                campaign(), None, DM, "", None, SettingField(PLACE, "Faerûn", True)
            ).children
            if isinstance(i, KeepSettingButton)
        ]
        self.assertEqual(len(keep), 1)

    def test_a_card_drawn_with_the_picked_world_shows_its_name(self) -> None:
        # (a pick is drawn again with the new field, so the button shows the picked world)
        self.assertEqual(
            self.picks(DM, SettingField(PLACE, "Faerûn", False))[0].item.label, "Faerûn"
        )

    async def test_the_button_opens_the_list_and_refuses_a_non_dm(self) -> None:
        it = press(DM, found=campaign())
        it.response.edit_message = AsyncMock()
        await PickButton(CAMPAIGN).callback(it)
        view = it.response.edit_message.await_args.kwargs["view"]
        menu = next(i for i in view.children if isinstance(i, WorldMenu))
        values = [o.value for o in menu.item.options]
        self.assertEqual(values[-1], OWN)
        self.assertEqual(menu.item.options[-1].label, "Somewhere of my own")
        self.assertGreater(len(values), 3)
        other = press(PLAYER, found=campaign())
        other.response.edit_message = AsyncMock()
        await PickButton(CAMPAIGN).callback(other)
        other.response.edit_message.assert_not_awaited()

    async def pick(self, value: str, user: int = DM) -> Any:
        it = press(user, found=campaign())
        it.client.memory = MagicMock()
        it.client.memory.choose_setting = AsyncMock()
        it.client.memory.setting = AsyncMock(return_value=None)
        it.response.send_modal = AsyncMock()
        menu = WorldMenu(CAMPAIGN)
        object.__setattr__(menu.item, "_values", [value])
        await menu.callback(it)
        return it

    async def test_a_listed_world_is_chosen_by_its_place(self) -> None:
        it = await self.pick("forgotten_realms")
        args = it.client.memory.choose_setting.await_args
        self.assertEqual(args.args[2], "Faerûn")
        self.assertEqual(args.kwargs["source"], "dm")
        it.edit_original_response.assert_awaited_once()

    async def test_somewhere_of_my_own_opens_the_form(self) -> None:
        it = await self.pick(OWN)
        it.response.send_modal.assert_awaited_once()
        it.client.memory.choose_setting.assert_not_awaited()

    async def test_a_stale_value_and_a_non_dm_change_nothing(self) -> None:
        it = await self.pick("no_such_world")
        it.client.memory.choose_setting.assert_not_awaited()
        other = await self.pick("eberron", user=PLAYER)
        other.client.memory.choose_setting.assert_not_awaited()

    async def test_the_typed_name_is_chosen_and_a_refusal_is_said(self) -> None:
        form = OwnWorldForm(CAMPAIGN)
        form.name._value = "Golarion"
        it = press(DM, found=campaign())
        it.client.memory = MagicMock()
        it.client.memory.choose_setting = AsyncMock(side_effect=MemoryRuleError("Too small."))
        it.client.memory.setting = AsyncMock(return_value=None)
        await form.on_submit(it)
        self.assertEqual(it.client.memory.choose_setting.await_args.args[2], "Golarion")
        it.followup.send.assert_awaited_once()
        self.assertIn("Too small.", it.followup.send.await_args.args[0])


class Templates(unittest.IsolatedAsyncioTestCase):
    async def test_pick_button_and_menu_round_trip(self) -> None:
        for cls, custom in (
            (PickButton, f"dmbot:setpick:{CAMPAIGN}"),
            (WorldMenu, f"dmbot:setworld:{CAMPAIGN}"),
        ):
            match = cls.__discord_ui_compiled_template__.fullmatch(custom)
            assert match is not None
            item = await cls.from_custom_id(MagicMock(), MagicMock(), match)
            self.assertEqual(item.campaign_id, CAMPAIGN)

    async def test_a_non_dm_on_the_own_world_choice_gets_no_form(self) -> None:
        it = press(PLAYER, found=campaign())
        it.response.send_modal = AsyncMock()
        menu = WorldMenu(CAMPAIGN)
        object.__setattr__(menu.item, "_values", [OWN])
        await menu.callback(it)
        it.response.send_modal.assert_not_awaited()
        self.assertIn("DM", it.response.send_message.await_args.args[0])

    async def test_a_refusal_leaves_the_card_and_says_why(self) -> None:
        it = press(DM, found=campaign())
        it.client.memory = MagicMock()
        it.client.memory.choose_setting = AsyncMock(side_effect=MemoryRuleError("Too small."))
        menu = WorldMenu(CAMPAIGN)
        object.__setattr__(menu.item, "_values", ["eberron"])
        await menu.callback(it)
        it.edit_original_response.assert_not_awaited()
        self.assertIn("Too small.", it.followup.send.await_args.args[0])
