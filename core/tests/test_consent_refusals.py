"""Each place core re-checks consent after an await, and refuses (#1863).

CLAUDE.md, Hard rules, Consent: "never capture, decode, store, or transcribe audio from a user who
has not opted in … re-checked in core, including after every async step." #1861's coverage report
listed refusal branches in `bot.py` and its neighbours that no test ran. Each test here lets the
person agree, then has them press Stop at the step just before the check, and asserts that nothing
was stored, posted or queued for them. A positive control (the same call without the stop) is in
each group, so a test can't pass because the setup was wrong.

Built on the session harness in `test_sessions.py` (a real DMBot, a real consent store).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import tests.test_sessions as ts
from dmbot.transcript import left_out, speaker_ai
from dmbot.transcript.topics import Waiting

GUILD, DM, PLAYER = ts.GUILD, ts.DM, ts.PLAYER


class Harness(ts.SessionTests):
    """The session harness without its own tests (they run in `test_sessions.py`)."""


for _name in dir(ts.SessionTests):
    if _name.startswith("test"):
        setattr(Harness, _name, None)


class Refusals(Harness):
    # The session harness's helpers, borrowed (not inherited: that class holds thousands of tests).
    said = ts.SaveAndResume.said
    settle = ts.SaveAndResume.settle
    filtered = ts.SaveAndResume.filtered
    speaking = ts.SaveAndResume.speaking
    joined_with_transcript = ts.SaveAndResume.joined_with_transcript
    left_out_line = ts.SaveAndResume.left_out_line
    asked_about_marin = ts.SaveAndResume.asked_about_marin
    two_alike = ts.SaveAndResume.two_alike

    def stop(self, user: int) -> None:
        """The person presses Stop recording me: the cache changes at once."""
        self.consent.stop_now(GUILD, user)


class SavingALabel(Refusals):
    """`_save_topic`: a line's topic is saved only while its speaker is recorded."""

    async def table_with_a_line(self) -> tuple[Any, Waiting]:
        table = await self.filtered("1 game\n2 game")
        self.said(table, "we sneak past the guards")
        await self.settle()
        return table, Waiting(PLAYER, 0, "we sneak past the guards", 0.0)

    async def test_a_stopped_speakers_line_gets_no_topic(self) -> None:
        table, waiting = await self.table_with_a_line()
        self.stop(PLAYER)
        await self.bot._save_topic(table, waiting, "off_topic")
        self.assertTrue(table.unsaved._waiting)
        self.assertTrue(all(line.topic == "game" for line in table.unsaved._waiting))

    async def test_the_same_call_for_someone_still_recorded_saves_it(self) -> None:
        table, waiting = await self.table_with_a_line()
        await self.bot._save_topic(table, waiting, "off_topic")
        self.assertEqual([line.topic for line in table.unsaved._waiting], ["off_topic"])


class SpeakerQuestions(Refusals):
    """ "Who was speaking just now?" (#53): asked, posted and answered only about a recorded
    person's line."""

    async def question_for_the_player(self) -> tuple[Any, Any]:
        table = await self.speaking("1 npc 2 maybe\n2 unsure")
        self.bot.post_message = AsyncMock(return_value=MagicMock(edit=AsyncMock()))  # type: ignore[method-assign]
        asked = table.speaker_questions.offer(PLAYER, 5_000, "the door creaks", ["Narrating"], 0.0)
        assert asked is not None
        return table, asked

    async def test_no_question_is_offered_about_a_stopped_speakers_line(self) -> None:
        table = await self.speaking("1 npc 2 maybe\n2 unsure")
        self.stop(PLAYER)
        waiting = Waiting(PLAYER, 5_000, "the door creaks", 1.0)
        self.bot._offer_speaker_question(
            table, waiting, speaker_ai.Guess("", False), table.name_lookup, {}
        )
        self.assertIsNone(table.speaker_questions.open)

    async def test_the_same_line_of_someone_recorded_is_offered(self) -> None:
        table = await self.speaking("1 npc 2 maybe\n2 unsure")
        waiting = Waiting(PLAYER, 5_000, "the door creaks", 1.0)
        self.bot._offer_speaker_question(
            table, waiting, speaker_ai.Guess("", False), table.name_lookup, {}
        )
        self.assertIsNotNone(table.speaker_questions.open)

    async def test_a_question_is_not_posted_after_its_speaker_stops(self) -> None:
        table, asked = await self.question_for_the_player()
        self.stop(PLAYER)
        await self.bot._ask_speaker(table, asked)
        self.bot.post_message.assert_not_awaited()
        self.assertIsNone(table.speaker_questions.open)  # closed: their words are not on the screen

    async def test_an_answer_about_a_stopped_speaker_saves_nothing(self) -> None:
        table, asked = await self.question_for_the_player()
        self.stop(PLAYER)
        text, done = await self.bot.answer_speaker_question(GUILD, asked.id, "0", DM)
        self.assertEqual((text, done), (ts.speaker_q.GONE, True))
        self.assertTrue(all(line.entity == "" for line in table.unsaved._waiting))

    async def test_a_stop_while_the_names_are_checked_saves_nothing(self) -> None:
        _table, asked = await self.question_for_the_player()
        real = self.bot._current_names

        async def names_then_stop(t: Any) -> Any:
            got = await real(t)
            self.stop(PLAYER)  # they press Stop while the DM's answer is being checked
            return got

        self.bot._current_names = names_then_stop  # type: ignore[method-assign]
        saved = AsyncMock()
        self.bot._save_entity = saved  # type: ignore[method-assign]
        text, done = await self.bot.answer_speaker_question(GUILD, asked.id, "0", DM)
        self.assertEqual((text, done), (ts.speaker_q.GONE, True))
        saved.assert_not_awaited()


class PuttingLinesBack(Refusals):
    """ "Put it back" for lines left out as off-topic (#677): a person who stopped has no lines
    to put back, in the saved transcript or in the live channel."""

    async def test_put_back_after_a_stop_changes_nothing(self) -> None:
        table, posted, _ = await self.left_out_line()
        (run,) = table.left_out.runs
        edits = posted.edit.await_count
        self.stop(PLAYER)
        answer = await self.bot.put_back(GUILD, run.id, DM)
        self.assertEqual(answer, left_out.STOPPED)
        topics = {line.text: line.topic for line in table.unsaved._waiting}
        self.assertEqual(topics["my boss called again"], "off_topic")  # still left out
        self.assertEqual(posted.edit.await_count, edits)  # and the live channel is untouched
        self.assertFalse(run.put_back)
        self.assertNotIn("my boss called again", [text for _, text in table.heard])

    async def test_the_live_channel_is_not_edited_after_a_stop(self) -> None:
        table, posted, _ = await self.left_out_line()
        (run,) = table.left_out.runs
        edits = posted.edit.await_count
        markers = set(table.hidden)
        self.assertTrue(markers)  # the setup left a marker in the channel
        self.stop(PLAYER)
        shown = await self.bot._show_lines_again(table, run.lines)
        self.assertFalse(shown)
        self.assertEqual(posted.edit.await_count, edits)
        self.assertEqual(table.hidden, markers)  # still markers

    async def test_the_same_put_back_for_someone_recorded_works(self) -> None:
        table, posted, _ = await self.left_out_line()
        (run,) = table.left_out.runs
        answer = await self.bot.put_back(GUILD, run.id, DM)
        self.assertIn("Put back 1", answer)
        self.assertIn("my boss called again", posted.edit.await_args.kwargs["content"])
