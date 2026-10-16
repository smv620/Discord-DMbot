"""The 🗑 Delete my past transcripts flow in the consent menu and `/dmbot delete-my-lines`
(#1532): one warning with the numbers, one confirmation, nothing deleted before it, the words,
and that it only ever acts for the person who pressed. Fakes: no Discord, no database."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot import consent_dm as c
from dmbot.bot import DMBot
from dmbot.config import Settings
from dmbot.transcript.store import Deleted

GUILD, ME = 1, 8


def press(counts: tuple[int, int] = (12, 3), *, fail: bool = False) -> Any:
    actions = SimpleNamespace(
        get_guild=lambda gid: SimpleNamespace(id=gid, name="Dragon Club"),
        count_my_lines=AsyncMock(return_value=counts),
        delete_my_lines=AsyncMock(
            side_effect=RuntimeError("down") if fail else None, return_value=counts
        ),
    )
    it = MagicMock()
    it.client = actions
    it.user = SimpleNamespace(id=ME)
    it.response = SimpleNamespace(
        defer=AsyncMock(), send_message=AsyncMock(), edit_message=AsyncMock()
    )
    it.followup = SimpleNamespace(send=AsyncMock())
    it.edit_original_response = AsyncMock()
    return it


class Warning(unittest.IsolatedAsyncioTestCase):
    async def test_one_warning_with_the_numbers_and_nothing_deleted(self) -> None:
        it = press()
        await c.DeleteButton(GUILD).callback(it)
        text = it.followup.send.await_args.args[0]
        self.assertIn("everything DMbot wrote down from you in **Dragon Club**", text)
        self.assertIn("12 lines in 3 sessions", text)
        self.assertIn("Transcripts others downloaded already are not affected", text)
        self.assertIn("This can't be undone.", text)
        view = it.followup.send.await_args.kwargs["view"]
        self.assertEqual(
            [i.item.label for i in view.children], [c.DELETE_YES_LABEL, c.DELETE_NO_LABEL]
        )
        it.client.delete_my_lines.assert_not_awaited()
        it.client.count_my_lines.assert_awaited_once_with(GUILD, ME)  # the presser's own

    async def test_one_line_in_one_session_is_said_in_the_singular(self) -> None:
        text = c.delete_warning_text("Dragon Club", 1, 1)
        self.assertIn("1 line in 1 session.", text)

    async def test_nothing_written_down_says_so_and_asks_nothing(self) -> None:
        it = press((0, 0))
        await c.DeleteButton(GUILD).callback(it)
        self.assertEqual(
            it.followup.send.await_args.args[0],
            "DMbot has nothing written down from you in **Dragon Club**.",
        )
        self.assertNotIn("view", it.followup.send.await_args.kwargs)

    async def test_the_names_cant_format_or_ping(self) -> None:
        text = c.delete_warning_text("**Dragon** @everyone", 2, 1)
        self.assertNotIn("@everyone", text)
        self.assertNotIn("**Dragon**", text)

    async def test_a_server_this_process_doesnt_serve_points_to_the_command(self) -> None:
        it = press()
        it.client.get_guild = lambda gid: None
        await c.DeleteButton(GUILD).callback(it)
        it.response.send_message.assert_awaited_once_with(c.NOT_HERE, ephemeral=True)


class Confirmation(unittest.IsolatedAsyncioTestCase):
    async def test_yes_deletes_for_the_presser_and_says_done(self) -> None:
        it = press()
        await c.DeleteYesButton(GUILD).callback(it)
        it.client.delete_my_lines.assert_awaited_once_with(GUILD, ME)
        kwargs = it.edit_original_response.await_args.kwargs
        self.assertEqual(
            kwargs["content"], "🗑 Done. Your lines are gone from **Dragon Club**'s transcripts."
        )
        self.assertIsNone(kwargs["view"])

    async def test_a_yes_on_an_old_warning_with_nothing_left_says_nothing_written(self) -> None:
        it = press((0, 0))
        await c.DeleteYesButton(GUILD).callback(it)
        self.assertIn(
            "nothing written down", it.edit_original_response.await_args.kwargs["content"]
        )

    async def test_a_failure_says_nothing_was_deleted(self) -> None:
        it = press(fail=True)
        with self.assertLogs("dmbot.consent_dm", "ERROR"):
            await c.DeleteYesButton(GUILD).callback(it)
        self.assertEqual(it.edit_original_response.await_args.kwargs["content"], c.DELETE_FAILED)

    async def test_cancel_deletes_nothing(self) -> None:
        it = press()
        await c.DeleteNoButton(GUILD).callback(it)
        it.client.delete_my_lines.assert_not_awaited()
        self.assertEqual(it.response.edit_message.await_args.kwargs["content"], c.DELETE_KEPT)


class Stopped(unittest.TestCase):
    def test_the_stop_message_says_how_to_delete(self) -> None:
        text = c.stopped_text("Dragon Club")
        self.assertIn(c.ALREADY_RECORDED, text)
        self.assertIn(
            "To remove what it already wrote down, press 🗑 Delete my past transcripts "
            "in the ⚙️ Menu.",
            text,
        )

    def test_the_buttons_are_registered_so_they_survive_a_restart(self) -> None:
        for button in (c.DeleteButton, c.DeleteYesButton, c.DeleteNoButton):
            self.assertIn(button, c.CONSENT_BUTTONS)


class BotSide(unittest.IsolatedAsyncioTestCase):
    def bot(self, *, memory: Any = None) -> DMBot:
        bot = DMBot.__new__(DMBot)
        bot.transcripts = SimpleNamespace(  # type: ignore[assignment]
            person_counts=AsyncMock(return_value=Deleted(5, 2)),
            delete_person=AsyncMock(return_value=Deleted(5, 2)),
        )
        bot.memory = memory
        return bot

    async def test_counts_come_from_the_transcripts(self) -> None:
        self.assertEqual(await self.bot().count_my_lines(GUILD, ME), (5, 2))

    async def test_the_alias_step_runs_first_and_a_failure_there_deletes_no_lines(self) -> None:
        memory = SimpleNamespace(forget_alias_user=AsyncMock(side_effect=RuntimeError("down")))
        bot = self.bot(memory=memory)
        with self.assertRaises(RuntimeError):
            await bot.delete_my_lines(GUILD, ME)
        bot.transcripts.delete_person.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_a_bot_without_transcripts_has_nothing_to_delete(self) -> None:
        bot = self.bot()
        bot.transcripts = None
        self.assertEqual(await bot.delete_my_lines(GUILD, ME), (0, 0))
        self.assertEqual(await bot.count_my_lines(GUILD, ME), (0, 0))

    async def test_the_command_needs_a_server(self) -> None:
        from dmbot.ui.dmbot_commands import delete_my_lines_command

        it = press()
        it.guild = None
        await delete_my_lines_command.callback(it)  # type: ignore[call-arg]
        it.response.send_message.assert_awaited_once_with("Use this in a server.", ephemeral=True)


_ = Settings  # (kept importable for the other consent tests' helper style)

if __name__ == "__main__":
    unittest.main()
