"""🌐 Approved sites (#1652): a DM sees each approved site with who said so and when, takes one back
after one plain question, the entries stay but are not used (and the list says so), a re-approval
brings them back, and a player's press does nothing."""

from __future__ import annotations

from typing import Any

from dmbot.ui import approved_sites as sites
from dmbot.ui import homebrew as ui
from tests.test_homebrew_import import URL
from tests.test_homebrew_ui import DM, GUILD, PLAYER, HomebrewUiTest


class Sites(HomebrewUiTest):
    async def linked(self) -> Any:
        await self.sources.approve(GUILD, self.campaign.id, DM, "dndbeyond.com")
        preview = await self.importer.read(GUILD, self.campaign.id, URL)
        return await self.importer.save(GUILD, self.campaign.id, DM, preview)

    async def open_sites(self, user: int = DM) -> Any:
        it = self.press(user)
        menu = ui.ListMenu(self.campaign, [], 0, [])
        await menu._sites(it)
        return it

    async def test_the_list_shows_who_and_when_and_the_empty_list_says_how_it_fills(self) -> None:
        it = await self.open_sites()
        self.assertIn(sites.NONE_YET, self.said(it))
        await self.sources.approve(GUILD, self.campaign.id, DM, "dndbeyond.com")
        it = await self.open_sites()
        text = self.said(it)
        self.assertIn("**dndbeyond.com**: approved by a DM, <t:", text)
        self.assertIn(":D>", text)
        self.assertEqual(
            [
                str(getattr(c, "label", ""))
                for c in self.view_of(it).children
                if hasattr(c, "label")
            ],
            ["↩ Back to homebrew"],
        )

    async def test_the_question_counts_the_entries_and_says_nothing_is_deleted(self) -> None:
        await self.linked()
        it = await self.open_sites()
        view = self.view_of(it)
        view.pick._values = ["dndbeyond.com"]
        ask = self.press()
        await view._picked(ask)
        text = self.said(ask)
        self.assertEqual(
            text,
            "Take back **dndbeyond.com**? DMbot will stop using the 1 entry from it until a DM "
            "says yes again. Nothing is deleted.",
        )
        self.assertEqual([c.label for c in self.view_of(ask).children], ["Cancel", "Take it back"])

    async def test_cancel_changes_nothing_and_take_it_back_stops_the_entries(self) -> None:
        entry = await self.linked()
        confirm = sites.TakeBack(self.campaign, "dndbeyond.com")
        await confirm._cancel(self.press())
        self.assertTrue(await self.sources.approved(GUILD, self.campaign.id, "dndbeyond.com"))
        it = self.press()
        await confirm._confirm(it)
        self.assertIn(
            "Took back **dndbeyond.com**. Its entries are saved but skipped.", self.said(it)
        )
        self.assertFalse(await self.sources.approved(GUILD, self.campaign.id, "dndbeyond.com"))
        # Saved, but not used; the list says so.
        self.assertEqual(len(await self.store.list(GUILD, self.campaign.id)), 1)
        self.assertEqual(await self.store.list(GUILD, self.campaign.id, usable_only=True), [])
        listing = self.press()
        await ui.show_list(listing, self.campaign, first=True, kind=entry.kind)
        self.assertIn(f"**{entry.name}**{ui.WAITING_FOR_SITE}", self.said(listing))
        # Adding from the site asks the right-to-use question again.
        self.assertFalse(await self.sources.approved(GUILD, self.campaign.id, "dndbeyond.com"))
        # A new approval brings the entry back.
        await self.sources.approve(GUILD, self.campaign.id, DM, "dndbeyond.com")
        self.assertEqual(len(await self.store.list(GUILD, self.campaign.id, usable_only=True)), 1)
        again = self.press()
        await ui.show_list(again, self.campaign, first=True, kind=entry.kind)
        self.assertNotIn(ui.WAITING_FOR_SITE, self.said(again))

    async def test_a_double_press_takes_it_back_once(self) -> None:
        await self.linked()
        confirm = sites.TakeBack(self.campaign, "dndbeyond.com")
        await confirm._confirm(self.press())
        second = self.press()
        await confirm._confirm(second)
        self.assertFalse(await self.sources.approved(GUILD, self.campaign.id, "dndbeyond.com"))

    async def test_a_player_can_neither_open_it_nor_take_anything_back(self) -> None:
        await self.linked()
        player = await self.open_sites(PLAYER)
        self.assertEqual(self.said(player), ui.ONLY_DMS)
        confirm = sites.TakeBack(self.campaign, "dndbeyond.com")
        it = self.press(PLAYER)
        await confirm._confirm(it)
        self.assertEqual(self.said(it), ui.ONLY_DMS)
        self.assertTrue(await self.sources.approved(GUILD, self.campaign.id, "dndbeyond.com"))
        picker = sites.SitesMenu(self.campaign, [], {})
        again = self.press(PLAYER)
        await picker._back(again)
        self.assertEqual(self.said(again), ui.ONLY_DMS)

    async def test_another_campaigns_approval_is_not_touched(self) -> None:
        other = await self.campaigns.create(GUILD, "Elsewhere", DM)
        await self.sources.approve(GUILD, other.id, DM, "dndbeyond.com")
        await self.linked()
        await sites.TakeBack(self.campaign, "dndbeyond.com")._confirm(self.press())
        self.assertTrue(await self.sources.approved(GUILD, other.id, "dndbeyond.com"))
