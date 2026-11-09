"""The DM Bot channel's counts-only pointers and the notices that moved there (#3874, group D).

The channel is read by the campaign's people, so none of these may carry a name, a secret or a
file name.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.dm_screen.adventure_card import finished_text
from dmbot.dm_screen.upkeep_pointers import names_fixed_pointer, secrets_pointer

FILE = "Mirelle Vane's secrets.pdf"


class ThePointers(unittest.TestCase):
    def test_the_secrets_pointer_is_numbers_only(self) -> None:
        self.assertEqual(
            secrets_pointer(2, 1, 3),
            "🤫 After the session: 2 secrets said at the table, 1 new name, 3 things to check. "
            "The DM has the details privately.",
        )
        self.assertIn("something to look at", secrets_pointer(0, 0, 0))

    def test_the_names_fixed_pointer_is_numbers_only(self) -> None:
        text = names_fixed_pointer(1, 2, 0)
        self.assertIn("1 name fixed", text)
        self.assertIn("2 not sure", text)
        self.assertNotIn("new", text)
        self.assertIn("The DM has the report with Undo.", text)

    def test_a_finished_read_names_the_file_only_where_it_is_private(self) -> None:
        done = SimpleNamespace(status="done", stop_reason="", percent=100)
        stopped = SimpleNamespace(status="stopped", stop_reason="", percent=40)
        for state in (done, stopped):
            self.assertIn("Mirelle Vane", finished_text(FILE, state, None))  # type: ignore[arg-type]
            plain = finished_text(FILE, state, None, named=False)  # type: ignore[arg-type]
            self.assertNotIn("Mirelle", plain)
            self.assertIn("your file", plain)


class TheMovedNotices(unittest.IsolatedAsyncioTestCase):
    def bot(self, bot_channel: int | None) -> Any:
        camp = SimpleNamespace(dm_screen_channel_id=66, dm_bot_channel_id=bot_channel)
        me = SimpleNamespace(
            plan=SimpleNamespace(
                states=AsyncMock(
                    return_value={"d": SimpleNamespace(status="done", stop_reason="", percent=100)}
                ),
                counts=AsyncMock(return_value={}),
            ),
            adventure=SimpleNamespace(
                documents=AsyncMock(return_value=[SimpleNamespace(id="d", file_name=FILE)])
            ),
            campaigns=SimpleNamespace(get=AsyncMock(return_value=camp)),
            post=AsyncMock(),
        )
        me._upkeep_channel = lambda c: DMBot._upkeep_channel(me, c)
        return me

    async def test_the_finish_line_goes_to_the_dm_bot_channel_without_the_file_name(self) -> None:
        me = self.bot(88)
        await DMBot._plan_read_notice(me, 1, "c", "d")
        channel, text = me.post.await_args.args
        self.assertEqual(channel, 88)
        self.assertNotIn("Mirelle", text)

    async def test_with_no_dm_bot_channel_the_dm_screen_keeps_the_named_line(self) -> None:
        me = self.bot(None)
        await DMBot._plan_read_notice(me, 1, "c", "d")
        channel, text = me.post.await_args.args
        self.assertEqual(channel, 66)
        self.assertIn("Mirelle Vane", text)


if __name__ == "__main__":
    unittest.main()
