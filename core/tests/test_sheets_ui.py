"""📜 My character sheet and the DM's sheet controls (#723, part A second half), with
fakes: no Discord, no database, no D&D Beyond. The store's rules are tested with a
database in test_sheet_store."""

from __future__ import annotations

import re
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.consent_dm import stop_view
from dmbot.memory import sheets
from dmbot.memory.sheet_store import CharacterSheet, PlayerCharacter, SheetStore
from dmbot.ui import sheets as ui
from tests.test_sheets import answer

GUILD, PLAYER = 111, 9
CAMPAIGN = "c" * 32
ENTITY = "e" * 32
TESTA = PlayerCharacter(CAMPAIGN, "Frost*maiden", ENTITY, "Testa")
URL = sheets.sheet_url(42)


def sheet(*, url: str | None = URL, read: bool = True) -> CharacterSheet:
    snapshot = sheets.parse(answer()) if read else None
    return CharacterSheet(ENTITY, "Testa", PLAYER, url, snapshot, 1_700_000_000 if read else None)


def interaction(found: list[PlayerCharacter], current: CharacterSheet | None = None) -> Any:
    store = MagicMock(spec=SheetStore)
    store.characters_of = AsyncMock(return_value=found)
    store.sheet = AsyncMock(return_value=current)
    store.link = AsyncMock()
    store.save = AsyncMock(return_value=True)
    store.unlink = AsyncMock(return_value=True)
    store.sheets = AsyncMock(return_value=[current] if current else [])
    it = MagicMock()
    it.user = MagicMock(id=PLAYER)
    it.guild = None  # in a private message
    it.type = discord.InteractionType.component
    it.client = MagicMock(sheets=store)
    done = {"yes": False}

    async def answered(*_: Any, **__: Any) -> None:
        done["yes"] = True

    for name in ("send_message", "edit_message", "defer", "send_modal"):
        setattr(it.response, name, AsyncMock(side_effect=answered))
    it.response.is_done = MagicMock(side_effect=lambda: done["yes"])
    it.followup.send = AsyncMock()
    it.edit_original_response = AsyncMock()
    return it


def said(it: Any) -> str:
    call = it.followup.send.await_args or it.response.send_message.await_args
    return str(call.args[0])


def sent_view(it: Any) -> Any:
    call = it.followup.send.await_args or it.response.send_message.await_args
    return call.kwargs.get("view")


class Words(unittest.TestCase):
    def test_what_the_panel_says(self) -> None:
        self.assertIn("no sheet yet", ui.sheet_status(None, link=True))
        linked = ui.sheet_status(sheet(), link=True)
        self.assertIn(f"[D&D Beyond](<{URL}>)", linked)
        self.assertIn("read <t:1700000000:R>", linked)
        self.assertIn("Test Species · Test Class 3 / Second Class 2", linked)
        self.assertIn("not read yet", ui.sheet_status(sheet(read=False), link=True))
        self.assertIn(
            "told DMbot about this character", ui.sheet_status(sheet(url=None), link=True)
        )

    def test_the_link_only_for_whoever_may_see_it(self) -> None:
        line = ui.card_line(sheet(), link=False)
        assert line is not None
        self.assertNotIn("dndbeyond.com", line)
        self.assertIn(URL, ui.card_line(sheet(), link=True) or "")
        self.assertIsNone(ui.card_line(None, link=True))

    def test_the_consent_messages_carry_the_button(self) -> None:
        for campaign in (None, CAMPAIGN):
            view = stop_view(GUILD, sheet=True, campaign_id=campaign)
            ids = [getattr(i, "custom_id", "") for i in view.children]
            sheet_id = next(i for i in ids if i.startswith("dmbot:sheet:"))
            match = re.fullmatch(ui.MySheetButton.__discord_ui_compiled_template__, sheet_id)
            self.assertIsNotNone(match, sheet_id)
        self.assertEqual(len(stop_view(GUILD).children), 1)  # just 🛑 where asked


class Button(unittest.IsolatedAsyncioTestCase):
    async def test_no_character_yet_says_who_adds_it(self) -> None:
        it = interaction([])
        await ui.MySheetButton(GUILD).callback(it)
        self.assertEqual(said(it), ui.NO_CHARACTER)

    async def test_one_character_opens_its_panel(self) -> None:
        it = interaction([TESTA])
        await ui.MySheetButton(GUILD, CAMPAIGN).callback(it)
        it.client.sheets.characters_of.assert_any_await(GUILD, PLAYER, CAMPAIGN)
        self.assertIn("**Testa** (Frost\\*maiden): no sheet yet", said(it))
        labels = [b.label for b in sent_view(it).children]
        self.assertEqual(labels, [ui.LINK_LABEL, ui.TELL_LABEL])  # nothing to unlink yet

    async def test_several_characters_ask_which(self) -> None:
        other = PlayerCharacter("d" * 32, "Second", "f" * 32, "Testa Two")
        it = interaction([TESTA, other])
        await ui.MySheetButton(GUILD).callback(it)
        self.assertEqual(said(it), ui.PICK)
        self.assertIsInstance(sent_view(it), ui.CharacterChoice)

    async def test_a_linked_one_can_be_unlinked(self) -> None:
        it = interaction([TESTA], sheet())
        await ui.MySheetButton(GUILD).callback(it)
        panel = sent_view(it)
        self.assertEqual(panel.children[-1].label, ui.FORGET_LABEL)
        press = interaction([TESTA])
        press.client = it.client
        with patch.object(ui, "_bot", return_value=MagicMock(sheets=it.client.sheets)) as bot:
            await panel._forget(press)
        it.client.sheets.unlink.assert_awaited_once_with(GUILD, CAMPAIGN, ENTITY, player=PLAYER)
        bot.return_value.sheets_changed.assert_called_once_with(GUILD, CAMPAIGN)


class Guards(unittest.IsolatedAsyncioTestCase):
    async def test_someone_who_left_the_server_can_change_nothing(self) -> None:
        it = interaction([TESTA])
        it.client.get_guild.return_value.get_member.return_value = None
        it.client.get_guild.return_value.fetch_member = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "gone")
        )
        await ui.MySheetButton(GUILD).callback(it)
        self.assertEqual(said(it), ui.NOT_A_MEMBER)
        it.client.sheets.characters_of.assert_not_awaited()

    async def test_a_character_given_to_someone_else_meanwhile(self) -> None:
        it = interaction([])  # no longer theirs when the panel is drawn
        await ui.show_panel(it, GUILD, TESTA)
        self.assertEqual(said(it), ui.NOT_YOURS)
        it.client.sheets.sheet.assert_not_awaited()


class LinkAndRead(unittest.IsolatedAsyncioTestCase):
    async def test_a_link_changed_while_read(self) -> None:
        bot = MagicMock(sheets=interaction([TESTA]).client.sheets)
        bot.sheets.save = AsyncMock(return_value=False)
        with patch.object(sheets, "fetch", AsyncMock(return_value=sheets.parse(answer()))):
            said_now = await ui.link_and_read(bot, GUILD, CAMPAIGN, ENTITY, 42)
        self.assertEqual(said_now, ui.LINK_CHANGED)
        bot.sheets_changed.assert_called_once_with(GUILD, CAMPAIGN)

    async def test_a_failed_read_still_updates_a_running_session(self) -> None:
        bot = MagicMock(sheets=interaction([TESTA]).client.sheets)
        with patch.object(sheets, "fetch", AsyncMock(side_effect=sheets.SheetError("down"))):
            said_now = await ui.link_and_read(bot, GUILD, CAMPAIGN, ENTITY, 42)
        self.assertEqual(said_now, ui.LINKED_UNREAD)
        bot.sheets_changed.assert_called_once_with(GUILD, CAMPAIGN)  # the old names went

    async def test_the_dms_bad_link_keeps_the_character(self) -> None:
        it = interaction([TESTA])
        self.assertEqual(await ui.dm_link(it, GUILD, CAMPAIGN, ENTITY, "my sheet"), ui.DM_BAD_LINK)
        it.client.sheets.link.assert_not_awaited()

    async def test_the_dms_link_failing_after_the_save_never_raises(self) -> None:
        it = interaction([TESTA])
        it.client.sheets.link = AsyncMock(side_effect=RuntimeError("database down"))
        with self.assertLogs("dmbot.ui.sheets", "ERROR"):
            said_now = await ui.dm_link(it, GUILD, CAMPAIGN, ENTITY, URL)
        self.assertEqual(said_now, ui.DM_NOT_LINKED)


class Cooldowns(unittest.TestCase):
    def test_old_entries_are_dropped(self) -> None:
        last: dict[Any, float] = {n: -1000.0 for n in range(ui._COOLDOWNS_KEPT)}
        self.assertFalse(ui._too_soon(last, "new", 20))
        self.assertEqual(list(last), ["new"])
        self.assertTrue(ui._too_soon(last, "new", 20))


class Linking(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        ui._last_link.clear()

    def form(self, link: str) -> ui.LinkForm:
        form = ui.LinkForm(GUILD, TESTA)
        form.link._value = link  # what the player typed
        return form

    async def test_not_a_link(self) -> None:
        it = interaction([TESTA])
        await self.form("my sheet").on_submit(it)
        self.assertEqual(said(it), sheets.NOT_A_LINK)
        it.client.sheets.link.assert_not_awaited()

    async def test_a_public_sheet_is_linked_and_read(self) -> None:
        it = interaction([TESTA])
        it.client.sheets_changed = MagicMock()
        with patch.object(sheets, "fetch", AsyncMock(return_value=sheets.parse(answer()))):
            await self.form("https://www.dndbeyond.com/characters/42/abc").on_submit(it)
        it.client.sheets.link.assert_awaited_once_with(GUILD, CAMPAIGN, ENTITY, 42, player=PLAYER)
        self.assertIn("✅ Linked. DMbot read **Testa Placeholder**'s sheet", said(it))
        it.client.sheets_changed.assert_called_once_with(GUILD, CAMPAIGN)

    async def test_a_private_sheet_says_how_to_make_it_public(self) -> None:
        it = interaction([TESTA])
        refused = sheets.SheetError(sheets.NOT_PUBLIC, refused=True)
        with patch.object(sheets, "fetch", AsyncMock(side_effect=refused)):
            await self.form(URL).on_submit(it)
        self.assertIn(sheets.NOT_PUBLIC, said(it))

    async def test_pressing_again_at_once_waits(self) -> None:
        with patch.object(sheets, "fetch", AsyncMock(return_value=sheets.parse(answer()))):
            await self.form(URL).on_submit(interaction([TESTA]))
            again = interaction([TESTA])
            await self.form(URL).on_submit(again)
        self.assertEqual(said(again), ui.WAIT)


class Typing(unittest.IsolatedAsyncioTestCase):
    def form(self, level: str, names: str = "Misty Step, Shield") -> ui.TypedForm:
        form = ui.TypedForm(GUILD, TESTA)
        form.class_name._value, form.level._value = "Wizard", level
        form.species._value, form.names._value = "Elf", names
        return form

    async def test_a_level_must_be_1_to_20(self) -> None:
        for level in ("0", "21", "x"):
            it = interaction([TESTA])
            await self.form(level).on_submit(it)
            self.assertIn("1 to 20", said(it))
            it.client.sheets.save.assert_not_awaited()

    async def test_saved_as_a_typed_snapshot(self) -> None:
        it = interaction([TESTA])
        it.client.sheets_changed = MagicMock()
        await self.form("5").on_submit(it)
        snapshot = it.client.sheets.save.await_args.args[3]
        self.assertEqual(snapshot["source"], "typed")
        self.assertEqual(sheets.who(snapshot), "Elf · Wizard 5")
        self.assertEqual(snapshot["features"], ["Misty Step", "Shield"])
        self.assertIn("The transcript will spell those names right", said(it))

    async def test_names_left_out_are_counted(self) -> None:
        it = interaction([TESTA])
        it.client.sheets_changed = MagicMock()
        await self.form("5", ", ".join(f"Name {i}" for i in range(25))).on_submit(it)
        self.assertIn("5 left out", said(it))


class Refreshing(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        ui._last_refresh.clear()

    async def test_at_most_once_a_minute_per_campaign(self) -> None:
        it = interaction([TESTA], sheet())
        with patch("dmbot.memory.sheet_refresh.refresh", AsyncMock(return_value=[sheet()])):
            await ui.refresh_all(it, CAMPAIGN, GUILD)
            again = interaction([TESTA], sheet())
            await ui.refresh_all(again, CAMPAIGN, GUILD)
        self.assertEqual(said(again), ui.REFRESH_WAIT)

    async def test_says_which_couldnt_be_read(self) -> None:
        it = interaction([TESTA], sheet())
        it.client.sheets_changed = MagicMock()
        old = sheet()  # read long ago: this refresh couldn't read it
        with patch("dmbot.memory.sheet_refresh.refresh", AsyncMock(return_value=[old])):
            await ui.refresh_all(it, CAMPAIGN, GUILD)
        self.assertIn("Read 0 of 1 sheets. Couldn't read: **Testa**", said(it))
        it.client.sheets_changed.assert_called_once_with(GUILD, CAMPAIGN)

    async def test_nothing_linked(self) -> None:
        it = interaction([TESTA], sheet(url=None))
        await ui.refresh_all(it, CAMPAIGN, GUILD)
        self.assertEqual(said(it), ui.NO_SHEETS)


if __name__ == "__main__":
    unittest.main()
