"""One private message for the questions after a session (#3210): the list, the order, one
button per question, and the fall-back to the DM screen. No database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord

from dmbot.bot import NO_PRIVATE_MESSAGE, DMBot
from dmbot.dm_questions import CLAIMS, FAMILY, NAMES, Ask, combine
from tests.asking import with_asking


def make(kind: str, label: str) -> Ask:
    def button() -> discord.ui.Item[Any]:
        return discord.ui.Button(label=label, custom_id=f"test:{kind}")

    def screen_button() -> discord.ui.Item[Any]:
        return discord.ui.Button(label=label, custom_id=f"screen:{kind}")

    return Ask(
        kind, f"{kind} alone", f"{kind} line", button, screen_button, f"{kind} on the screen"
    )


def ids(view: discord.ui.View) -> list[str | None]:
    """The custom ids of a view's buttons, in order."""
    return [getattr(child, "custom_id", None) for child in view.children]


class Combining(unittest.TestCase):
    def test_nothing_to_ask_is_no_message(self) -> None:
        self.assertIsNone(combine([]))

    def test_one_question_keeps_its_own_words_and_button(self) -> None:
        combined = combine([make(CLAIMS, "Check")])
        assert combined is not None
        text, view = combined
        self.assertEqual(text, "claims alone")
        self.assertEqual(len(view.children), 1)

    def test_several_become_one_list_in_a_fixed_order_with_a_button_each(self) -> None:
        asks = [make(FAMILY, "Links"), make(NAMES, "Names"), make(CLAIMS, "Said")]
        combined = combine(asks)
        assert combined is not None
        text, view = combined
        self.assertTrue(text.startswith("**3 questions** about your last session:"))
        self.assertLess(text.index("names line"), text.index("claims line"))
        self.assertLess(text.index("claims line"), text.index("family line"))
        self.assertEqual(ids(view), ["test:names", "test:claims", "test:family"])


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
        self.assertEqual([ids(v)[0] for v in views], ["screen:names", "screen:claims"])

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


class Live(unittest.IsolatedAsyncioTestCase):
    """`DMBot.ask_live` and the copies of a live question (#3210)."""

    def bot(self, reach: dict[int, bool]) -> Any:
        campaign = SimpleNamespace(name="The *Table*", owner_user_id=8, dm_user_ids=set(reach))
        order: list[int] = []

        async def dm_message(user_id: int, text: str, view: Any) -> Any:
            order.append(user_id)
            if not reach[user_id]:
                return None
            return SimpleNamespace(id=100 + user_id, text=text, edit=AsyncMock())

        return SimpleNamespace(
            campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
            _dm_message=dm_message,
            post_message=AsyncMock(return_value=SimpleNamespace(id=1, edit=AsyncMock())),
            order=order,
        )

    def table(self) -> Any:
        return SimpleNamespace(
            guild_id=1,
            campaign_id="c" * 32,
            screen_channel_id=9,
            question_messages=[],
            closed_copies={},
        )

    async def ask(self, me: Any) -> list[Any]:
        make_view: Any = lambda: discord.ui.View(timeout=None)  # noqa: E731
        return await DMBot.ask_live(cast(Any, me), self.table(), "Did they mean…?", make_view)

    async def test_every_dm_who_can_be_reached_gets_it_the_owner_first(self) -> None:
        me = self.bot({7: True, 8: True, 9: False})
        messages = await self.ask(me)
        self.assertEqual(me.order, [8, 7, 9])  # the owner (8) first, then by id
        self.assertEqual([m.id for m in messages], [108, 107])  # the two who could be reached
        for message in messages:
            self.assertTrue(message.text.startswith("❓ **DMbot has a question** · "))
            self.assertIn("\\*Table\\*", message.text)
        me.post_message.assert_not_awaited()  # nothing on the screen

    async def test_a_dm_who_fails_does_not_stop_the_others(self) -> None:
        me = self.bot({7: True, 8: True})
        real = me._dm_message

        async def flaky(user_id: int, text: str, view: Any) -> Any:
            if user_id == 8:
                raise RuntimeError("boom")
            return await real(user_id, text, view)

        me._dm_message = flaky
        with self.assertLogs("dmbot.bot", level="ERROR"):
            messages = await self.ask(me)
        self.assertEqual([m.id for m in messages], [107])

    async def test_when_no_dm_can_be_reached_it_goes_on_the_screen_with_the_reason(self) -> None:
        me = self.bot({7: False})
        messages = await self.ask(me)
        self.assertEqual(len(messages), 1)
        text = me.post_message.await_args.args[1]
        self.assertEqual(text, "Did they mean…?" + NO_PRIVATE_MESSAGE)

    async def test_nowhere_to_post_is_an_empty_list(self) -> None:
        me = self.bot({7: False})
        me.post_message = AsyncMock(return_value=None)
        self.assertEqual(await self.ask(me), [])

    async def test_answering_in_one_dm_edits_the_other_copy_but_not_the_answerers(self) -> None:
        me = self.bot({7: True, 8: True})
        table = self.table()
        mine, theirs = await self.ask(me)
        table.question_messages = [mine, theirs]
        me._session_table = lambda guild_id, question_id: table
        DMBot._retire_copies(cast(Any, me), table, "q1")  # the question was answered
        self.assertEqual(table.question_messages, [])
        await DMBot.finish_copies(cast(Any, me), 1, "q1", mine.id, "Saved: Maren.")
        mine.edit.assert_not_awaited()  # the answerer's own copy keeps its Undo
        theirs.edit.assert_awaited_once_with(content="Saved: Maren.", view=None)

    async def test_without_the_pressed_message_nothing_is_touched(self) -> None:
        me = self.bot({7: True, 8: True})
        table = self.table()
        copies = await self.ask(me)
        table.question_messages = copies
        me._session_table = lambda guild_id, question_id: table
        DMBot._retire_copies(cast(Any, me), table, "q1")
        await DMBot.finish_copies(cast(Any, me), 1, "q1", None, "Saved.")
        for copy in copies:
            copy.edit.assert_not_awaited()  # so Undo is never taken away by mistake

    async def test_a_question_that_runs_out_edits_every_copy(self) -> None:
        me = self.bot({7: True, 8: True})
        table = self.table()
        copies = await self.ask(me)
        table.question_messages = copies
        await DMBot._close_question(cast(Any, me), table, "Nobody answered.")
        for copy in copies:
            copy.edit.assert_awaited_once_with(content="Nobody answered.", view=None)
        self.assertEqual(table.question_messages, [])


if __name__ == "__main__":
    unittest.main()
