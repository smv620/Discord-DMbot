"""📚 Homebrew (#1520), the DM's private menu: the list, Add (kind, then the form), an entry's
Turn off or on, its kind, Remove with Undo, and that every press checks the person is a DM. The real
store, against the database."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot.campaigns import CampaignStore
from dmbot.dm_screen.settings import HomebrewButton
from dmbot.rules import page_fetch
from dmbot.rules.homebrew import FileEntry, HomebrewSection, HomebrewStore
from dmbot.rules.homebrew_import import HomebrewImporter
from dmbot.rules.homebrew_sources import SiteApprovalsSection, SourceStore
from dmbot.ui import homebrew as ui
from dmbot.ui import logic
from tests.pg import DatabaseTest
from tests.test_homebrew_import import URL, Fetches
from tests.test_memory_names import FakeResponse

GUILD, DM, PLAYER = 111, 7, 9


class HomebrewUiTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(HomebrewSection())
        self.campaigns.register_section(SiteApprovalsSection())
        self.store = HomebrewStore(self.db)
        self.sources = SourceStore(self.db)
        self.net = Fetches()
        self.importer = HomebrewImporter(self.store, self.sources, fetch=self.net)
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        self.bot = SimpleNamespace(
            campaigns=self.campaigns,
            homebrew=self.store,
            sources=self.sources,
            importer=self.importer,
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
        if it.response.edited:
            return it.response.edited[-1][1]
        return it.response.sent[-1][1]["view"]


class TheList(HomebrewUiTest):
    async def test_an_empty_list_says_how_to_start_and_only_the_dm_sees_the_menu(self) -> None:
        it = self.press()
        await ui.show_list(it, self.campaign, first=True)
        self.assertIn("No homebrew yet", self.said(it))
        self.assertIn("Share a rulebook", self.said(it))

    async def test_an_entry_that_is_off_says_so_in_the_list(self) -> None:
        await self.add("Hollow King")
        lamp = await self.add("Moonblade", kind="item")
        await self.store.set_enabled(GUILD, self.campaign.id, DM, lamp.id, False)
        it = self.press()
        await ui.show_list(it, self.campaign, first=True, kind="monster")
        self.assertIn("👹 Monster **Hollow King**:", self.said(it))
        it = self.press()
        await ui.show_list(it, self.campaign, first=True, kind="item")
        text = self.said(it)
        self.assertIn("🗡 Item **Moonblade** (off):", text)
        self.assertIn("(off) means DMbot ignores it until you turn it on.", text)
        self.assertNotIn("Hollow King", text)  # only this kind
        self.assertNotIn("🙈", text)
        self.assertNotIn("👀", text)

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


def labels(view: Any) -> list[str]:
    return [str(getattr(c, "label", "")) for c in view.children if hasattr(c, "label")]


class KindsMenu(HomebrewUiTest):
    async def test_one_button_for_each_kind_with_entries_and_how_many_are_on(self) -> None:
        for n in range(3):
            await self.add(f"Bolt {n}", kind="spell")
        (bolt,) = (await self.store.list(GUILD, self.campaign.id))[:1]
        await self.store.set_enabled(GUILD, self.campaign.id, DM, bolt.id, False)
        await self.add("Moonfolk", kind="species")
        it = self.press()
        await ui.show_list(it, self.campaign, first=True)
        shown = labels(self.view_of(it))
        self.assertEqual(shown[:2], ["Spells 2/3", "Species 1/1"])  # the kinds, in KINDS order
        self.assertNotIn("Monsters", " ".join(shown))  # no entries, no button
        for action in ("➕ Add", ui.LINK_LABEL, ui.LOAD_LABEL, ui.DOWNLOAD_LABEL, ui.SITES_LABEL):
            self.assertIn(action, shown)
        self.assertIn(ui.PACKS_LABEL, shown)
        self.assertNotIn("Bolt 0", self.said(it))  # the long list is not drawn

    async def test_a_kind_opens_its_list_and_all_kinds_goes_back(self) -> None:
        await self.add("Frost Lance", kind="spell")
        await self.add("Hollow King")
        menu = ui.ListMenu(self.campaign, await self.store.list(GUILD, self.campaign.id), 0, [])
        it = self.press()
        await menu._open_kind("spell", it)
        self.assertIn("Frost Lance", self.said(it))
        self.assertNotIn("Hollow King", self.said(it))
        inner = self.view_of(it)
        self.assertIn(ui.ALL_KINDS_LABEL, labels(inner))
        back = self.press()
        await inner._all_kinds(back)
        self.assertEqual(labels(self.view_of(back))[:2], ["Spells 1/1", "Monsters 1/1"])

    async def test_a_kind_on_a_later_page_with_an_undo_still_builds(self) -> None:
        for n in range(27):
            await self.add(f"Bolt {n:02}", kind="spell")
        gone = (await self.store.list(GUILD, self.campaign.id))[0]
        entries = await self.store.list(GUILD, self.campaign.id)
        view = ui.ListMenu(self.campaign, entries, 1, [gone], "spell")  # (no ValueError)
        self.assertEqual(
            [str(c.label) for c in view.children if hasattr(c, "label") and c.row == 2],
            ["◀ Newer", "↩️ Put back Bolt 00"],
        )

    async def test_the_undo_label_shows_an_ordinary_long_name_whole_and_cuts_a_very_long_one(
        self,
    ) -> None:
        names = ("Amulet of Tenacity", "A" * 20, "A" * 21, "Hollow King")
        for name in names:
            await self.add(name, kind="item")
        made = {e.name: ui._undo_label(e) for e in await self.store.list(GUILD, self.campaign.id)}
        self.assertEqual(made["Amulet of Tenacity"], "↩️ Put back Amulet of Tenacity")
        self.assertEqual(made["A" * 20], "↩️ Put back " + "A" * 20)
        self.assertEqual(made["A" * 21], "↩️ Put back " + "A" * 19 + "…")
        for label in made.values():
            self.assertLessEqual(len(label), logic.NAME_LABEL_MAX)  # the wider phone label
            self.assertLessEqual(len(label), logic.BUTTON_LABEL_MAX)  # and Discord's limit

    async def test_a_player_cannot_use_the_packs_button(self) -> None:
        await self.add("Frost Lance", kind="spell")
        menu = ui.ListMenu(self.campaign, await self.store.list(GUILD, self.campaign.id), 0, [])
        it = self.press(PLAYER)
        await menu._packs(it)
        self.assertEqual(self.said(it), ui.ONLY_DMS)

    async def test_no_homebrew_shows_no_kind_buttons(self) -> None:
        it = self.press()
        await ui.show_list(it, self.campaign, first=True)
        self.assertFalse([b for b in labels(self.view_of(it)) if "/" in b])

    async def test_the_last_entry_of_a_kind_removed_goes_back_to_the_menu(self) -> None:
        await self.add("Frost Lance", kind="spell")
        await self.add("Hollow King")
        spell = next(e for e in await self.store.list(GUILD, self.campaign.id) if e.kind == "spell")
        await self.store.remove(
            GUILD, self.campaign.id, DM, spell.id, unchanged_since=spell.version
        )
        it = self.press()
        await ui._redraw(it, self.campaign, kind="spell")
        self.assertEqual(labels(self.view_of(it))[0], "Monsters 1/1")

    async def test_a_players_press_on_a_kind_does_nothing(self) -> None:
        await self.add("Frost Lance", kind="spell")
        menu = ui.ListMenu(self.campaign, await self.store.list(GUILD, self.campaign.id), 0, [])
        it = self.press(PLAYER)
        await menu._open_kind("spell", it)
        self.assertEqual(self.said(it), ui.ONLY_DMS)


class Adding(HomebrewUiTest):
    async def test_add_asks_the_kind_then_the_form_and_the_entry_starts_on(self) -> None:
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
        self.assertEqual((saved.kind, saved.name, saved.enabled), ("spell", "Frost Lance", True))
        self.assertIn("DMbot uses it now", self.said(submit))

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
    async def test_turn_off_and_turn_on_ask_nothing_and_only_a_dm_can(self) -> None:
        saved = await self.add()
        menu = ui.EntryMenu(self.campaign, saved, 0)
        labels = [str(getattr(c, "label", "")) for c in menu.children]
        self.assertIn("Turn off", labels)
        self.assertFalse(any("👀" in label or "🙈" in label for label in labels))
        await menu._enabled(self.press(PLAYER))
        (same,) = await self.store.list(GUILD, self.campaign.id)
        self.assertTrue(same.enabled)  # a player's press changed nothing
        it = self.press()
        await menu._enabled(it)
        (now,) = await self.store.list(GUILD, self.campaign.id)
        self.assertFalse(now.enabled)
        self.assertIn("Turned off. DMbot will not use it.", self.said(it))
        again = ui.EntryMenu(self.campaign, now, 0)
        self.assertIn("Turn on", [str(getattr(c, "label", "")) for c in again.children])
        it2 = self.press()
        await again._enabled(it2)
        (back,) = await self.store.list(GUILD, self.campaign.id)
        self.assertTrue(back.enabled)
        self.assertIn("Turned on. DMbot will use it.", self.said(it2))

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
        saved = await self.add()
        confirm = ui.ConfirmRemove(self.campaign, saved, 0)
        it = self.press()
        await confirm._yes(it)
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])
        self.assertIn("Removed **Hollow King**", self.said(it))
        removed = self.bot.homebrew_removed[(GUILD, self.campaign.id)]
        menu = ui.ListMenu(self.campaign, [], 0, removed)
        undo = [c for c in menu.children if "Put back" in str(getattr(c, "label", ""))]
        self.assertEqual(len(undo), 1)
        await undo[0].callback(self.press())
        (back,) = await self.store.list(GUILD, self.campaign.id)
        self.assertEqual((back.name, back.enabled), ("Hollow King", True))
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

    async def test_download_tags_shared_book_entries_like_the_players_card(self) -> None:
        await self.store.load_file(
            GUILD,
            self.campaign.id,
            DM,
            (FileEntry("spell", "Moonlance", "From the book.", None, False),),
            from_book=True,
            book_edition="2014",
        )
        await self.add("Hollow King")
        menu = ui.ListMenu(self.campaign, [], 0, [])
        it = self.press()
        await menu._download(it)
        text = it.followup.send.await_args.kwargs["file"].fp.read().decode()
        self.assertIn("## Moonlance (spell)\n[Shared book] [Legacy 2014]\n", text)
        self.assertNotIn("## Hollow King (monster)\n[Shared book]", text)

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
        ask = ui.FromBookAsk(self.campaign, (FileEntry("feat", "House", "Mine."),), "a" * 64, 0)
        asked = self.press()
        await ask._own(asked)
        self.assertIn("Who made these?", self.said(asked))  # one more question, nothing stored yet
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])
        it = self.press()
        await ui.WhoMade(self.campaign, ask.entries, ask.digest, 0)._me(it)
        (saved,) = await self.store.list(GUILD, self.campaign.id)
        self.assertFalse(saved.from_book)
        self.assertEqual(
            (saved.source_kind, saved.credit_kind, saved.added_by), ("upload", "user", DM)
        )
        self.assertEqual(await self.campaigns.confirmations(GUILD, self.campaign.id), [])
        self.assertIn("Loaded: 1 new", self.said(it))

    async def test_yes_asks_first_and_only_the_confirmation_stores_and_records_who_and_when(
        self,
    ) -> None:
        ask = ui.FromBookAsk(self.campaign, (FileEntry("feat", "Booked", "Words."),), "b" * 64, 0)
        it = self.press()
        await ask._book(it)
        self.assertIn("you say you may use this text", self.said(it))
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])  # nothing yet
        right = ui.RightToUse(self.campaign, ask.entries, ask.digest, 0)
        asked = self.press()
        await right._confirmed(asked)
        self.assertIn("Which rules is this book written for?", self.said(asked))
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])  # still nothing
        self.assertEqual(await self.campaigns.confirmations(GUILD, self.campaign.id), [])
        edition = ui.BookEdition(self.campaign, ask.entries, ask.digest, 0)
        self.assertEqual(
            [getattr(c, "label", "") for c in edition.children],
            ["Cancel", "2024 book", "2014 book", "Not sure / add-on"],
        )
        await edition._pick("2014", self.press())
        (saved,) = await self.store.list(GUILD, self.campaign.id)
        self.assertTrue(saved.from_book)
        self.assertEqual(saved.book_edition, "2014")  # stored with the entry
        (record,) = await self.campaigns.confirmations(GUILD, self.campaign.id)
        self.assertEqual(record["purpose"], "rulebook")
        self.assertEqual(record["fingerprint"], "b" * 64)

    async def test_cancel_loads_nothing_and_a_non_dm_cannot_confirm(self) -> None:
        right = ui.RightToUse(self.campaign, (FileEntry("feat", "X", "y"),), "c" * 64, 0)
        await right._cancel(self.press())
        await right._confirmed(self.press(PLAYER))
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])
        self.assertEqual(await self.campaigns.confirmations(GUILD, self.campaign.id), [])

    async def test_the_question_is_only_for_a_book_and_each_answer_is_kept(self) -> None:
        entries = (FileEntry("feat", "Mine", "Words."),)
        own = ui.WhoMade(self.campaign, entries, "f" * 64, 0)
        await own._me(self.press())  # "No, all my own", then "Me": no question, no edition
        (saved,) = await self.store.list(GUILD, self.campaign.id)
        self.assertEqual((saved.from_book, saved.book_edition), (False, None))
        for edition in ("2024", "other"):
            book = ui.BookEdition(
                self.campaign, (FileEntry("feat", f"Book {edition}", "x"),), "a" * 64, 0
            )
            await book._pick(edition, self.press())
        got = {e.name: e.book_edition for e in await self.store.list(GUILD, self.campaign.id)}
        self.assertEqual(got, {"Mine": None, "Book 2024": "2024", "Book other": "other"})

    async def test_someone_else_credits_a_typed_name(self) -> None:
        who = ui.WhoMade(self.campaign, (FileEntry("feat", "Borrowed", "Words."),), "e" * 64, 0)
        form = ui.NameForm(who)
        form.name._value = "  Sam   Rivers "
        it = self.press()
        await form.on_submit(it)
        (saved,) = await self.store.list(GUILD, self.campaign.id)
        self.assertEqual(
            (saved.source_kind, saved.credit_kind, saved.credit_text, saved.credit_domain),
            ("upload", "name", "Sam Rivers", None),
        )
        self.assertIn("Credited to Sam Rivers", self.said(it))
        self.assertEqual(await self.sources.approvals(GUILD, self.campaign.id), [])  # no site

    async def test_a_website_asks_once_and_only_the_confirmation_stores_and_approves(self) -> None:
        who = ui.WhoMade(self.campaign, (FileEntry("feat", "Webby", "Words."),), "e" * 64, 0)
        form = ui.SiteForm(who)
        form.site._value = "https://www.DNDBeyond.com/magic-items/1-x"
        it = self.press()
        await form.on_submit(it)
        self.assertIn("right to use homebrew from dndbeyond.com", self.said(it))
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])  # nothing yet
        self.assertEqual(await self.sources.approvals(GUILD, self.campaign.id), [])
        right = self.view_of(it)
        self.assertEqual(
            [getattr(c, "label", "") for c in right.children], ["Cancel", "I have the right"]
        )
        await right._confirmed(self.press(PLAYER))  # a player's press changes nothing
        self.assertEqual(await self.sources.approvals(GUILD, self.campaign.id), [])
        done = self.press()
        await right._confirmed(done)
        (saved,) = await self.store.list(GUILD, self.campaign.id)
        self.assertEqual(
            (saved.credit_kind, saved.credit_domain, saved.credit_text),
            ("website", "dndbeyond.com", None),
        )
        (approval,) = await self.sources.approvals(GUILD, self.campaign.id)
        self.assertEqual((approval.domain, approval.approved_by), ("dndbeyond.com", DM))
        self.assertIn("Credited to dndbeyond.com", self.said(done))
        # The second time for the same site in this campaign needs no new warning.
        again = ui.WhoMade(self.campaign, (FileEntry("feat", "Webby Two", "More."),), "f" * 64, 0)
        form2 = ui.SiteForm(again)
        form2.site._value = "dndbeyond.com"
        quick = self.press()
        await form2.on_submit(quick)
        self.assertIn("Loaded: 1 new", self.said(quick))
        self.assertEqual(len(await self.sources.approvals(GUILD, self.campaign.id)), 1)

    async def test_a_bad_site_loads_nothing_and_cancel_approves_nothing(self) -> None:
        who = ui.WhoMade(self.campaign, (FileEntry("feat", "Webby", "Words."),), "e" * 64, 0)
        form = ui.SiteForm(who)
        for bad in ("", "not a site", "http://example.com", "10.0.0.1"):
            form.site._value = bad
            it = self.press()
            await form.on_submit(it)
            self.assertIn("can't read that as a website", self.said(it))
        form.site._value = "example.org"
        await form.on_submit(self.press())
        right = ui.RightToUseSite(who, "example.org")
        await right._cancel(self.press())
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])
        self.assertEqual(await self.sources.approvals(GUILD, self.campaign.id), [])

    async def test_only_a_dm_gets_the_who_made_it_buttons(self) -> None:
        who = ui.WhoMade(self.campaign, (FileEntry("feat", "X", "y"),), "e" * 64, 0)
        for press in (who._me, who._site, who._someone):
            it = self.press(PLAYER)
            await press(it)
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])
        self.assertIsNone(self.press(PLAYER).response.modal)

    async def test_a_file_over_the_limit_is_refused_with_the_count(self) -> None:
        for n in range(200):
            await self.store.add(GUILD, self.campaign.id, DM, kind="feat", name=f"R{n}", text="x")
        ask = ui.WhoMade(self.campaign, (FileEntry("feat", "One more", "x"),), "d" * 64, 0)
        it = self.press()
        await ask._me(it)
        self.assertIn("would make 201 entries", self.said(it))
        self.assertEqual(len(await self.store.list(GUILD, self.campaign.id)), 200)


class OnlyOnce(HomebrewUiTest):
    async def test_a_double_press_records_the_confirmation_and_loads_once(self) -> None:
        right = ui.BookEdition(self.campaign, (FileEntry("feat", "Booked", "Words."),), "e" * 64, 0)
        await right._pick("2024", self.press())
        again = self.press()
        await right._pick("2024", again)
        self.assertEqual(len(await self.campaigns.confirmations(GUILD, self.campaign.id)), 1)
        self.assertIn("already being loaded", self.said(again))


class FromALink(HomebrewUiTest):
    """🔗 Add from a link (#1634): the address, the once-per-site warning, the preview, Save."""

    async def submit(self, address: str, user: int = DM) -> Any:
        form = ui.LinkForm(self.campaign, 0)
        form.address._value = address
        it = self.press(user)
        await form.on_submit(it)
        return it

    def test_the_button_is_beside_add_and_the_old_ones_stay(self) -> None:
        labels = [
            str(getattr(c, "label", "")) for c in ui.ListMenu(self.campaign, [], 0, []).children
        ]
        self.assertIn("🔗 Add from a link", labels)
        self.assertTrue({"➕ Add", ui.LOAD_LABEL, ui.DOWNLOAD_LABEL} <= set(labels))

    async def test_only_a_dm_gets_the_form(self) -> None:
        menu = ui.ListMenu(self.campaign, [], 0, [])
        it = self.press(PLAYER)
        await menu._link(it)
        self.assertIsNone(it.response.modal)
        it = self.press()
        await menu._link(it)
        self.assertIsInstance(it.response.modal, ui.LinkForm)
        it = await self.submit(URL, PLAYER)
        self.assertEqual(self.net.asked, [])

    async def test_another_site_is_told_what_is_allowed_and_nothing_is_fetched(self) -> None:
        for address in (
            "https://example.com/spells/1-x",
            "http://www.dndbeyond.com/spells/1-x",
            "x",
        ):
            it = await self.submit(address)
            self.assertIn("only add homebrew from D&D Beyond", self.said(it))
        self.assertEqual(self.net.asked, [])

    async def test_the_first_link_asks_once_then_reads_then_saves_and_the_second_asks_nothing(
        self,
    ) -> None:
        it = await self.submit(URL)
        self.assertIn("right to use homebrew from dndbeyond.com", self.said(it))
        self.assertEqual(self.net.asked, [])  # nothing is read before the confirmation
        self.assertEqual(await self.sources.approvals(GUILD, self.campaign.id), [])
        right = self.view_of(it)
        self.assertEqual(
            [getattr(c, "label", "") for c in right.children], ["Cancel", "I have the right"]
        )
        await right._confirmed(self.press(PLAYER))  # a player's press changes nothing
        self.assertEqual(await self.sources.approvals(GUILD, self.campaign.id), [])
        shown = self.press()
        await right._confirmed(shown)
        self.assertEqual(len(await self.sources.approvals(GUILD, self.campaign.id)), 1)
        self.assertEqual(len(self.net.asked), 1)
        self.assertIn("Moonlit Charm", self.said(shown))
        self.assertIn("Not saved yet. Press **Save**", self.said(shown))
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])  # not saved yet
        preview = self.view_of(shown)
        done = self.press()
        await preview._save(done)
        (saved,) = await self.store.list(GUILD, self.campaign.id)
        self.assertEqual(
            (saved.name, saved.source_kind, saved.page_url), ("Moonlit Charm", "link", URL)
        )
        self.assertIn("Added **Moonlit Charm**", self.said(done))
        # A second page from the same site in this campaign asks nothing.
        second = await self.submit(URL.replace("2302039-moonlit-charm", "9-other") + "")
        self.assertIn("Not saved yet. Press **Save**", self.said(second))
        self.assertEqual(len(await self.sources.approvals(GUILD, self.campaign.id)), 1)

    async def test_another_campaign_asks_again_and_cancel_adds_and_approves_nothing(self) -> None:
        other = await self.campaigns.create(GUILD, "Other", DM)
        await self.sources.approve(GUILD, other.id, DM, "dndbeyond.com")
        it = await self.submit(URL)  # this campaign has not approved the site
        right = self.view_of(it)
        await right._cancel(self.press())
        self.assertEqual(await self.sources.approvals(GUILD, self.campaign.id), [])
        self.assertEqual(await self.store.list(GUILD, self.campaign.id), [])
        self.assertEqual(self.net.asked, [])

    async def test_not_public_and_no_answer_are_plain_and_a_double_save_adds_once(self) -> None:
        await self.sources.approve(GUILD, self.campaign.id, DM, "dndbeyond.com")
        self.net.reply = page_fetch.Reply(404, "")
        it = await self.submit(URL)
        self.assertIn("That page isn't public", self.said(it))
        self.net.reply = page_fetch.Reply(500, "")
        it = await self.submit(URL)
        self.assertIn("D&D Beyond didn't answer", self.said(it))
        self.net.reply = page_fetch.Reply(200, "<h1>Zap</h1><p>It zaps.</p>")
        shown = await self.submit(URL)
        preview = self.view_of(shown)
        await preview._save(self.press())
        again = self.press()
        await preview._save(again)
        self.assertEqual(len(await self.store.list(GUILD, self.campaign.id)), 1)
