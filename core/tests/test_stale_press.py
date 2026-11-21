"""A press on a question a forget removed (#4908; Supervisor's ruling on #4907): the goodbye and
death question's buttons say "This question isn't waiting any more." when nobody answered it since
the message was sent, and keep "Already answered" when someone did; the language questions' card
says the one sentence true of both. Invented names and ids."""

from __future__ import annotations

import datetime as dt
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.memory import join_questions
from dmbot.ui import party_leave, trait_questions
from tests.test_goodbye_reading import TheQuestion
from tests.test_party_leave import ANNA, GUILD


class TheWords(unittest.TestCase):
    def test_supervisors_words_in_both_places(self) -> None:
        self.assertEqual(party_leave.NOT_WAITING, "This question isn't waiting any more.")
        self.assertTrue(trait_questions.GONE_NOW.startswith(party_leave.NOT_WAITING))


class ThePress(unittest.IsolatedAsyncioTestCase):
    def press(self, answered: Any) -> tuple[Any, Any]:
        sent = dt.datetime(2026, 11, 21, 12, 0, tzinfo=dt.UTC)
        interaction = SimpleNamespace(message=SimpleNamespace(created_at=sent))
        memory = SimpleNamespace(staying_answered_since=AsyncMock(side_effect=answered))
        return interaction, SimpleNamespace(memory=memory)

    async def test_answered_since_the_message_says_already_answered(self) -> None:
        interaction, bot = self.press([True])
        campaign = SimpleNamespace(guild_id=GUILD, id="c" * 32)
        with patch.object(party_leave, "_edit", AsyncMock()) as edit:
            await party_leave._gone(interaction, bot, campaign, "e" * 32)
        edit.assert_awaited_once_with(interaction, party_leave.ANSWERED)
        since = bot.memory.staying_answered_since.await_args.args[3]
        self.assertEqual(since, int(interaction.message.created_at.timestamp()))

    async def test_gone_without_an_answer_says_it_isnt_waiting_and_keeps_the_others(self) -> None:
        interaction, bot = self.press([False])
        campaign = SimpleNamespace(guild_id=GUILD, id="c" * 32)
        rest = object()
        with (
            patch.object(party_leave, "_edit", AsyncMock()) as edit,
            patch.object(
                party_leave, "_after_answer", AsyncMock(return_value=("redrawn", rest))
            ) as after,
        ):
            await party_leave._gone(interaction, bot, campaign, "e" * 32)
        after.assert_awaited_once_with(bot, campaign, "e" * 32, party_leave.NOT_WAITING)
        edit.assert_awaited_once_with(interaction, "redrawn", rest)

    async def test_a_failed_check_keeps_the_old_words(self) -> None:
        interaction, bot = self.press(RuntimeError("down"))
        campaign = SimpleNamespace(guild_id=GUILD, id="c" * 32)
        with patch.object(party_leave, "_edit", AsyncMock()) as edit:
            await party_leave._gone(interaction, bot, campaign, "e" * 32)
        edit.assert_awaited_once_with(interaction, party_leave.ANSWERED)


class TheStoreRead(TheQuestion):
    async def test_answered_since_or_removed_by_a_forget(self) -> None:
        sid = await self.session(9_000, 9_500)
        await self.memory.ask_goodbyes(GUILD, self.c, [(self.vell, [ANNA])], sid)
        await self.memory.ask_goodbyes(GUILD, self.c, [(self.tuck, [ANNA])], sid)
        sent = self.now
        self.now += 10
        await self.memory.staying_answer_stays(GUILD, self.c, self.vell)
        self.assertTrue(await self.memory.staying_answered_since(GUILD, self.c, self.vell, sent))
        self.assertFalse(
            await self.memory.staying_answered_since(GUILD, self.c, self.vell, self.now + 1)
        )  # (answered before this message was sent: an older answer)
        async with self.db.guild(GUILD) as conn:  # (Anna uses "Stop recording me")
            await join_questions.forget_speaker(conn, GUILD, ANNA, self.c)
        with self.assertRaises(join_questions.AlreadyAnswered):
            await self.memory.staying_answer_stays(GUILD, self.c, self.tuck)
        self.assertFalse(await self.memory.staying_answered_since(GUILD, self.c, self.tuck, sent))
