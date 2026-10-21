"""📥 Import sessions and `/dmbot import` (#2154): the same private link from both, DMs only, only
where importing is on for the server, and a plain line when there is no website address."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord

from dmbot import imports_switch
from dmbot.dm_screen import import_button as ib
from dmbot.dm_screen.settings import settings_view
from dmbot.ui import import_command
from tests.test_dm_screen_settings import CAMPAIGN, DM, GUILD, PLAYER, campaign

SITE = "https://dmbot.example"


def press(user: int, *, site: str = SITE, found: Any = "yes", dms: tuple[int, ...] = (DM,)) -> Any:
    here = SimpleNamespace(id=CAMPAIGN, guild_id=GUILD, name="Frost", dm_user_ids=set(dms))
    store = SimpleNamespace(
        get=AsyncMock(return_value=here if found == "yes" else None),
        list_campaigns=AsyncMock(return_value=[here]),
    )
    return SimpleNamespace(
        client=SimpleNamespace(campaigns=store, settings=SimpleNamespace(site_url=site)),
        guild=SimpleNamespace(id=GUILD),
        guild_id=GUILD,
        user=SimpleNamespace(id=user),
        response=SimpleNamespace(send_message=AsyncMock()),
    )


def told(it: Any) -> tuple[str, Any]:
    call = it.response.send_message.await_args
    return call.args[0], call.kwargs.get("view")


def link_of(view: Any) -> tuple[str, str]:
    (button,) = view.children
    return str(button.url), str(button.label)


class WithTheSwitch(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        ib.use_switch(lambda guild_id: imports_switch.enabled(False, frozenset({GUILD}), guild_id))
        self.addCleanup(ib.use_switch, ib._off)


class TheReply(WithTheSwitch):
    async def test_the_button_and_the_command_give_the_same_link(self) -> None:
        a, b = press(DM), press(DM)
        await ib.ImportButton(CAMPAIGN).callback(a)
        await import_command.dmbot_import.callback(b)
        (text_a, view_a), (text_b, view_b) = told(a), told(b)
        self.assertEqual(text_a, ib.LINE)
        self.assertEqual((text_a, link_of(view_a)), (text_b, link_of(view_b)))
        self.assertEqual(link_of(view_a)[0], f"{SITE}/account/import/{GUILD}.{CAMPAIGN}")
        self.assertTrue(a.response.send_message.await_args.kwargs["ephemeral"])
        self.assertTrue(b.response.send_message.await_args.kwargs["ephemeral"])

    async def test_a_player_gets_the_dm_line_from_both_and_no_link(self) -> None:
        for call in (
            lambda it: ib.ImportButton(CAMPAIGN).callback(it),
            lambda it: import_command.dmbot_import.callback(it),
        ):
            it = press(PLAYER)
            await call(it)
            self.assertEqual(told(it), (ib.ONLY_DM, None))

    async def test_switched_off_for_this_server_it_says_so(self) -> None:
        ib.use_switch(lambda guild_id: False)
        for call in (
            lambda it: ib.ImportButton(CAMPAIGN).callback(it),
            lambda it: import_command.dmbot_import.callback(it),
        ):
            it = press(DM)
            await call(it)
            self.assertEqual(told(it), (ib.NOT_ON, None))
        other = press(PLAYER)  # a player is told nothing about the switch
        await ib.ImportButton(CAMPAIGN).callback(other)
        self.assertEqual(told(other)[0], ib.ONLY_DM)

    async def test_without_a_website_address_there_is_no_link(self) -> None:
        for site in ("", "http://dmbot.example", "not a link"):
            it = press(DM, site=site)
            await ib.ImportButton(CAMPAIGN).callback(it)
            self.assertEqual(told(it), (ib.NO_SITE, None), site)

    async def test_a_campaign_that_is_gone_is_said_so(self) -> None:
        it = press(DM, found="no")
        await ib.ImportButton(CAMPAIGN).callback(it)
        self.assertEqual(told(it)[0], ib.GONE)

    async def test_a_dm_of_several_campaigns_gets_one_link_for_each(self) -> None:
        it = press(DM)
        other = SimpleNamespace(id="d" * 32, guild_id=GUILD, name="Embers", dm_user_ids={DM})
        mine = SimpleNamespace(id=CAMPAIGN, guild_id=GUILD, name="Frost", dm_user_ids={DM})
        theirs = SimpleNamespace(id="e" * 32, guild_id=GUILD, name="Not mine", dm_user_ids={PLAYER})
        it.client.campaigns.list_campaigns = AsyncMock(return_value=[mine, other, theirs])
        await import_command.dmbot_import.callback(it)
        text, view = told(it)
        self.assertEqual(text, ib.MANY)
        self.assertEqual(
            [(str(b.label), str(b.url)) for b in view.children],
            [
                ("Frost", f"{SITE}/account/import/{GUILD}.{CAMPAIGN}"),
                ("Embers", f"{SITE}/account/import/{GUILD}.{'d' * 32}"),
            ],
        )

    async def test_a_dm_who_lost_the_role_since_the_card_was_drawn_gets_the_dm_line(self) -> None:
        it = press(DM, dms=(PLAYER,))  # the store no longer lists them
        await ib.ImportButton(CAMPAIGN).callback(it)
        self.assertEqual(told(it), (ib.ONLY_DM, None))

    async def test_a_store_that_fails_is_said_plainly_by_both(self) -> None:
        it = press(DM)
        it.client.campaigns.list_campaigns = AsyncMock(side_effect=RuntimeError("x"))
        it.client.campaigns.get = AsyncMock(side_effect=RuntimeError("x"))
        await import_command.dmbot_import.callback(it)
        await ib.ImportButton(CAMPAIGN).callback(it)
        sent = [c.args[0] for c in it.response.send_message.await_args_list]
        self.assertEqual(sent, [ib.FAILED, ib.FAILED])

    async def test_the_command_takes_no_options_and_no_file(self) -> None:
        self.assertEqual(list(import_command.dmbot_import.parameters), [])


class TheCard(WithTheSwitch):
    def labels(self, view: discord.ui.View) -> list[str]:
        return [
            str(getattr(i.item if hasattr(i, "item") else i, "label", "")) for i in view.children
        ]

    def test_the_button_is_on_the_dms_card_only_where_importing_is_on(self) -> None:
        self.assertIn(ib.LABEL, self.labels(settings_view(campaign(), None, DM)))
        self.assertNotIn(ib.LABEL, self.labels(settings_view(campaign(), None, PLAYER)))
        ib.use_switch(lambda guild_id: False)
        self.assertNotIn(ib.LABEL, self.labels(settings_view(campaign(), None, DM)))

    def test_its_id_matches_its_template_and_fits_a_phone(self) -> None:
        item = ib.ImportButton(CAMPAIGN)
        self.assertLessEqual(len(str(item.item.label)), 25)
        template = type(item).__discord_ui_compiled_template__
        self.assertIsNotNone(template.fullmatch(str(item.item.custom_id)))

    def test_the_page_address_comes_from_the_site_setting_alone(self) -> None:
        got = ib.page_url(" https://dmbot.example/some/path ", 5, CAMPAIGN)
        self.assertEqual(got, f"{SITE}/account/import/5.{CAMPAIGN}")
        self.assertEqual(ib.page_url("", 5, CAMPAIGN), "")
        self.assertEqual(ib.page_url("http://x.example", 5, CAMPAIGN), "")


if __name__ == "__main__":
    unittest.main()
