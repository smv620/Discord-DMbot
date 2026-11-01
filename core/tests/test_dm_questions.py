"""One private message for the questions after a session (#3210): the list, the order, one
button per question, and the fall-back to the DM screen. No database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord

from dmbot.bot import NO_PRIVATE_MESSAGE, DMBot
from dmbot.dm_questions import CLAIMS, FAMILY, NAMES, Ask, combine
from tests.asking import with_asking


def make(kind: str, label: str) -> Ask:
    def button() -> discord.ui.Item[discord.ui.View]:
        return discord.ui.Button(label=label, custom_id=f"test:{kind}")

    def screen_button() -> discord.ui.Item[discord.ui.View]:
        return discord.ui.Button(label=label, custom_id=f"screen:{kind}")

    return Ask(
        kind, f"{kind} alone", f"{kind} line", button, screen_button, f"{kind} on the screen"
    )


class Combining(unittest.TestCase):
    def test_nothing_to_ask_is_no_message(self) -> None:
        self.assertIsNone(combine([]))

    def test_one_question_keeps_its_own_words_and_button(self) -> None:
        text, view = combine([make(CLAIMS, "Check")]) or ("", None)
        self.assertEqual(text, "claims alone")
        self.assertEqual(len(view.children), 1)

    def test_several_become_one_list_in_a_fixed_order_with_a_button_each(self) -> None:
        asks = [make(FAMILY, "Links"), make(NAMES, "Names"), make(CLAIMS, "Said")]
        text, view = combine(asks) or ("", None)
        self.assertTrue(text.startswith("**3 questions** about your last session:"))
        self.assertLess(text.index("names line"), text.index("claims line"))
        self.assertLess(text.index("claims line"), text.index("family line"))
        self.assertEqual([c.custom_id for c in view.children], [  # type: ignore[attr-defined]
            "test:names", "test:claims", "test:family",
        ])  # fmt: skip


class Sending(unittest.IsolatedAsyncioTestCase):
    def bot(self, reachable: bool) -> Any:
        return with_asking(
            SimpleNamespace(
                post=AsyncMock(return_value=True),
                ask_privately=AsyncMock(return_value=reachable),
            )
        )

    def table(self, collecting: bool) -> Any:
        return SimpleNamespace(
            guild_id=1, campaign_id="c" * 32, screen_channel_id=9, asks=[] if collecting else None
        )

    async def test_collected_questions_wait_and_go_out_as_one_message(self) -> None:
        me, table = self.bot(True), self.table(True)
        await DMBot.ask_dm(me, table, make(NAMES, "Names"))
        await DMBot.ask_dm(me, table, make(CLAIMS, "Said"))
        me.ask_privately.assert_not_awaited()  # still collecting
        await DMBot.send_collected_asks(me, table)
        me.ask_privately.assert_awaited_once()
        self.assertIn("2 questions", me.ask_privately.await_args.args[2])
        me.post.assert_not_awaited()
        self.assertIsNone(table.asks)  # nothing is sent twice; later questions go at once

    async def test_not_collecting_sends_the_question_at_once(self) -> None:
        me, table = self.bot(True), self.table(False)
        await DMBot.ask_dm(me, table, make(NAMES, "Names"))
        me.ask_privately.assert_awaited_once()
        self.assertEqual(me.ask_privately.await_args.args[2], "names alone")

    async def test_when_no_dm_can_be_reached_each_goes_on_the_screen_with_the_reason(self) -> None:
        me, table = self.bot(False), self.table(True)
        await DMBot.ask_dm(me, table, make(CLAIMS, "Said"))
        await DMBot.ask_dm(me, table, make(NAMES, "Names"))
        await DMBot.send_collected_asks(me, table)
        texts = [call.args[1] for call in me.post.await_args_list]
        self.assertEqual(texts, ["names on the screen" + NO_PRIVATE_MESSAGE,
                                 "claims on the screen" + NO_PRIVATE_MESSAGE])  # fmt: skip
        views = [call.kwargs["view"] for call in me.post.await_args_list]
        self.assertTrue(all(len(v.children) == 1 for v in views))  # a fresh button each
        # the screen's own buttons, not the private chat's
        self.assertEqual(
            [v.children[0].custom_id for v in views], ["screen:names", "screen:claims"]
        )  # type: ignore[attr-defined]

    async def test_one_failed_post_does_not_skip_the_questions_after_it(self) -> None:
        me, table = self.bot(False), self.table(True)
        me.post = AsyncMock(side_effect=[RuntimeError("boom"), True])
        await DMBot.ask_dm(me, table, make(NAMES, "Names"))
        await DMBot.ask_dm(me, table, make(CLAIMS, "Said"))
        with self.assertLogs("dmbot.bot", level="ERROR"):
            await DMBot.send_collected_asks(me, table)
        self.assertEqual(me.post.await_count, 2)  # both were tried

    async def test_a_failed_send_keeps_the_questions_for_the_final_retry(self) -> None:
        me, table = self.bot(True), self.table(True)
        me.ask_privately = AsyncMock(side_effect=RuntimeError("boom"))
        await DMBot.ask_dm(me, table, make(NAMES, "Names"))
        with self.assertRaises(RuntimeError):
            await DMBot.send_collected_asks(me, table)
        self.assertEqual(len(table.asks), 1)  # still there, for the `finally` in `_after_session`

    async def test_after_sending_a_later_question_goes_out_at_once(self) -> None:
        me, table = self.bot(True), self.table(True)
        await DMBot.send_collected_asks(me, table)
        self.assertIsNone(table.asks)
        await DMBot.ask_dm(me, table, make(FAMILY, "Links"))
        me.ask_privately.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
