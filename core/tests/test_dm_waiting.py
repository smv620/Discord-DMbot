"""What waits for the DM after a lore upload or an import: one private message, once (#3314).
Against a real Postgres, with the sending stood in. Invented names and ids."""

from __future__ import annotations

import time
from functools import partial
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.dm_screen import links_ready
from dmbot.dm_waiting import WaitingChecks, WaitingSent, asks_for
from tests.test_import_recaps import GUILD, WithImports

BOT: Any = DMBot  # (its methods are called on a stand-in)


class TheAsks(WithImports):
    def test_one_button_for_each_kind_with_something_waiting_and_none_for_the_rest(self) -> None:
        made: dict[str, Any] = {
            k: (lambda k=k: SimpleNamespace(kind=k))
            for k in ("names", "people", "claims", "connections")
        }
        asks = asks_for(WaitingChecks(names=2, connections=1), made)
        self.assertEqual([a.kind for a in asks], ["names", "connections"])
        self.assertIn("2 names to check", asks[0].alone)
        self.assertIn("1 connection to check", asks[1].line)
        self.assertEqual(asks_for(WaitingChecks(), made), [])
        self.assertNotIn("Tavin", asks[0].alone)  # (counts only)

    async def test_a_key_is_claimed_once(self) -> None:
        sent = WaitingSent(self.db)
        cid = self.campaign.id
        self.assertTrue(await sent.claim(GUILD, cid, "lore:1", "names=1"))
        self.assertFalse(await sent.claim(GUILD, cid, "lore:1"))  # (a restart or a second round)
        self.assertEqual(await sent.offered_of(GUILD, cid, "lore:1"), "names=1")
        await sent.release(GUILD, cid, "lore:1")
        self.assertTrue(await sent.claim(GUILD, cid, "lore:1"))


def fake_bot(db_owner: Any, waiting: list[WaitingChecks], *, private: bool = True) -> Any:
    me = SimpleNamespace(
        memory=object(),
        campaigns=SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(dm_screen_channel_id=77)),
            db=db_owner.db,
        ),
        count_waiting=AsyncMock(side_effect=waiting),
        ask_privately=AsyncMock(return_value=private),
        post=AsyncMock(),
        private_sent=[],
    )
    me._send_asks_for = partial(BOT._send_asks_for, me)
    me.tell_dm_waiting = partial(BOT.tell_dm_waiting, me)
    me._tell_finished_imports = partial(BOT._tell_finished_imports, me)
    return me


class TheMessage(WithImports):
    async def test_a_lore_upload_that_leaves_a_family_link_sends_one_private_message(self) -> None:
        me = fake_bot(self, [WaitingChecks(connections=1)] * 3)
        cid = self.campaign.id
        self.assertTrue(await me.tell_dm_waiting(GUILD, cid, "lore:5", where="your upload"))
        self.assertFalse(await me.tell_dm_waiting(GUILD, cid, "lore:5", where="your upload"))
        me.ask_privately.assert_awaited_once()
        text, view = me.ask_privately.await_args.args[2:]
        self.assertIn("1 connection to check", text)
        self.assertEqual(len(view.children), 1)
        self.assertIsInstance(view.children[0], links_ready.ReadyButton)
        me.post.assert_not_awaited()  # (not on the DM screen when the private chat worked)

    async def test_nothing_waiting_sends_nothing(self) -> None:
        me = fake_bot(self, [WaitingChecks()])
        self.assertFalse(await me.tell_dm_waiting(GUILD, self.campaign.id, "lore:1", where="x"))
        me.ask_privately.assert_not_awaited()
        me.post.assert_not_awaited()

    async def test_a_dm_without_a_private_chat_gets_it_on_the_dm_screen(self) -> None:
        me = fake_bot(self, [WaitingChecks(names=3)], private=False)
        self.assertTrue(await me.tell_dm_waiting(GUILD, self.campaign.id, "lore:1", where="x"))
        me.post.assert_awaited_once()
        self.assertEqual(me.post.await_args.args[0], 77)
        self.assertIn("allow messages", me.post.await_args.args[1].lower())

    async def test_when_it_cannot_be_sent_anywhere_the_key_is_given_back_for_the_next_round(
        self,
    ) -> None:
        me = fake_bot(self, [WaitingChecks(names=1)] * 2, private=False)
        me.campaigns.get = AsyncMock(return_value=SimpleNamespace(dm_screen_channel_id=None))
        self.assertFalse(await me.tell_dm_waiting(GUILD, self.campaign.id, "lore:1", where="x"))
        self.assertFalse(await WaitingSent(self.db).sent(GUILD, self.campaign.id, "lore:1"))

    async def test_two_different_uploads_each_send_and_the_heading_names_the_upload(self) -> None:
        me = fake_bot(
            self, [WaitingChecks(names=1, connections=1), WaitingChecks(names=1, connections=1)]
        )
        cid = self.campaign.id
        self.assertTrue(await me.tell_dm_waiting(GUILD, cid, "lore:1", where="your upload"))
        self.assertTrue(await me.tell_dm_waiting(GUILD, cid, "lore:2", where="your upload"))
        text = me.ask_privately.await_args_list[0].args[2]
        self.assertIn("2 questions** about your upload:", text)

    async def test_a_send_that_raises_gives_the_key_back(self) -> None:
        me = fake_bot(self, [WaitingChecks(names=1)] * 2)
        me.ask_privately = AsyncMock(side_effect=[RuntimeError("down"), True])
        cid = self.campaign.id
        self.assertFalse(await me.tell_dm_waiting(GUILD, cid, "lore:1", where="x"))
        self.assertFalse(await WaitingSent(self.db).sent(GUILD, cid, "lore:1"))
        self.assertTrue(await me.tell_dm_waiting(GUILD, cid, "lore:1", where="x"))  # (next round)

    async def test_a_second_message_leaves_out_what_the_first_already_offered(self) -> None:
        me = fake_bot(self, [WaitingChecks(names=2, connections=1)])
        self.assertTrue(
            await me.tell_dm_waiting(
                GUILD, self.campaign.id, "k", where="x",
                already=WaitingChecks.parse("names=2 people=0 claims=0 connections=0 unread=3"),
            )
        )  # fmt: skip
        text = me.ask_privately.await_args.args[2]
        self.assertIn("connection to check", text)
        self.assertNotIn("name", text.replace("connection", ""))  # (the 2 names were offered)

    async def test_a_restart_does_not_send_it_again(self) -> None:
        cid = self.campaign.id
        await fake_bot(self, [WaitingChecks(names=1)]).tell_dm_waiting(
            GUILD, cid, "lore:9", where="x"
        )
        again = fake_bot(self, [WaitingChecks(names=1)])  # (a new process, the same database)
        self.assertFalse(await again.tell_dm_waiting(GUILD, cid, "lore:9", where="x"))
        again.ask_privately.assert_not_awaited()


class TheImport(WithImports):
    async def scan(self, sid: str) -> None:
        await self.store.claim_scan(GUILD, sid, int(time.time()))
        await self.store.finish_scan(GUILD, sid, int(time.time()))

    async def test_an_import_of_three_sessions_sends_one_message_after_all_three(self) -> None:
        ids = [await self.imported(d) for d in (1, 2, 3)]
        me = fake_bot(self, [WaitingChecks(names=2)] * 4)
        for sid in ids[:2]:
            await self.scan(sid)
            await me._tell_finished_imports(GUILD, self.store)
        me.ask_privately.assert_not_awaited()  # (not per session)
        await self.scan(ids[2])
        await me._tell_finished_imports(GUILD, self.store)
        await me._tell_finished_imports(GUILD, self.store)  # (a second round)
        me.ask_privately.assert_awaited_once()

    async def test_the_leftover_read_gets_a_second_message_when_it_finishes(self) -> None:
        sid = await self.imported(1)
        await self.scan(sid)  # (scanned; its story read is not finished: the limit stopped it)
        me = fake_bot(self, [WaitingChecks(names=1), WaitingChecks(connections=2)])
        await me._tell_finished_imports(GUILD, self.store)
        me.ask_privately.assert_awaited_once()  # (what waits now)
        await me._tell_finished_imports(GUILD, self.store)
        me.ask_privately.assert_awaited_once()  # (still unread: nothing new)
        await self.transcripts.record_extraction(GUILD, sid, through_ms=10**13, calls=1, now=1)
        await me._tell_finished_imports(GUILD, self.store)
        self.assertEqual(me.ask_privately.await_count, 2)  # (the rest)
        await me._tell_finished_imports(GUILD, self.store)
        self.assertEqual(me.ask_privately.await_count, 2)

    async def test_an_import_scanned_before_the_story_read_existed_is_left_alone(self) -> None:
        sid = await self.imported(1)
        await self.store.claim_scan(GUILD, sid, 1_700_000_000)
        await self.store.finish_scan(GUILD, sid, 1_700_000_100)
        me = fake_bot(self, [WaitingChecks(names=1)])
        await me._tell_finished_imports(GUILD, self.store)
        me.ask_privately.assert_not_awaited()
