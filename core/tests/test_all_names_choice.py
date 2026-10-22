"""The last choice of a cut-short name menu, "🌐 All names on the website…" (#2294): when it is
there, what it does, and that it never costs a name its place when nothing was left out."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord

from dmbot.memory.lookup import CampaignLookup
from dmbot.ui import all_names, name_lists
from dmbot.ui import names as names_ui
from tests.test_memory_names import GUILD, NamesTest


def options(count: int) -> list[discord.SelectOption]:
    return [discord.SelectOption(label=f"Name {n}", value=f"e{n}") for n in range(count)]


class TheOptions(unittest.TestCase):
    def test_nothing_left_out_adds_nothing(self) -> None:
        self.assertEqual(len(all_names.with_choice(options(3), left_out=False)), 3)
        self.assertEqual(len(all_names.with_choice(options(25), left_out=False)), 25)

    def test_names_left_out_end_the_menu_with_the_choice_in_a_slot_of_its_own(self) -> None:
        got = all_names.with_choice(options(10), left_out=True)
        self.assertEqual(len(got), 11)
        self.assertEqual(got[-1].value, all_names.VALUE)
        self.assertEqual(got[-1].label, "🌐 All names on the website…")
        full = all_names.with_choice(options(40), left_out=True)
        self.assertEqual(len(full), 25)  # Discord's limit: 24 names and the choice
        self.assertEqual(full[-1].value, all_names.VALUE)
        self.assertEqual(full[-2].value, "e23")

    def test_a_menu_too_long_gets_the_choice_even_if_the_caller_forgot(self) -> None:
        got = all_names.with_choice(options(30), left_out=False)
        self.assertEqual((len(got), got[-1].value), (25, all_names.VALUE))

    def test_the_address_needs_https_and_names_the_campaign(self) -> None:
        url = all_names.page_url("https://dmbot.example.com/", 42, "a" * 32)
        self.assertEqual(url, f"https://dmbot.example.com/account/names/42.{'a' * 32}")
        self.assertEqual(all_names.page_url("http://dmbot.example.com", 42, "a"), "")
        self.assertEqual(all_names.page_url("", 42, "a"), "")


class TheReply(unittest.IsolatedAsyncioTestCase):
    def press(self, site: str) -> Any:
        response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
        return SimpleNamespace(
            client=SimpleNamespace(settings=SimpleNamespace(site_url=site)),
            guild_id=GUILD,
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_it_answers_privately_with_one_link_button(self) -> None:
        it = self.press("https://dmbot.example.com")
        await all_names.reply(it, "c" * 32)
        kw = it.response.send_message.await_args.kwargs
        self.assertTrue(kw["ephemeral"])
        (button,) = kw["view"].children
        self.assertEqual(button.url, f"https://dmbot.example.com/account/names/{GUILD}.{'c' * 32}")
        self.assertIn("Open all names", button.label)

    async def test_with_no_website_address_it_says_so(self) -> None:
        it = self.press("")
        await all_names.reply(it, "c" * 32)
        args = it.response.send_message.await_args
        self.assertEqual(args.args[0], all_names.NO_SITE)

    async def test_after_a_defer_it_uses_the_follow_up(self) -> None:
        it = self.press("https://dmbot.example.com")
        it.response.is_done = lambda: True
        await all_names.reply(it, "c" * 32)
        it.followup.send.assert_awaited_once()
        it.response.send_message.assert_not_awaited()


class TheMenus(NamesTest):
    async def lookup(self) -> CampaignLookup:
        return CampaignLookup.build(await self.memory.lookup_data(GUILD, self.campaign.id))

    added = 0

    async def add(self, count: int, kind: str = "npc") -> None:
        for _ in range(count):
            self.added += 1
            name = f"Person{self.added:03d}"
            await names_ui.save_name(self.memory, self.campaign, name, kind, [], [])

    async def test_a_short_panel_has_no_choice(self) -> None:
        await self.add(3)
        it = self.it()
        await names_ui.dmbot_names.callback(it)  # type: ignore[call-arg]
        view = it.response.sent[0][1]["view"]
        self.assertNotIn(all_names.VALUE, [o.value for o in view.open.options])

    async def test_a_long_panel_ends_with_the_choice(self) -> None:
        await self.add(30)
        it = self.it()
        await names_ui.dmbot_names.callback(it)  # type: ignore[call-arg]
        view = it.response.sent[0][1]["view"]
        self.assertEqual(view.open.options[-1].value, all_names.VALUE)
        self.assertLessEqual(len(view.open.options), 25)

    async def test_choosing_it_in_the_panel_gives_the_link_and_opens_no_card(self) -> None:
        await self.add(30)
        it = self.it()
        await names_ui.dmbot_names.callback(it)  # type: ignore[call-arg]
        view = it.response.sent[0][1]["view"]
        view.open = SimpleNamespace(values=[all_names.VALUE])
        press = self.it()
        press.client = SimpleNamespace(settings=SimpleNamespace(site_url="https://x.example.com"))
        await view._open(press)
        kw = press.response.sent[0][1]
        self.assertEqual(len(kw["view"].children), 1)

    async def test_browse_by_kind_has_the_choice_when_the_kind_runs_to_more_pages(self) -> None:
        await self.add(5)
        few = name_lists.Browse(self.campaign.id, await self.lookup(), "npc")
        assert few.open is not None
        self.assertNotIn(all_names.VALUE, [o.value for o in few.open.options])
        await self.add(40)  # more than one page
        many = name_lists.Browse(self.campaign.id, await self.lookup(), "npc")
        assert many.open is not None
        self.assertEqual(many.open.options[-1].value, all_names.VALUE)
        self.assertLessEqual(len(many.open.options), 25)
