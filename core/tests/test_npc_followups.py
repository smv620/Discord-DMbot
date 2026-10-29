"""Follow-ups to the NPC pages' pass (#1792, #2874): a kind's label changing refreshes the view
(through the same paced pass), and what is kept in memory for a campaign goes when the campaign
does, unless a pass is running. The kind test needs Postgres."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock

from dmbot.bot import DMBot
from dmbot.memory import notify
from dmbot.memory.ontology import TypeTerm
from tests.test_memory_store import GUILD_A, MemoryTest

CID = "c" * 32


class Payload(unittest.TestCase):
    def test_the_flag_carries_names_and_kinds_and_old_senders_still_read(self) -> None:
        for names in (False, True):
            for kinds in (False, True):
                raw = notify.payload(CID, 5, names, kinds)
                got = notify.parse(raw)
                assert got is not None
                self.assertEqual((got.names_changed, got.kinds_changed), (names, kinds))
        old = notify.parse(f"{CID}:5:1")  # what a sender from before this change sends
        assert old is not None
        self.assertEqual((old.names_changed, old.kinds_changed), (True, False))
        self.assertIsNone(notify.parse(f"{CID}:5:4"))
        self.assertIsNone(notify.parse(f"{CID}:5:x"))


class KindRename(MemoryTest):
    async def test_a_kind_change_is_announced_as_kinds_changed_only(self) -> None:
        seen: list[Any] = []
        real = notify.send

        async def spy(conn: Any, cid: str, version: int, names: bool, kinds: bool = False) -> None:
            seen.append((names, kinds))
            await real(conn, cid, version, names, kinds)

        from unittest import mock

        with mock.patch.object(notify, "send", spy):
            await self.memory.add_type(
                GUILD_A, self.c, TypeTerm("ship", "item", "ship", "A ship.", core=False),
                examples=["x"], reason="y", source="entitybot",
            )  # fmt: skip
            await self.add("Mira", type="character", role="npc", status="confirmed")
        self.assertEqual(seen[0], (False, True))  # a kind: the labels the pages show
        self.assertEqual(seen[1][0], True)  # an entry: names, as before


class Bot(unittest.IsolatedAsyncioTestCase):
    def bot(self) -> Any:
        bot = MagicMock(spec=DMBot)
        bot._npcs_sync, bot._npcs_dirty = {}, set()
        bot._npcs_last, bot._npcs_guilds = {}, {}
        return bot

    async def test_a_kind_rename_goes_through_the_same_paced_pass(self) -> None:
        bot = self.bot()
        bot._npcs_guilds = {CID: 1}
        tasks: list[Any] = []

        def track(work: Any, name: str) -> Any:
            work.close()
            task = MagicMock()
            task.done.return_value = False
            tasks.append(task)
            return task

        bot._track = track
        for flag in ("2", "2", "3"):  # kinds changed, twice, then names and kinds
            event = notify.parse(f"{CID}:5:{flag}")
            assert event is not None
            if event.names_changed or event.kinds_changed:
                DMBot._npcs_changed(bot, event.campaign_id)
        self.assertEqual(len(tasks), 1)  # one pass; the rest wait for it (no second path)
        self.assertEqual(bot._npcs_dirty, {CID})

    async def test_a_deleted_campaign_loses_what_the_pass_kept(self) -> None:
        bot = self.bot()
        other = "d" * 32
        bot._npcs_guilds = {CID: 1, other: 1}
        bot._npcs_last = {CID: 1.0, other: 2.0}
        bot._npcs_dirty = {CID}
        done = MagicMock()
        done.done.return_value = True
        bot._npcs_sync = {CID: done}
        DMBot._forget_campaign(bot, CID)
        self.assertEqual((bot._npcs_guilds, bot._npcs_last), ({other: 1}, {other: 2.0}))
        self.assertEqual((bot._npcs_dirty, bot._npcs_sync), (set(), {}))

    async def test_a_running_pass_is_left_to_clean_up_after_itself(self) -> None:
        bot = self.bot()
        bot._npcs_guilds = {CID: 1}
        running = MagicMock()
        running.done.return_value = False
        bot._npcs_sync = {CID: running}
        bot._npcs_dirty = {CID}
        DMBot._forget_campaign(bot, CID)
        self.assertEqual(bot._npcs_sync, {CID: running})
        self.assertNotIn(CID, bot._npcs_guilds)


if __name__ == "__main__":
    unittest.main()
