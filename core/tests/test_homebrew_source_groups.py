"""Where homebrew came from (#2285): the grouping by source, the 📂 Sources list on the Homebrew
menu and its per-source downloads."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord

from dmbot.campaigns import Campaign
from dmbot.rules import homebrew_file, homebrew_groups
from dmbot.rules.homebrew import Homebrew
from dmbot.ui import homebrew_sources as ui
from tests.test_memory_names import FakeResponse

GUILD, DM, PLAYER = 1, 7, 8
NOW = 1_700_000_000


def entry(n: int, name: str = "", **kw: Any) -> Homebrew:
    base: dict[str, Any] = {
        "id": n, "campaign_id": "c1", "kind": "monster", "name": name or f"Beast {n}",
        "text": "Spooky.", "summary": None, "created_by": DM, "created_at": NOW, "updated_at": NOW,
    }  # fmt: skip
    return Homebrew(**{**base, **kw})


def campaign() -> Campaign:
    return Campaign(
        "c1", GUILD, "Frostmaiden", NOW - 86400, NOW - 3600, "2024", "2014", True,
        frozenset({DM}), None, 50, "peek",
    )  # fmt: skip


MIX = [
    entry(1),
    entry(2, source_kind="upload", credit_kind="name", credit_text="Sam"),
    entry(3, source_kind="upload", credit_kind="website", credit_domain="example.com"),
    entry(4, source_kind="link", credit_domain="dndbeyond.com", page_url="https://dndbeyond.com/a"),
    entry(5, source_kind="link", credit_domain="dndbeyond.com", page_url="https://dndbeyond.com/b"),
    entry(-1, "Pack beast", source="Frost Pack homebrew, v2"),
]


class Grouping(unittest.TestCase):
    def test_each_place_is_its_own_group_in_reading_order(self) -> None:
        out = homebrew_groups.group(MIX)
        self.assertEqual(
            [(g.kind, g.title, len(g.entries)) for g in out],
            [
                ("typed", "Typed in Discord", 1),
                ("upload", "Upload, by Sam", 1),
                ("upload", "Upload, credited to example.com", 1),
                ("link", "Linked from dndbeyond.com", 2),
                ("pack", "Pack: Frost Pack homebrew, v2", 1),
            ],
        )

    def test_a_linked_group_lists_each_page_once(self) -> None:
        link = next(g for g in homebrew_groups.group(MIX) if g.kind == "link")
        self.assertEqual(link.pages, ("https://dndbeyond.com/a", "https://dndbeyond.com/b"))

    def test_a_download_is_exactly_that_groups_entries_and_loads_again(self) -> None:
        link = next(g for g in homebrew_groups.group(MIX) if g.kind == "link")
        name, text = homebrew_groups.download("Frostmaiden", "2024", link)
        self.assertTrue(name.startswith("homebrew-"))
        self.assertEqual(
            sorted(e.name for e in homebrew_file.parse(text).entries), ["Beast 4", "Beast 5"]
        )

    def test_nothing_is_nothing(self) -> None:
        self.assertEqual(homebrew_groups.group([]), [])


class TheList(unittest.IsolatedAsyncioTestCase):
    def it(self, user: int = DM) -> Any:
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

    async def asyncSetUp(self) -> None:
        own = [e for e in MIX if not e.source]
        packs = [e for e in MIX if e.source]
        self.bot = SimpleNamespace(
            campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign())),
            homebrew=SimpleNamespace(list=AsyncMock(return_value=own)),
            homebrew_reader=SimpleNamespace(list=AsyncMock(return_value=[*own, *packs])),
        )

    async def test_the_list_shows_every_source_and_the_pages(self) -> None:
        it = self.it()
        await ui.open_sources(it, campaign())
        text = it.followup.send.await_args.args[0]
        for line in ("Typed in Discord", "Linked from dndbeyond.com", "Pack: Frost Pack"):
            self.assertIn(line, text)
        self.assertIn("<https://dndbeyond.com/a>", text)
        view = it.followup.send.await_args.kwargs["view"]
        self.assertEqual(len(view.pick.options), 5)
        self.assertTrue(all(len(o.label) <= ui.LABEL_MAX for o in view.pick.options))

    async def test_a_download_gives_only_that_sources_entries(self) -> None:
        it = self.it()
        await ui.open_sources(it, campaign())
        view = it.followup.send.await_args.kwargs["view"]
        view.pick._values = ["3"]  # the linked site
        press = self.it()
        await view.pick.callback(press)
        sent = press.followup.send.await_args.kwargs["file"]
        names = [e.name for e in homebrew_file.parse(sent.fp.read().decode()).entries]
        self.assertEqual(sorted(names), ["Beast 4", "Beast 5"])

    async def test_a_press_by_someone_who_is_not_a_dm_sends_no_file(self) -> None:
        # Opened by the DM, then the DM is no longer one when the menu is pressed.
        it = self.it()
        await ui.open_sources(it, campaign())
        view = it.followup.send.await_args.kwargs["view"]
        view.pick._values = ["0"]
        press = self.it(PLAYER)
        press.client.campaigns = SimpleNamespace(get=AsyncMock(return_value=campaign()))
        await view.pick.callback(press)
        sent = [c for c in press.followup.send.await_args_list if "file" in c.kwargs]
        self.assertEqual(sent, [])

    async def test_a_book_entry_gets_a_note_and_more_than_25_sources_are_said(self) -> None:
        book = entry(9, "Book beast", source_kind="upload", from_book=True)
        text = ui._text(homebrew_groups.group([book]))
        self.assertIn(ui.BOOK_NOTE, text)
        many = [
            entry(100 + i, source_kind="link", credit_domain=f"site{i}.example.com")
            for i in range(30)
        ]
        groups = homebrew_groups.group(many)
        self.assertEqual(len(groups), homebrew_groups.MAX_SOURCES)
        text = ui._text(groups)
        self.assertIn(ui.CUT_NOTE, text)
        self.assertLessEqual(len(text), 2000)

    async def test_an_empty_homebrew_says_so(self) -> None:
        self.bot.homebrew.list.return_value = []
        self.bot.homebrew_reader.list.return_value = []
        it = self.it()
        await ui.open_sources(it, campaign())
        self.assertEqual(it.followup.send.await_args.args[0], ui.NONE_YET)

    async def test_a_source_emptied_meanwhile_is_told(self) -> None:
        it = self.it()
        await ui.open_sources(it, campaign())
        view = it.followup.send.await_args.kwargs["view"]
        self.bot.homebrew.list.return_value = [MIX[0]]
        self.bot.homebrew_reader.list.return_value = [MIX[0]]
        view.pick._values = ["3"]
        press = self.it()
        await view.pick.callback(press)
        self.assertEqual(press.followup.send.await_args.args[0], ui.GONE)

    async def test_the_menu_button_is_for_dms_only(self) -> None:
        from dmbot.ui import homebrew as hb

        menu = hb.ListMenu(campaign(), MIX[:1], 0, [])
        button = next(c for c in menu.children if getattr(c, "label", "") == ui.SOURCES_LABEL)
        self.assertIsInstance(button, discord.ui.Button)
