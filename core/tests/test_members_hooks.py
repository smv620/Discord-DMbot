"""The bot keeps the member directory up to date (#2141): a recorded member is remembered in the
background, a failure never reaches the caller, and without a database nothing happens."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from dmbot.bot import DMBot


class TheHooks(unittest.IsolatedAsyncioTestCase):
    def bot(self, directory: object) -> SimpleNamespace:
        bot = SimpleNamespace(members=directory, _name_tasks=set())
        bot._keep_member = lambda *a: DMBot._keep_member(bot, *a)  # type: ignore[arg-type]
        return bot

    async def test_a_member_is_remembered_in_the_background_with_both_names(self) -> None:
        directory = SimpleNamespace(remember=AsyncMock())
        bot = self.bot(directory)
        member = SimpleNamespace(id=7, global_name="Quill Ardent", name="quill_a")
        DMBot.note_member(bot, 5, member)  # type: ignore[arg-type]
        await asyncio.gather(*bot._name_tasks)
        directory.remember.assert_awaited_once_with(5, 7, "Quill Ardent", "quill_a")

    async def test_a_failure_is_logged_and_never_raised(self) -> None:
        directory = SimpleNamespace(remember=AsyncMock(side_effect=RuntimeError("db blip")))
        bot = self.bot(directory)
        member = SimpleNamespace(id=7, global_name=None, name="quill_a")
        with self.assertLogs("dmbot.bot", level="WARNING") as logs:
            DMBot.note_member(bot, 5, member)  # type: ignore[arg-type]
            await asyncio.gather(*bot._name_tasks)
        self.assertIn("RuntimeError", " ".join(logs.output))
        self.assertNotIn("db blip", " ".join(logs.output))  # a database message can quote a name

    async def test_without_a_directory_nothing_happens(self) -> None:
        bot = self.bot(None)
        DMBot.note_member(bot, 5, SimpleNamespace(id=7, global_name="x", name="y"))  # type: ignore[arg-type]
        self.assertEqual(bot._name_tasks, set())


class TheStop(unittest.IsolatedAsyncioTestCase):
    def fake(self, directory: object, order: list[str]) -> SimpleNamespace:
        async def revoke(*_a: object) -> bool:
            order.append("revoke")
            return True

        async def forget(*_a: object) -> None:
            order.append("forget")

        return SimpleNamespace(
            consent=SimpleNamespace(
                has_consent=lambda *_: True, agreed_now=lambda *_: True, revoke=revoke
            ),
            stop_recording=MagicMock(),
            _tell_dm_about_consent=MagicMock(),
            push_allowlist=AsyncMock(),
            heard_questions=None,
            memory=None,
            members=SimpleNamespace(forget=forget) if directory == "ok" else directory,
        )

    async def test_stopping_forgets_the_member_after_the_revoke(self) -> None:
        order: list[str] = []
        fake = self.fake("ok", order)
        self.assertTrue(await DMBot.withdraw_consent(fake, 5, 7))  # type: ignore[arg-type]
        self.assertEqual(order, ["revoke", "forget"])

    async def test_a_failing_forget_never_blocks_stopping_and_logs_only_the_type(self) -> None:
        fake = self.fake(
            SimpleNamespace(forget=AsyncMock(side_effect=RuntimeError("row Mira"))), []
        )
        with self.assertLogs("dmbot.bot", level="WARNING") as logs:
            self.assertTrue(await DMBot.withdraw_consent(fake, 5, 7))  # type: ignore[arg-type]
        self.assertIn("RuntimeError", " ".join(logs.output))
        self.assertNotIn("Mira", " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
