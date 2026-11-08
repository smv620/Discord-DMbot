"""The old dmb-chat channel (#3212): one notice, and a button that removes it only for a DM, only
after a confirmation, and only a channel DMbot named like a chat channel."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.bot import DMBot
from dmbot.dm_screen import old_chat

GUILD, CID, CHANNEL, DM, PLAYER = 1, "c" * 32, 700, 10, 20


def campaign(channel_id: int | None = CHANNEL) -> Any:
    return SimpleNamespace(
        id=CID, guild_id=GUILD, dm_user_ids=frozenset({DM}), dmbchat_channel_id=channel_id
    )


def press_on(user: int = DM, name: str = "dmb-chat-red", exists: bool = True) -> tuple[Any, Any]:
    channel = SimpleNamespace(name=name, delete=AsyncMock())
    guild = SimpleNamespace(id=GUILD, get_channel=lambda _id: channel if exists else None)
    campaigns = SimpleNamespace(
        get=AsyncMock(return_value=campaign()), set_dmbchat_channel=AsyncMock()
    )
    bot = SimpleNamespace(campaigns=campaigns)
    done = {"v": False}
    it = MagicMock()
    it.client, it.guild, it.user = bot, guild, SimpleNamespace(id=user)
    it.response.is_done = lambda: done["v"]
    it.response.send_message = AsyncMock()
    it.response.edit_message = AsyncMock()
    it.channel_obj = channel
    return it, bot


class Pressing(unittest.IsolatedAsyncioTestCase):
    async def test_remove_asks_first_and_deletes_nothing(self) -> None:
        it, _bot = press_on()
        await old_chat.press(it, CID, "ask")
        it.channel_obj.delete.assert_not_awaited()
        text = it.response.send_message.await_args.args[0]
        self.assertIn("for good", text)
        self.assertIn("view", it.response.send_message.await_args.kwargs)

    async def test_yes_deletes_it_and_forgets_it(self) -> None:
        it, bot = press_on()
        await old_chat.press(it, CID, "yes")
        it.channel_obj.delete.assert_awaited_once()
        bot.campaigns.set_dmbchat_channel.assert_awaited_once_with(GUILD, CID, None)
        self.assertEqual(it.response.edit_message.await_args.kwargs["content"], old_chat.DONE)

    async def test_keep_changes_nothing(self) -> None:
        it, bot = press_on()
        await old_chat.press(it, CID, "keep")
        it.channel_obj.delete.assert_not_awaited()
        bot.campaigns.set_dmbchat_channel.assert_not_awaited()

    async def test_a_player_may_do_none_of_it(self) -> None:
        for act in ("ask", "yes", "keep"):
            it, _bot = press_on(user=PLAYER)
            await old_chat.press(it, CID, act)
            it.channel_obj.delete.assert_not_awaited()
            self.assertEqual(it.response.send_message.await_args.args[0], old_chat.ONLY_DMS)

    async def test_only_a_channel_dmbot_named_is_ever_deleted(self) -> None:
        it, _bot = press_on(name="general")
        await old_chat.press(it, CID, "yes")
        it.channel_obj.delete.assert_not_awaited()
        self.assertEqual(it.response.send_message.await_args.args[0], old_chat.NOT_OURS)

    async def test_a_channel_that_is_gone_is_forgotten(self) -> None:
        it, bot = press_on(exists=False)
        await old_chat.press(it, CID, "ask")
        bot.campaigns.set_dmbchat_channel.assert_awaited_once_with(GUILD, CID, None)

    async def test_no_permission_leaves_it_and_says_so(self) -> None:
        it, bot = press_on()
        it.channel_obj.delete = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "no"))
        await old_chat.press(it, CID, "yes")
        bot.campaigns.set_dmbchat_channel.assert_not_awaited()
        self.assertEqual(it.response.edit_message.await_args.kwargs["content"], old_chat.CANT)


class TheNotice(unittest.IsolatedAsyncioTestCase):
    def bot(self, exists: bool = True) -> Any:
        me = SimpleNamespace(
            _old_chat_told=set(),
            post=AsyncMock(),
            campaigns=SimpleNamespace(set_dmbchat_channel=AsyncMock()),
        )
        guild = SimpleNamespace(id=GUILD, get_channel=lambda _id: object() if exists else None)
        return me, guild

    async def test_it_is_told_once_with_the_button(self) -> None:
        me, guild = self.bot()
        for _ in range(2):
            await DMBot._old_chat_notice(me, guild, campaign(), 55)
        me.post.assert_awaited_once()
        self.assertIn(f"<#{CHANNEL}>", me.post.await_args.args[1])
        self.assertIn("will not delete it by itself", me.post.await_args.args[1])

    async def test_a_campaign_without_an_old_channel_is_told_nothing(self) -> None:
        me, guild = self.bot()
        await DMBot._old_chat_notice(me, guild, campaign(None), 55)
        me.post.assert_not_awaited()

    async def test_a_channel_that_is_not_there_is_not_mentioned_and_not_forgotten(self) -> None:
        me, guild = self.bot(exists=False)
        await DMBot._old_chat_notice(me, guild, campaign(), 55)
        me.post.assert_not_awaited()
        # (a cache miss is not proof it is gone: the stored id stays; the button forgets it)
        me.campaigns.set_dmbchat_channel.assert_not_awaited()


class TheWords(unittest.TestCase):
    def test_the_notice_names_the_channel_and_promises_no_deletion(self) -> None:
        text = old_chat.notice_text(555)
        self.assertIn("<#555>", text)
        self.assertIn("is still there", text)
        self.assertIn("will not delete it by itself", text)

    def test_the_notice_has_no_jargon(self) -> None:
        text = old_chat.notice_text(555)
        self.assertNotIn("dmb-chat", text)
        self.assertNotIn("webhook", text.casefold())

    def test_the_confirmation_names_the_channel_and_warns(self) -> None:
        text = old_chat.confirm_text("dmb-chat-red")
        self.assertIn("**dmb-chat-red**", text)
        self.assertIn("for good", text)
        self.assertIn("can't be undone", text)

    def test_a_channel_name_cannot_break_the_markdown(self) -> None:
        self.assertIn(r"a\*b\_c", old_chat.confirm_text("a*b_c"))
        self.assertNotIn("a*b_c", old_chat.confirm_text("a*b_c"))

    def test_both_are_short(self) -> None:
        self.assertLess(len(old_chat.notice_text(555)), 600)
        self.assertLess(len(old_chat.confirm_text("x" * 100)), 600)


if __name__ == "__main__":
    unittest.main()
