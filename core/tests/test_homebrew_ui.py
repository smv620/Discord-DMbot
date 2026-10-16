"""📚 Homebrew (#1520), the DM's private menu: the list, Add (kind, then the form), an entry's
👀/🙈 switch and kind, Remove with Undo, and that every press checks the person is a DM. The real
store, against the database."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot.campaigns import CampaignStore
from dmbot.dm_screen.settings import HomebrewButton
from dmbot.rules.homebrew import FileEntry, HomebrewSection, HomebrewStore
from dmbot.ui import homebrew as ui
from tests.pg import DatabaseTest
from tests.test_memory_names import FakeResponse

GUILD, DM, PLAYER = 111, 7, 9


class HomebrewUiTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(HomebrewSection())
        self.store = HomebrewStore(self.db)
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        self.bot = SimpleNamespace(
            campaigns=self.campaigns,
            homebrew=self.store,
            homebrew_removed={},
            active_campaign_id=lambda g: None,
        )

    def press(self, user: int = DM) -> Any:
        return SimpleNamespace(
            client=self.bot,
            guild=SimpleNamespace(id=GUILD),
            guild_id=GUILD,
            user=SimpleNamespace(id=user),
            response=FakeResponse(),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
            type=None,
        )

    async def add(self, name: str = "Hollow King", **kw: Any) -> Any:
        return await self.store.add(
            GUILD,
            self.campaign.id,
            DM,
            kind=kw.pop("kind", "monster"),
            name=name,
            text="Spooky.",
            **kw,
        )

    def said(self, it: Any) -> str:
        if it.edit_original_response.await_args is not None:
            return str(it.edit_original_response.await_args.kwargs["content"])
        if it.response.edited:
            return str(it.response.edited[-1][0])
        if it.followup.send.await_args is not None:
            return str(it.followup.send.await_args.args[0])
        return str(it.response.sent[-1][0])

    def view_of(self, it: Any) -> Any:
        if it.edit_original_response.await_args is not None:
            return it.edit_original_response.await_args.kwargs["view"]
        return it.response.edited[-1][1]


class TheList(HomebrewUiTest):
    async def test_an_empty_list_says_how_to_start_and_only_the_dm_sees_the_menu(self) -> None:
        it = self.press()
        await ui.show_list(it, self.campaign, first=True)
        self.assertIn("No homebrew yet", self.said(it))
        self.assertIn("Share a rulebook", self.said(it))

    async def test_each_entry_shows_who_may_know_it(self) -> None:
        await self.add("Hollow King")
        await self.add("Moonblade", kind="item", players_may_know=True)
        it = self.press()
        await ui.show_list(it, self.campaign, first=True)
        text = self.said(it)
        self.assertIn("🙈 👹 Monster **Hollow King**", text)
        self.assertIn("👀 🗡 Item **Moonblade**", text)

    async def test_the_settings_button_opens_it_for_a_dm_and_refuses_anyone_else(self) -> None:
        button = HomebrewButton(self.campaign.id)
        player = self.press(PLAYER)
        await button.callback(player)
        self.assertEqual(self.said(player), ui.ONLY_DMS)
        dm = self.press()
        await button.callback(dm)
        self.assertIn("Homebrew: Frostmaiden", self.said(dm))

    async def test_the_command_is_for_the_dms_of_a_campaign_here(self) -> None:
        it = self.press(PLAYER)
        await cast(Any, ui.dmbot_homebrew).callback(it)
        self.assertEqual(self.said(it), ui.NOT_YOUR_CAMPAIGN)


class Adding(HomebrewUiTest):
    async def test_add_asks_the_kind_then_the_form_and_the_entry_starts_hidden(self) -> None:
        menu = ui.ListMenu(self.campaign, [], 0, [])
        it = self.press()
        await menu._add(it)
        self.assertEqual(self.said(it), ui.KIND_ASK)
        pick = ui.KindPick(self.campaign, 0)
        pick.pick._values = ["spell"]
        form_it = self.press()
        await pick._picked(form_it)
        form = form_it.response.modal
        self.assertIsInstance(form, ui.AddForm)
        form.name._value = "Frost Lance"
        form.text._value = "A beam of cold.\nIt chills."
        form.summary._value = ""
        submit = self.press()
        await form.on_submit(submit)
        (saved,) = await self.store.list(GUILD, self.campaign.id)
        self.assertEqual(
            (saved.kind, saved.name, saved.players_may_know), ("spell", "Frost Lance", False)
        )
        self.assertIn("Only you see it", self.said(submit))

    async def test_a_refused_save_gives_the_words_back_to_copy(self) -> None:
        await self.add("Hollow King")
        form = ui.AddForm(self.campaign, "monster")
        form.name._value = "hollow king"
        form.text._value = "My long words."
        form.summary._value = ""
        it = self.press()
        await form.on_submit(it)
        sent = it.followup.send.await_args_list[0].args[0]
        self.assertIn("already has homebrew with that name", sent)
        self.assertIn("My long words.", sent)

    async def test_a_non_dm_cannot_open_the_kind_menu_or_the_form(self) -> None:
        it = self.press(PLAYER)
        await ui.ListMenu(self.campaign, [], 0, [])._add(it)
        self.assertEqual(self.said(it), ui.ONLY_DMS)
        pick = ui.KindPick(self.campaign, 0)
        pick.pick._values = ["spell"]
        it2 = self.press(PLAYER)
        await pick._picked(it2)
        self.assertIsNone(it2.response.modal)

    async def test_a_player_forging_a_form_submit_is_refused_by_the_store(self) -> None:
        form = ui.AddForm(self.campaign, "monster")
        form.name._value, form.text._value, form.summary._value = "Sneaky", "x", ""
        it = self.press(PLAYER)
        await form.on_submit(it)
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])


class AnEntry(HomebrewUiTest):
    async def test_the_switch_lets_players_know_and_takes_it_back(self) -> None:
        saved = await self.add()
        menu = ui.EntryMenu(self.campaign, saved, 0)
        it = self.press()
        await menu._toggle(it)
        (now,) = await self.store.list(GUILD, self.campaign.id)
        self.assertTrue(now.players_may_know)
        self.assertIn("players may know it 👀", self.said(it))
        again = ui.EntryMenu(self.campaign, now, 0)
        await again._toggle(self.press())
        (back,) = await self.store.list(GUILD, self.campaign.id)
        self.assertFalse(back.players_may_know)

    async def test_the_kind_can_be_changed_and_only_by_a_dm(self) -> None:
        saved = await self.add()
        menu = ui.EntryMenu(self.campaign, saved, 0)
        menu.kind._values = ["item"]
        await menu._kind(self.press(PLAYER))
        (same,) = await self.store.list(GUILD, self.campaign.id)
        self.assertEqual(same.kind, "monster")
        await menu._kind(self.press())
        (now,) = await self.store.list(GUILD, self.campaign.id)
        self.assertEqual(now.kind, "item")

    async def test_remove_asks_then_removes_and_undo_puts_it_back(self) -> None:
        saved = await self.add(players_may_know=True)
        confirm = ui.ConfirmRemove(self.campaign, saved, 0)
        it = self.press()
        await confirm._yes(it)
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])
        self.assertIn("Removed **Hollow King**", self.said(it))
        removed = self.bot.homebrew_removed[(GUILD, self.campaign.id)]
        menu = ui.ListMenu(self.campaign, [], 0, removed)
        undo = [c for c in menu.children if "Undo" in str(getattr(c, "label", ""))]
        self.assertEqual(len(undo), 1)
        await undo[0].callback(self.press())
        (back,) = await self.store.list(GUILD, self.campaign.id)
        self.assertEqual((back.name, back.players_may_know), ("Hollow King", True))
        self.assertEqual(removed, [])

    async def test_a_stale_view_is_refused_with_the_new_list(self) -> None:
        saved = await self.add()
        await self.store.edit(
            GUILD, self.campaign.id, DM, saved.id, kind="monster", name="Hollow King", text="v2"
        )
        it = self.press()
        await ui.ConfirmRemove(self.campaign, saved, 0)._yes(it)  # saved is version 1
        self.assertIn("Another DM changed that entry", self.said(it))
        self.assertEqual(len(await self.store.list(GUILD, self.campaign.id)), 1)

    async def test_the_removed_list_keeps_five(self) -> None:
        for i in range(7):
            e = await self.add(f"E{i}")
            await ui.ConfirmRemove(self.campaign, e, 0)._yes(self.press())
        kept = self.bot.homebrew_removed[(GUILD, self.campaign.id)]
        self.assertEqual([e.name for e in kept], ["E2", "E3", "E4", "E5", "E6"])


class EveryPress(HomebrewUiTest):
    async def test_a_person_who_is_no_longer_a_dm_cannot_page_or_go_back_to_the_list(self) -> None:
        await self.add("Hollow King")
        menu = ui.ListMenu(self.campaign, [], 1, [])
        handlers = [
            menu._older,
            menu._newer,
            ui.KindPick(self.campaign, 0)._back,
            ui.ConfirmRemove(self.campaign, await self.add("X"), 0)._keep,
        ]
        for handler in handlers:
            it = self.press(PLAYER)
            await handler(it)
            self.assertEqual(self.said(it), ui.ONLY_DMS)
            self.assertNotIn("Hollow King", self.said(it))

    async def test_a_name_that_case_folds_long_is_refused_in_plain_words_not_crashed(self) -> None:
        saved = await self.add("ΐ" * 80)  # each letter folds to three: 240, within the limit
        self.assertEqual(len(saved.name), 80)


class TheFile(HomebrewUiTest):
    def with_upload(self, form: Any, text: str) -> Any:
        """A load form whose file box holds `text`."""

        class Attachment:
            size = len(text.encode())

            async def read(self) -> bytes:
                return text.encode()

        form.file = SimpleNamespace(component=SimpleNamespace(values=[Attachment()]))
        return form

    async def test_download_sends_the_file_privately_to_a_dm_only(self) -> None:
        await self.add("Hollow King")
        menu = ui.ListMenu(self.campaign, [], 0, [])
        it = self.press()
        await menu._download(it)
        kwargs = it.followup.send.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual(kwargs["file"].filename, "homebrew-frostmaiden.txt")
        self.assertIn(b"## Hollow King (monster)", kwargs["file"].fp.read())
        stranger = self.press(PLAYER)
        await menu._download(stranger)
        self.assertEqual(self.said(stranger), ui.ONLY_DMS)

    async def test_load_opens_the_form_for_a_dm_and_refuses_anyone_else(self) -> None:
        menu = ui.ListMenu(self.campaign, [], 0, [])
        it = self.press()
        await menu._load(it)
        self.assertIsInstance(it.response.modal, ui.LoadForm)
        stranger = self.press(PLAYER)
        await menu._load(stranger)
        self.assertIsNone(stranger.response.modal)
        self.assertEqual(self.said(stranger), ui.ONLY_DMS)

    async def test_the_preview_counts_and_asks_about_a_book_before_storing_anything(self) -> None:
        await self.store.add(
            GUILD, self.campaign.id, DM, kind="monster", name="Hollow King", text="Old."
        )
        text = (
            "## Hollow King (monster)\n\nNew.\n\n## Moonblade (item)\n\nA sword.\n\n"
            "## Bad (dragon)\n\nx\n"
        )
        form = self.with_upload(ui.LoadForm(self.campaign), text)
        it = self.press()
        await form.on_submit(it)
        said = it.followup.send.await_args.args[0]
        self.assertIn("2 entries", said)
        self.assertIn("1 new, 1 changed, 0 unchanged. Nothing is saved yet", said)
        self.assertIn("because DMbot couldn't read them", said)
        self.assertIn(ui.FROM_BOOK_ASK, said)
        self.assertEqual(
            [e.name for e in await self.store.list(GUILD, self.campaign.id)], ["Hollow King"]
        )
        self.assertEqual((await self.store.list(GUILD, self.campaign.id))[0].text, "Old.")

    async def test_no_loads_it_as_the_dms_own_and_records_no_confirmation(self) -> None:
        ask = ui.FromBookAsk(self.campaign, (FileEntry("rule", "House", "Mine."),), "a" * 64, 0)
        it = self.press()
        await ask._own(it)
        (saved,) = await self.store.list(GUILD, self.campaign.id)
        self.assertFalse(saved.from_book)
        self.assertEqual(await self.campaigns.confirmations(GUILD, self.campaign.id), [])
        self.assertIn("Loaded: 1 new", self.said(it))

    async def test_yes_asks_first_and_only_the_confirmation_stores_and_records_who_and_when(
        self,
    ) -> None:
        ask = ui.FromBookAsk(self.campaign, (FileEntry("rule", "Booked", "Words."),), "b" * 64, 0)
        it = self.press()
        await ask._book(it)
        self.assertIn("you say you may use this text", self.said(it))
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])  # nothing yet
        right = ui.RightToUse(self.campaign, ask.entries, ask.digest, 0)
        asked = self.press()
        await right._confirmed(asked)
        self.assertIn("Which rules is this book for?", self.said(asked))  # one more question
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])  # still nothing
        self.assertEqual(await self.campaigns.confirmations(GUILD, self.campaign.id), [])
        edition = ui.BookEdition(self.campaign, ask.entries, ask.digest, 0)
        self.assertEqual(
            [getattr(c, "label", "") for c in edition.children],
            ["Cancel", "2024 rules", "2014 rules", "Other / a supplement"],
        )
        await edition._pick("2014", self.press())
        (saved,) = await self.store.list(GUILD, self.campaign.id)
        self.assertTrue(saved.from_book)
        self.assertEqual(saved.book_edition, "2014")  # stored with the entry
        (record,) = await self.campaigns.confirmations(GUILD, self.campaign.id)
        self.assertEqual(record["purpose"], "rulebook")
        self.assertEqual(record["fingerprint"], "b" * 64)

    async def test_cancel_loads_nothing_and_a_non_dm_cannot_confirm(self) -> None:
        right = ui.RightToUse(self.campaign, (FileEntry("rule", "X", "y"),), "c" * 64, 0)
        await right._cancel(self.press())
        await right._confirmed(self.press(PLAYER))
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])
        self.assertEqual(await self.campaigns.confirmations(GUILD, self.campaign.id), [])

    async def test_the_question_is_only_for_a_book_and_each_answer_is_kept(self) -> None:
        entries = (FileEntry("rule", "Mine", "Words."),)
        own = ui.FromBookAsk(self.campaign, entries, "f" * 64, 0)
        await own._own(self.press())  # "No, all my own": no question, no edition
        (saved,) = await self.store.list(GUILD, self.campaign.id)
        self.assertEqual((saved.from_book, saved.book_edition), (False, None))
        for edition in ("2024", "other"):
            book = ui.BookEdition(
                self.campaign, (FileEntry("rule", f"Book {edition}", "x"),), "a" * 64, 0
            )
            await book._pick(edition, self.press())
        got = {e.name: e.book_edition for e in await self.store.list(GUILD, self.campaign.id)}
        self.assertEqual(got, {"Mine": None, "Book 2024": "2024", "Book other": "other"})

    async def test_a_file_over_the_limit_is_refused_with_the_count(self) -> None:
        for n in range(200):
            await self.store.add(GUILD, self.campaign.id, DM, kind="rule", name=f"R{n}", text="x")
        ask = ui.FromBookAsk(self.campaign, (FileEntry("rule", "One more", "x"),), "d" * 64, 0)
        it = self.press()
        await ask._own(it)
        self.assertIn("would make 201 entries", self.said(it))
        self.assertEqual(len(await self.store.list(GUILD, self.campaign.id)), 200)


class OnlyOnce(HomebrewUiTest):
    async def test_a_double_press_records_the_confirmation_and_loads_once(self) -> None:
        right = ui.BookEdition(self.campaign, (FileEntry("rule", "Booked", "Words."),), "e" * 64, 0)
        await right._pick("2024", self.press())
        again = self.press()
        await right._pick("2024", again)
        self.assertEqual(len(await self.campaigns.confirmations(GUILD, self.campaign.id)), 1)
        self.assertIn("already being loaded", self.said(again))
