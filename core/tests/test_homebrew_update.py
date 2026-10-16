"""🔄 Update from the link (#1645): a DM's press fetches the entry's one page again (same guards,
gate and approval), shows what changed, and Save replaces the one kept copy for every campaign that
points at it. A gone page is marked stale and keeps its words; a player's press does nothing."""

from __future__ import annotations

from dmbot.rules import dndbeyond, page_fetch
from dmbot.rules.homebrew_import import GONE, SAME, TAKEN_BACK, HomebrewImporter, ImportRefused
from dmbot.ui import homebrew as ui
from tests.test_dndbeyond import PAGE
from tests.test_homebrew_import import DM, GUILD, PLAYER, URL, ImportTest
from tests.test_homebrew_ui import HomebrewUiTest

CHANGED = PAGE.replace("you see in the dark", "you see in the dark and fly")


class Updating(ImportTest):
    async def linked(self, campaign: object | None = None) -> object:
        c = campaign or self.one
        await self.approve(c)
        preview = await self.importer.read(GUILD, c.id, URL)  # type: ignore[attr-defined]
        return await self.importer.save(GUILD, c.id, DM, preview)  # type: ignore[attr-defined]

    async def status(self) -> str:
        async with self.db.unscoped() as conn:
            cur = await conn.execute("SELECT fetch_status AS s FROM homebrew_pages")
            return str((await cur.fetchone() or {})["s"])

    async def test_nothing_changed_says_so_and_a_fetch_happened(self) -> None:
        entry = await self.linked()
        fresh = await self.importer.refresh(GUILD, self.one.id, entry)  # type: ignore[arg-type]
        self.assertEqual((fresh.note, fresh.changed), (SAME, False))
        self.assertEqual(len(self.net.asked), 2)  # the kept copy is not re-used: that's the point

    async def test_save_replaces_the_page_for_two_campaigns_and_cancel_changes_nothing(
        self,
    ) -> None:
        one = await self.linked()
        await self.linked(self.two)
        self.net.reply = page_fetch.Reply(200, CHANGED)
        fresh = await self.importer.refresh(GUILD, self.one.id, one)  # type: ignore[arg-type]
        self.assertEqual(fresh.note, "The page changed: the words.")
        # Nothing is saved by looking (Cancel is just not pressing Save).
        (still,) = await self.brew.list(GUILD, self.one.id)
        self.assertNotIn("fly", still.text)
        await self.importer.save_refresh(GUILD, self.one.id, fresh.preview)
        for c in (self.one, self.two):
            (entry,) = await self.brew.list(GUILD, c.id)
            self.assertIn("and fly", entry.text)
        self.assertEqual(await self.status(), "ok")

    async def test_a_gone_page_is_marked_stale_and_keeps_its_words(self) -> None:
        entry = await self.linked()
        self.net.reply = page_fetch.Reply(404, "<title>Page not found</title>")
        with self.assertRaises(ImportRefused) as raised:
            await self.importer.refresh(GUILD, self.one.id, entry)  # type: ignore[arg-type]
        self.assertEqual(str(raised.exception), GONE)
        self.assertEqual(await self.status(), "stale")
        (kept,) = await self.brew.list(GUILD, self.one.id)
        self.assertIn("see in the dark", kept.text)
        # A later good fetch, saved, makes it fresh again.
        self.net.reply = page_fetch.Reply(200, PAGE)
        fresh = await self.importer.refresh(GUILD, self.one.id, kept)
        await self.importer.save_refresh(GUILD, self.one.id, fresh.preview)
        self.assertEqual(await self.status(), "ok")

    async def test_a_failed_fetch_changes_nothing_and_does_not_mark_stale(self) -> None:
        entry = await self.linked()
        self.net.reply = page_fetch.Reply(503, "busy")
        with self.assertRaises(ImportRefused) as raised:
            await self.importer.refresh(GUILD, self.one.id, entry)  # type: ignore[arg-type]
        self.assertEqual(str(raised.exception), dndbeyond.NO_ANSWER)
        self.assertEqual(await self.status(), "ok")

    async def test_the_site_approval_is_checked_again_and_the_gate_is_honoured(self) -> None:
        entry = await self.linked()
        await self.sources.revoke(GUILD, self.one.id, DM, "dndbeyond.com")
        with self.assertRaises(ImportRefused) as raised:
            await self.importer.refresh(GUILD, self.one.id, entry)  # type: ignore[arg-type]
        self.assertEqual(str(raised.exception), TAKEN_BACK)
        fetched = len(self.net.asked)
        await self.approve()
        gate = page_fetch.FetchGate()
        gate.enter(GUILD, self.one.id)  # one already running for this campaign
        busy = HomebrewImporter(self.brew, self.sources, fetch=self.net, gate=gate)
        with self.assertRaises(ImportRefused):
            await busy.refresh(GUILD, self.one.id, entry)  # type: ignore[arg-type]
        self.assertEqual(len(self.net.asked), fetched)

    async def test_save_after_the_site_was_taken_back_is_refused(self) -> None:
        entry = await self.linked()
        self.net.reply = page_fetch.Reply(200, CHANGED)
        fresh = await self.importer.refresh(GUILD, self.one.id, entry)  # type: ignore[arg-type]
        await self.sources.revoke(GUILD, self.one.id, DM, "dndbeyond.com")
        with self.assertRaises(ImportRefused):
            await self.importer.save_refresh(GUILD, self.one.id, fresh.preview)
        self.assertIn(
            "see in the dark", (await self.brew.list(GUILD, self.one.id, usable_only=False))[0].text
        )

    async def test_a_stale_page_that_answers_the_same_is_offered_save_and_becomes_fresh(
        self,
    ) -> None:
        entry = await self.linked()
        async with self.db.unscoped() as conn:
            await conn.execute("UPDATE homebrew_pages SET fetch_status = 'stale'")
        fresh = await self.importer.refresh(GUILD, self.one.id, entry)  # type: ignore[arg-type]
        self.assertTrue(fresh.changed)
        await self.importer.save_refresh(GUILD, self.one.id, fresh.preview)
        self.assertEqual(await self.status(), "ok")

    async def test_save_for_a_campaign_with_no_entry_for_the_page_is_refused(self) -> None:
        entry = await self.linked()
        self.net.reply = page_fetch.Reply(200, CHANGED)
        fresh = await self.importer.refresh(GUILD, self.one.id, entry)  # type: ignore[arg-type]
        await self.approve(self.two)  # approved, but it never imported this page
        with self.assertRaises(ImportRefused):
            await self.importer.save_refresh(GUILD, self.two.id, fresh.preview)

    async def test_an_entry_that_is_not_a_link_has_nothing_to_update(self) -> None:
        typed = await self.brew.add(GUILD, self.one.id, DM, kind="item", name="Lamp", text="x")
        with self.assertRaises(ImportRefused):
            await self.importer.refresh(GUILD, self.one.id, typed)

    async def test_a_restored_page_is_trusted_once_a_fetch_replaces_it(self) -> None:
        entry = await self.linked()
        async with self.db.unscoped() as conn:
            await conn.execute("UPDATE homebrew_pages SET fetch_status = 'restored'")
        fresh = await self.importer.refresh(GUILD, self.one.id, entry)  # type: ignore[arg-type]
        await self.importer.save_refresh(GUILD, self.one.id, fresh.preview)
        self.assertEqual(await self.status(), "ok")


class TheButton(HomebrewUiTest):
    async def linked(self) -> object:
        await self.sources.approve(GUILD, self.campaign.id, DM, "dndbeyond.com")
        preview = await self.importer.read(GUILD, self.campaign.id, URL)
        return await self.importer.save(GUILD, self.campaign.id, DM, preview)

    def labels(self, menu: object) -> list[str]:
        return [str(getattr(c, "label", "")) for c in menu.children]  # type: ignore[attr-defined]

    async def test_only_a_linked_entry_has_the_button_and_a_player_press_does_nothing(self) -> None:
        typed = await self.add()
        self.assertNotIn(ui.UPDATE_LABEL, self.labels(ui.EntryMenu(self.campaign, typed, 0)))
        entry = await self.linked()
        menu = ui.EntryMenu(self.campaign, entry, 0)  # type: ignore[arg-type]
        self.assertIn(ui.UPDATE_LABEL, self.labels(menu))
        asked = len(self.net.asked)
        player = self.press(PLAYER)
        await menu._update(player)
        self.assertEqual(self.said(player), ui.ONLY_DMS)
        self.assertEqual(len(self.net.asked), asked)

    async def test_update_shows_the_change_then_save_says_every_table_gets_it(self) -> None:
        entry = await self.linked()
        menu = ui.EntryMenu(self.campaign, entry, 0)  # type: ignore[arg-type]
        self.net.reply = page_fetch.Reply(200, CHANGED)
        it = self.press()
        await menu._update(it)
        self.assertIn("The page changed: the words.", self.said(it))
        self.assertIn(ui.UPDATE_WARNING, self.said(it))
        preview = self.view_of(it)
        self.assertIsInstance(preview, ui.UpdatePreview)
        cancel = self.press()
        await preview._cancel(cancel)
        (same,) = await self.store.list(GUILD, self.campaign.id)
        self.assertNotIn("fly", same.text)
        save_by_player = self.press(PLAYER)
        await preview._save(save_by_player)
        (same,) = await self.store.list(GUILD, self.campaign.id)
        self.assertNotIn("fly", same.text)
        saved = self.press()
        await preview._save(saved)
        (now,) = await self.store.list(GUILD, self.campaign.id)
        self.assertIn("and fly", now.text)
        self.assertIn(f"Updated. {ui.UPDATE_WARNING}", self.said(saved))

    async def test_a_page_that_is_the_same_offers_only_ok_and_a_gone_page_says_so(self) -> None:
        entry = await self.linked()
        menu = ui.EntryMenu(self.campaign, entry, 0)  # type: ignore[arg-type]
        it = self.press()
        await menu._update(it)
        self.assertIn(SAME, self.said(it))
        self.assertEqual(self.labels(self.view_of(it)), ["OK"])
        self.net.reply = page_fetch.Reply(404, "<title>Page not found</title>")
        gone = self.press()
        await menu._update(gone)
        self.assertIn(GONE, self.said(gone))
