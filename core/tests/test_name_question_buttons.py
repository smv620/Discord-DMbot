"""The "Did they mean…?" buttons (#296), with a stand-in Discord interaction."""

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.dm_screen.name_questions import (
    NameAnswerUndoButton,
    NameQuestionButton,
    question_view,
    undo_view,
)
from dmbot.transcript import questions

GUILD = 1234
ASKED = questions.Asked("0a1b2c3d", 8, "Marin", (("a" * 32, "Maren"), ("b" * 32, "Marron")), 0.0)


def interaction(guild_id: int = GUILD, client: Any = None) -> Any:
    it = MagicMock()
    it.guild_id = guild_id
    it.client = client if client is not None else MagicMock()
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.followup.send = AsyncMock()
    it.edit_original_response = AsyncMock()
    return it


class ButtonsTest(unittest.IsolatedAsyncioTestCase):
    def test_ids_survive_a_restart(self) -> None:
        view = question_view(GUILD, ASKED)
        ids = [item.item.custom_id for item in view.children]  # type: ignore[attr-defined]
        self.assertEqual(len(ids), 3)  # two names and Keep
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


if __name__ == "__main__":
    unittest.main()
