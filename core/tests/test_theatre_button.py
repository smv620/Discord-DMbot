"""The DM's "Make a script" question and button (#3244, 5c): offered only when the switch and the
plan allow it, usable by a DM only, and it says what it costs before it spends anything. Invented
ids."""

from __future__ import annotations

import re
import unittest
from functools import partial
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.dm_questions import SCRIPT
from dmbot.ui import theatre_button as tb

BOT: Any = DMBot  # (its methods are called on a stand-in)
DM, PLAYER, GUILD, SESSION = 7, 8, 5, "a" * 32


def bot(*, on: bool = True, plan: bool = True, lines: int = 300) -> Any:
    me = SimpleNamespace(
        settings=SimpleNamespace(theatre_on=on),
        theatre_ai=object() if on else None,
        memory=object(),
        transcripts=SimpleNamespace(
            session=AsyncMock(return_value=SimpleNamespace(lines=lines, campaign_id="c" * 32))
        ),
        campaigns=SimpleNamespace(
            get=AsyncMock(
                return_value=SimpleNamespace(dm_user_ids=frozenset({DM}), dm_screen_channel_id=9)
            )
        ),
        plan_allows=AsyncMock(return_value=plan),
        ask_dm=AsyncMock(),
    )
    me.offer_script = partial(BOT.offer_script, me)
    return me


def table() -> Any:
    return SimpleNamespace(guild_id=GUILD, campaign_id="c" * 32, transcript_session_id=SESSION)


def pressed(me: Any, user: int) -> Any:
    return SimpleNamespace(
        user=SimpleNamespace(id=user),
        client=me,
        response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


class TheOffer(unittest.IsolatedAsyncioTestCase):
    async def test_it_is_offered_with_the_switch_and_the_plan(self) -> None:
        me = bot()
        await me.offer_script(table())
        me.ask_dm.assert_awaited_once()
        ask = me.ask_dm.await_args.args[1]
        self.assertEqual(ask.kind, SCRIPT)
        self.assertIsInstance(ask.button(), tb.ScriptButton)

    async def test_it_is_hidden_when_the_switch_is_off(self) -> None:
        me = bot(on=False)
        await me.offer_script(table())
        me.ask_dm.assert_not_awaited()

    async def test_it_is_hidden_when_the_plan_does_not_allow_it_or_cannot_be_checked(self) -> None:
        me = bot(plan=False)
        await me.offer_script(table())
        me.ask_dm.assert_not_awaited()
        self.assertEqual(me.plan_allows.await_args.kwargs, {"fail_open": False})

    async def test_a_session_with_no_lines_gets_no_offer(self) -> None:
        me = bot(lines=0)
        await me.offer_script(table())
        me.ask_dm.assert_not_awaited()

    def test_the_button_knows_its_server_and_session_after_a_restart(self) -> None:
        button = tb.ScriptButton(GUILD, SESSION)
        custom_id = button.item.custom_id
        assert custom_id is not None
        match = re.fullmatch(tb.ScriptButton.__discord_ui_compiled_template__.pattern, custom_id)
        assert match is not None
        self.assertEqual((int(match["guild"]), match["session"]), (GUILD, SESSION))


class ThePress(unittest.IsolatedAsyncioTestCase):
    def said(self, interaction: Any) -> str:
        return str(interaction.response.send_message.await_args.args[0])

    async def test_a_dm_sees_what_it_costs_and_a_confirm_step(self) -> None:
        me = bot(lines=300)
        interaction = pressed(me, DM)
        await tb.press(interaction, GUILD, SESSION)
        text = self.said(interaction)
        self.assertIn("about 2 AI calls", text)
        self.assertIn("only the DMs can read", text)
        view = interaction.response.send_message.await_args.kwargs["view"]
        self.assertEqual([c.label for c in view.children], [tb.CONFIRM_YES, tb.CONFIRM_NO])
        self.assertTrue(await view.interaction_check(pressed(me, DM)))
        self.assertFalse(await view.interaction_check(pressed(me, PLAYER)))

    async def test_a_player_is_refused_even_when_the_screen_shows_the_button(self) -> None:
        me = bot()
        interaction = pressed(me, PLAYER)
        await tb.press(interaction, GUILD, SESSION)
        self.assertEqual(self.said(interaction), tb.ONLY_DMS)

    async def test_a_stale_card_with_the_switch_off_or_no_plan_says_why(self) -> None:
        for me, words in ((bot(on=False), tb.OFF), (bot(plan=False), tb.NO_PLAN)):
            interaction = pressed(me, DM)
            await tb.press(interaction, GUILD, SESSION)
            self.assertEqual(self.said(interaction), words)

    async def test_a_session_that_is_gone_says_so(self) -> None:
        me = bot()
        me.transcripts.session = AsyncMock(return_value=None)
        interaction = pressed(me, DM)
        await tb.press(interaction, GUILD, SESSION)
        self.assertEqual(self.said(interaction), tb.GONE)
