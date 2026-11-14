"""The "New campaign" choice (#3955, PR 4): with the start-up wizard's switch on and a website
address, the buttons offer the wizard link first and keep the short form one press away; with it
off, or with no website, the short form opens exactly as before. Invented ids, no database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import discord

from dmbot.ui import dm_links, dmbot_commands

SITE = "https://dmbot.example.com"
GUILD = 111


def interaction(*, switch: bool, guild: bool = True) -> Any:
    response = SimpleNamespace(
        send_modal=AsyncMock(), send_message=AsyncMock(), is_done=lambda: False
    )
    return SimpleNamespace(
        client=SimpleNamespace(settings=SimpleNamespace(wizard_link_on=switch)),
        guild=SimpleNamespace(id=GUILD) if guild else None,
        response=response,
    )


class WizardLink(unittest.TestCase):
    def test_wizard_link_is_the_new_page_for_the_server(self) -> None:
        self.assertEqual(
            dm_links.wizard_link(SITE, GUILD), "https://dmbot.example.com/new?server=111"
        )
        self.assertEqual(dm_links.wizard_link("", GUILD), "")
        self.assertEqual(dm_links.wizard_link("http://dmbot.example.com", GUILD), "")


class OpenNewCampaign(unittest.IsolatedAsyncioTestCase):
    async def test_with_the_switch_off_the_short_form_opens_as_before(self) -> None:
        me = interaction(switch=False)
        with (
            patch.object(dm_links, "configured_site", return_value=SITE),
            patch.object(dmbot_commands, "_send", AsyncMock()) as send,
        ):
            await dmbot_commands.open_new_campaign(me)
        me.response.send_modal.assert_awaited_once()
        self.assertIsInstance(
            me.response.send_modal.await_args.args[0], dmbot_commands.NewCampaignForm
        )
        send.assert_not_awaited()

    async def test_with_the_switch_on_and_a_site_the_choice_is_offered_link_first(self) -> None:
        me = interaction(switch=True)
        with (
            patch.object(dm_links, "configured_site", return_value=SITE),
            patch.object(dmbot_commands, "_send", AsyncMock()) as send,
        ):
            await dmbot_commands.open_new_campaign(me)
        me.response.send_modal.assert_not_awaited()
        send.assert_awaited_once()
        text, view = send.await_args_list[0].args[1], send.await_args_list[0].args[2]
        self.assertEqual(text, dmbot_commands.NEW_CAMPAIGN_TEXT)
        first, second = view.children
        self.assertIsInstance(first, discord.ui.Button)
        self.assertEqual(first.url, "https://dmbot.example.com/new?server=111")
        self.assertEqual(first.label, "Set up step by step on the website")
        self.assertEqual(second.label, "Use the short form here")

    async def test_a_press_outside_a_server_opens_the_short_form(self) -> None:
        me = interaction(switch=True, guild=False)
        with (
            patch.object(dm_links, "configured_site", return_value=SITE),
            patch.object(dm_links, "wizard_link", AssertionError("not for a DM")),
            patch.object(dmbot_commands, "_send", AsyncMock()) as send,
        ):
            await dmbot_commands.open_new_campaign(me)
        me.response.send_modal.assert_awaited_once()
        send.assert_not_awaited()

    async def test_the_short_form_button_opens_the_form(self) -> None:
        view = dmbot_commands.NewCampaignChoice("https://dmbot.example.com/new?server=111")
        short = next(
            c for c in view.children if getattr(c, "label", "") == "Use the short form here"
        )
        me = interaction(switch=True)
        await cast(Any, short).callback(me)
        me.response.send_modal.assert_awaited_once()
        self.assertIsInstance(
            me.response.send_modal.await_args.args[0], dmbot_commands.NewCampaignForm
        )

    async def test_with_the_switch_on_but_no_site_the_short_form_opens(self) -> None:
        me = interaction(switch=True)
        with (
            patch.object(dm_links, "configured_site", return_value=""),
            patch.object(dmbot_commands, "_send", AsyncMock()) as send,
        ):
            await dmbot_commands.open_new_campaign(me)
        me.response.send_modal.assert_awaited_once()
        send.assert_not_awaited()

    async def test_both_new_campaign_buttons_use_it(self) -> None:
        opened = AsyncMock()
        with patch.object(dmbot_commands, "open_new_campaign", opened):
            welcome = dmbot_commands.Welcome()
            setup = next(
                c for c in welcome.children if getattr(c, "label", "") == "Set up my first campaign"
            )
            await cast(Any, setup).callback(interaction(switch=False))
            campaigns: Any = [SimpleNamespace(id="a" * 32)]
            picker = dmbot_commands.CampaignPicker(campaigns)
            new = next(c for c in picker.children if getattr(c, "label", "") == "＋ New campaign")
            await cast(Any, new).callback(interaction(switch=False))
        self.assertEqual(opened.await_count, 2)
