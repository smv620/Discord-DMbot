"""A press on a question a forget removed (#4908; Supervisor's ruling on #4907): the goodbye and
death question's buttons say "This question isn't waiting any more." when nobody answered it since
the message was sent, keep "Already answered" when someone did, and redraw the message with the
other characters on it that still wait. The language questions' card says the one sentence true of
both. Invented names and ids."""

from __future__ import annotations

import datetime as dt
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from dmbot.memory import join_questions
from dmbot.memory.join_questions import AlreadyAnswered
from dmbot.transcript.models import Line
from dmbot.ui import party_leave, trait_questions
from tests.agreed import agree
from tests.test_party_leave import ANNA, BEN, DM_ID, GUILD, Party

C, VELL, TUCK = "c" * 32, "a" * 32, "b" * 32
SENT = dt.datetime(2026, 11, 21, 12, 0, tzinfo=dt.UTC)


def message(*entities: str) -> Any:
    """A sent message with each character's two buttons, as `question_view` draws them."""
    rows = [
        SimpleNamespace(children=[
            SimpleNamespace(custom_id=f"dmbot:pleavel:{C}:{e}"),
            SimpleNamespace(custom_id=f"dmbot:pleaves:{C}:{e}"),
        ])
        for e in entities
    ]  # fmt: skip
    rows.append(SimpleNamespace(children=[SimpleNamespace(custom_id=f"dmbot:pleavek:{C}")]))
    return SimpleNamespace(created_at=SENT, components=rows)


class TheWords(unittest.TestCase):
    def test_supervisors_words_in_both_places(self) -> None:
        self.assertEqual(party_leave.NOT_WAITING, "This question isn't waiting any more.")
        self.assertTrue(trait_questions.GONE_NOW.startswith(party_leave.NOT_WAITING))

    def test_the_others_are_read_from_the_messages_own_buttons(self) -> None:
        self.assertEqual(party_leave._others_on(message(VELL, TUCK), VELL), [TUCK])
        self.assertEqual(party_leave._others_on(SimpleNamespace(), VELL), [])


class ThePress(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.campaign = SimpleNamespace(guild_id=GUILD, id=C)

    def bot(self, answered: Any) -> Any:
        return SimpleNamespace(
            memory=SimpleNamespace(staying_answered_since=AsyncMock(side_effect=answered))
        )

    async def test_answered_since_the_message_says_already_answered(self) -> None:
        bot, interaction = self.bot([True]), SimpleNamespace(message=message(VELL))
        with patch.object(party_leave, "_edit", AsyncMock()) as edit:
            await party_leave._gone(cast(Any, interaction), bot, self.campaign, VELL)
        edit.assert_awaited_once_with(interaction, party_leave.ANSWERED)
        since = bot.memory.staying_answered_since.await_args.args[3]
        self.assertEqual(since, int(SENT.timestamp()))

    async def test_removed_says_it_isnt_waiting_and_keeps_the_others_still_waiting(self) -> None:
        bot, interaction = self.bot([False]), SimpleNamespace(message=message(VELL, TUCK))
        rest = object()
        after = AsyncMock(return_value=("redrawn", rest))
        with (
            patch.object(party_leave, "_edit", AsyncMock()) as edit,
            patch.object(party_leave, "_after_answer", after),
        ):
            await party_leave._gone(cast(Any, interaction), bot, self.campaign, VELL)
        after.assert_awaited_once_with(bot, self.campaign, TUCK, party_leave.NOT_WAITING)
        edit.assert_awaited_once_with(interaction, "redrawn", rest)

    async def test_removed_with_nobody_left_drops_the_buttons(self) -> None:
        bot, interaction = self.bot([False]), SimpleNamespace(message=message(VELL))
        with patch.object(party_leave, "_edit", AsyncMock()) as edit:
            await party_leave._gone(cast(Any, interaction), bot, self.campaign, VELL)
        edit.assert_awaited_once_with(interaction, party_leave.NOT_WAITING, None)

    async def test_a_failed_check_says_the_words_true_either_way(self) -> None:
        bot, interaction = self.bot(RuntimeError("down")), SimpleNamespace(message=message(VELL))
        with patch.object(party_leave, "_edit", AsyncMock()) as edit:
            await party_leave._gone(cast(Any, interaction), bot, self.campaign, VELL)
        edit.assert_awaited_once_with(interaction, party_leave.NOT_WAITING, None)


class TheButtons(unittest.IsolatedAsyncioTestCase):
    """Each answer button sends a gone question to `_gone`, after the DM check, never before."""

    async def press(self, button: Any, *, dm: bool) -> Any:
        memory = SimpleNamespace(
            staying_answer_left=AsyncMock(side_effect=AlreadyAnswered("x")),
            staying_answer_stays=AsyncMock(side_effect=AlreadyAnswered("x")),
            staying_answered_since=AsyncMock(return_value=False),
        )
        bot = SimpleNamespace(memory=memory)
        campaign = SimpleNamespace(guild_id=GUILD, id=C) if dm else None
        interaction = SimpleNamespace(message=message(VELL))
        with (
            patch.object(party_leave, "_answer_first", AsyncMock()),
            patch.object(party_leave, "_bot", return_value=bot),
            patch.object(party_leave, "_dm_campaign", AsyncMock(return_value=campaign)),
            patch.object(party_leave, "_name", AsyncMock(return_value="Vell")),
            patch.object(party_leave, "_edit", AsyncMock()) as edit,
        ):
            await button.callback(interaction)
        return memory, edit, interaction

    async def test_left_and_stays_say_it_isnt_waiting(self) -> None:
        for button in (party_leave.LeftButton(C, VELL), party_leave.StaysButton(C, VELL)):
            with self.subTest(type(button).__name__):
                _memory, edit, interaction = await self.press(button, dm=True)
                edit.assert_awaited_once_with(interaction, party_leave.NOT_WAITING, None)

    async def test_someone_not_a_dm_reads_nothing(self) -> None:
        for button in (party_leave.LeftButton(C, VELL), party_leave.StaysButton(C, VELL)):
            with self.subTest(type(button).__name__):
                memory, edit, _ = await self.press(button, dm=False)
                memory.staying_answered_since.assert_not_awaited()
                edit.assert_not_awaited()


class TheStoreRead(Party):
    """`MemoryStore.staying_answered_since` and the redraw, on questions really sent."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await agree(self.db, GUILD, DM_ID, ANNA, BEN)
        self.sid = await self.played(9_000, None)
        await self.sessions.add_lines(
            GUILD, self.sid,
            [Line(9_001, DM_ID, "Vell says goodbye.", "x"), Line(9_002, ANNA, "Bye.", "x"),
             Line(9_003, BEN, "Bye.", "x")],
        )  # fmt: skip
        await self.memory.ask_goodbyes(GUILD, self.c, [(self.vell, [ANNA])], self.sid)
        await self.memory.ask_goodbyes(GUILD, self.c, [(self.tuck, [BEN])], self.sid)
        await self.sessions.end_session(GUILD, self.sid, 9_500)
        sent = await self.memory.staying_questions_to_send(GUILD, self.c)
        self.assertEqual({q.entity_id for q in sent}, {self.vell, self.tuck})  # (one message)
        self.sent = self.now
        self.now += 10

    async def since(self, who: str, at: int) -> bool:
        return await self.memory.staying_answered_since(GUILD, self.c, who, at)

    async def test_answered_left_or_stays_since_the_message(self) -> None:
        await self.memory.staying_answer_stays(GUILD, self.c, self.vell)
        await self.memory.staying_answer_left(GUILD, self.c, self.tuck)
        self.assertTrue(await self.since(self.vell, self.sent))
        self.assertTrue(await self.since(self.tuck, self.sent))
        self.assertFalse(await self.since(self.vell, self.now + 1))  # (before this message)

    async def test_stop_recording_me_removes_one_and_the_redraw_keeps_the_other(self) -> None:
        async with self.db.guild(GUILD) as conn:  # (Anna uses "Stop recording me")
            await join_questions.forget_speaker(conn, GUILD, ANNA, self.c)
        with self.assertRaises(AlreadyAnswered):
            await self.memory.staying_answer_stays(GUILD, self.c, self.vell)
        self.assertFalse(await self.since(self.vell, self.sent))
        bot = SimpleNamespace(memory=self.memory)
        campaign = SimpleNamespace(guild_id=GUILD, id=self.c)
        interaction = SimpleNamespace(message=SimpleNamespace(
            created_at=dt.datetime.fromtimestamp(self.sent, dt.UTC),
            components=[SimpleNamespace(children=[
                SimpleNamespace(custom_id=f"dmbot:pleavel:{self.c}:{who}")
                for who in (self.vell, self.tuck)
            ])],
        ))  # fmt: skip
        with patch.object(party_leave, "_edit", AsyncMock()) as edit:
            await party_leave._gone(cast(Any, interaction), bot, campaign, self.vell)
        assert edit.await_args is not None
        text, view = edit.await_args.args[1], edit.await_args.args[2]
        self.assertTrue(text.startswith(party_leave.NOT_WAITING))
        self.assertIn("Tuck", text)
        ids = [item.custom_id for item in view.children]
        self.assertIn(f"dmbot:pleavel:{self.c}:{self.tuck}", ids)  # (Tuck's buttons stay)
        self.assertNotIn(f"dmbot:pleavel:{self.c}:{self.vell}", ids)

    async def test_a_deleted_transcript_removes_both(self) -> None:
        async with self.db.guild(GUILD) as conn:
            await join_questions.forget_session(conn, GUILD, self.sid)
        self.assertFalse(await self.since(self.vell, self.sent))
        self.assertFalse(await self.since(self.tuck, self.sent))
