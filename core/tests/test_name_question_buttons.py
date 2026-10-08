"""The "Did they mean…?" buttons (#296), with a stand-in Discord interaction."""

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.dm_screen.name_questions import (
    NameAnswerUndoButton,
    NameQuestionButton,
    TypeNameForm,
    question_view,
    undo_view,
)
from dmbot.transcript import questions

GUILD = 1234
ASKED = questions.Asked("0a1b2c3d", 8, "Marin", (("a" * 32, "Maren"), ("b" * 32, "Marron")), 0.0)
CAMPAIGN = "c" * 32
DM_ID, PLAYER_ID = 7, 8


def interaction(guild_id: int = GUILD, client: Any = None) -> Any:
    it = MagicMock()
    it.guild_id = guild_id
    it.client = client if client is not None else MagicMock()
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.response.send_modal = AsyncMock()
    it.followup.send = AsyncMock()
    it.edit_original_response = AsyncMock()
    return it


class ButtonsTest(unittest.IsolatedAsyncioTestCase):
    def test_ids_survive_a_restart(self) -> None:
        view = question_view(GUILD, ASKED)
        ids = [item.item.custom_id for item in view.children]  # type: ignore[attr-defined]
        self.assertEqual(len(ids), 4)  # two names, Keep and Type it…
        three = questions.Asked("0a1b2c3d", 8, "Marin", (*ASKED.options, ("d" * 32, "Mara")), 0.0)
        labels = [item.item.label for item in question_view(GUILD, three).children]  # type: ignore[attr-defined]
        self.assertEqual(len(labels), 5)  # one row on a phone: Discord's limit
        self.assertEqual(labels[-1], "Type it…")
        self.assertTrue(all(len(label) <= 25 for label in labels))
        for custom_id in [*ids, undo_view("c" * 32, 99).children[0].item.custom_id]:  # type: ignore[attr-defined]
            with self.subTest(custom_id=custom_id):
                template = (
                    NameAnswerUndoButton if "askundo" in str(custom_id) else NameQuestionButton
                ).__discord_ui_compiled_template__
                self.assertIsNotNone(template.fullmatch(str(custom_id)))
                self.assertLessEqual(len(str(custom_id)), 100)

    async def test_a_press_from_another_server_is_closed(self) -> None:
        bot = MagicMock(answer_name_question=AsyncMock())
        it = interaction(guild_id=999, client=bot)
        await NameQuestionButton(GUILD, ASKED.id, "0", "Maren").callback(it)
        self.assertEqual(it.response.send_message.await_args.args[0], questions.EXPIRED)
        bot.answer_name_question.assert_not_awaited()

    async def test_an_answer_replaces_the_question_with_undo(self) -> None:
        bot = MagicMock(
            answer_name_question=AsyncMock(return_value=("✅ Got it", True, ("c" * 32, 5)))
        )
        it = interaction(client=bot)
        await NameQuestionButton(GUILD, ASKED.id, "0", "Maren").callback(it)
        kwargs = it.edit_original_response.await_args.kwargs
        self.assertEqual(kwargs["content"], "✅ Got it")
        (undo,) = kwargs["view"].children
        self.assertEqual(undo.item.custom_id, f"dmbot:askundo:{'c' * 32}:5")
        it.followup.send.assert_not_awaited()  # said once, in the message

    async def test_still_open_answers_privately_and_leaves_the_message(self) -> None:
        bot = MagicMock(answer_name_question=AsyncMock(return_value=(questions.BUSY, False, None)))
        it = interaction(client=bot)
        await NameQuestionButton(GUILD, ASKED.id, "0", "Maren").callback(it)
        self.assertEqual(it.followup.send.await_args.args[0], questions.BUSY)
        it.edit_original_response.assert_not_awaited()

    async def test_a_failure_says_try_again(self) -> None:
        bot = MagicMock(answer_name_question=AsyncMock(side_effect=RuntimeError("down")))
        it = interaction(client=bot)
        with self.assertLogs("dmbot.dm_screen.name_questions", "ERROR"):
            await NameQuestionButton(GUILD, ASKED.id, "keep", "Keep").callback(it)
        self.assertIn("Try again", it.followup.send.await_args.args[0])
        it.edit_original_response.assert_not_awaited()

    async def test_type_it_opens_the_form_for_the_dm(self) -> None:
        bot = MagicMock(
            answer_name_question=AsyncMock(), can_type_answer=MagicMock(return_value=None)
        )
        it = interaction(client=bot)
        it.user = MagicMock(id=DM_ID)
        await NameQuestionButton(GUILD, ASKED.id, "type", "Type it…").callback(it)
        bot.can_type_answer.assert_called_once_with(GUILD, ASKED.id, DM_ID)
        form = it.response.send_modal.await_args.args[0]
        self.assertIsInstance(form, TypeNameForm)
        self.assertEqual(form.name.max_length, 60)
        self.assertLessEqual(len(form.title), 45)  # Discord's limits
        self.assertLessEqual(len(questions.FORM_FIELD), 45)
        bot.answer_name_question.assert_not_awaited()  # nothing saved until it's sent

    async def test_type_it_says_why_not_without_a_form(self) -> None:
        bot = MagicMock(can_type_answer=MagicMock(return_value=questions.ONLY_DM))
        it = interaction(client=bot)
        await NameQuestionButton(GUILD, ASKED.id, "type", "Type it…").callback(it)
        self.assertEqual(it.response.send_message.await_args.args[0], questions.ONLY_DM)
        it.response.send_modal.assert_not_awaited()

    async def test_the_typed_name_is_the_answer(self) -> None:
        bot = MagicMock(
            answer_name_question=AsyncMock(return_value=("✅ Got it", True, ("c" * 32, 6)))
        )
        it = interaction(client=bot)
        it.user = MagicMock(id=DM_ID)
        form = TypeNameForm(GUILD, ASKED.id)
        form.name._value = "Maerin"
        await form.on_submit(it)
        bot.answer_name_question.assert_awaited_once_with(GUILD, ASKED.id, "type", DM_ID, "Maerin")
        self.assertEqual(it.edit_original_response.await_args.kwargs["content"], "✅ Got it")

    async def test_a_typing_mistake_answers_privately(self) -> None:
        problem = questions.typed_problem("it has a |. Type just the name")
        bot = MagicMock(answer_name_question=AsyncMock(return_value=(problem, False, None)))
        it = interaction(client=bot)
        form = TypeNameForm(GUILD, ASKED.id)
        form.name._value = "Mae | rin"
        await form.on_submit(it)
        self.assertEqual(it.followup.send.await_args.args[0], problem)
        it.edit_original_response.assert_not_awaited()  # the question stays


class UndoTest(unittest.IsolatedAsyncioTestCase):
    def bot(self, *, undo: Any = None) -> Any:
        campaign = MagicMock(guild_id=GUILD, id=CAMPAIGN, dm_user_ids=frozenset({DM_ID}))
        return MagicMock(
            campaigns=MagicMock(get=AsyncMock(return_value=campaign)),
            memory=MagicMock(undo=AsyncMock(side_effect=undo)),
            lookup=MagicMock(),
            answer_undone=AsyncMock(),
        )

    def press(self, bot: Any, user: int) -> Any:
        it = interaction(client=bot)
        it.guild = MagicMock(id=GUILD)
        it.user = MagicMock(id=user)
        it.message = MagicMock(content='✅ Got it: from now on, "Marin" is written **Maren**.')
        return it

    async def test_only_the_dm_can_undo(self) -> None:
        bot = self.bot()
        it = self.press(bot, PLAYER_ID)
        await NameAnswerUndoButton(CAMPAIGN, 5).callback(it)
        self.assertEqual(it.response.send_message.await_args.args[0], questions.UNDO_ONLY_DM)
        bot.memory.undo.assert_not_awaited()

    async def test_undo_takes_back_that_change(self) -> None:
        bot = self.bot()
        it = self.press(bot, DM_ID)
        await NameAnswerUndoButton(CAMPAIGN, 5).callback(it)
        bot.memory.undo.assert_awaited_once_with(GUILD, CAMPAIGN, 5)
        bot.lookup.mark_stale.assert_called_once_with(GUILD, CAMPAIGN)
        bot.answer_undone.assert_awaited_once_with(GUILD, CAMPAIGN, 5)  # the line goes back
        content = it.edit_original_response.await_args.kwargs["content"]
        self.assertIn('"Marin" stays as heard again', content)

    async def test_changed_since(self) -> None:
        from dmbot.memory.models import MemoryRuleError

        bot = self.bot(undo=MemoryRuleError("changed"))
        it = self.press(bot, DM_ID)
        await NameAnswerUndoButton(CAMPAIGN, 5).callback(it)
        self.assertEqual(it.followup.send.await_args.args[0], questions.UNDO_FAILED)
        it.edit_original_response.assert_not_awaited()

    async def test_too_late(self) -> None:
        from dmbot.memory.models import TooLateToUndo

        bot = self.bot(undo=TooLateToUndo("Too late to undo."))
        it = self.press(bot, DM_ID)
        await NameAnswerUndoButton(CAMPAIGN, 5).callback(it)
        self.assertEqual(
            it.followup.send.await_args.args[0],
            "Too late to undo. Fix the name on its card instead: `/dmbot names`.",
        )
        it.edit_original_response.assert_not_awaited()

    async def test_a_database_error_still_answers(self) -> None:
        bot = self.bot(undo=RuntimeError("down"))
        it = self.press(bot, DM_ID)
        with self.assertLogs("dmbot.dm_screen.name_questions", "ERROR"):
            await NameAnswerUndoButton(CAMPAIGN, 5).callback(it)
        self.assertIn("Try again", it.followup.send.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
