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

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import tests.test_sessions as ts
from dmbot.transcript import fix_notes, left_out, speaker_ai
from dmbot.transcript import questions as name_questions
from dmbot.transcript import speaker_question as speaker_q
from dmbot.transcript.topics import Waiting

GUILD, DM, PLAYER = ts.GUILD, ts.DM, ts.PLAYER


class Harness(ts.SessionTests):
    """The session harness without its own tests (they run in `test_sessions.py`)."""


for _name in dir(ts.SessionTests):
    if _name.startswith("test"):
        setattr(Harness, _name, None)


class Refusals(Harness):
    # The session harness's helpers, borrowed (not inherited: that class holds thousands of tests).
    said: Any = ts.SaveAndResume.said
    settle: Any = ts.SaveAndResume.settle
    filtered: Any = ts.SaveAndResume.filtered
    speaking: Any = ts.SaveAndResume.speaking
    joined_with_transcript: Any = ts.SaveAndResume.joined_with_transcript
    left_out_line: Any = ts.SaveAndResume.left_out_line
    asked_about_marin: Any = ts.SaveAndResume.asked_about_marin
    two_alike: Any = ts.SaveAndResume.two_alike

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
        cast(AsyncMock, self.bot.post_message).assert_not_awaited()
        self.assertIsNone(table.speaker_questions.open)  # closed: their words are not on the screen

    async def test_an_answer_about_a_stopped_speaker_saves_nothing(self) -> None:
        table, asked = await self.question_for_the_player()
        self.stop(PLAYER)
        text, done = await self.bot.answer_speaker_question(GUILD, asked.id, "0", DM)
        self.assertEqual((text, done), (speaker_q.GONE, True))
        self.assertTrue(all(line.entity == "" for line in table.unsaved._waiting))

    async def test_a_stop_while_the_names_are_checked_saves_nothing(self) -> None:
        _table, asked = await self.question_for_the_player()
        real = self.bot._current_names

        async def names_then_stop(t: Any) -> Any:
            got = await real(t)
            self.stop(PLAYER)  # they press Stop while the DM's answer is being checked
            return got

        self.bot._current_names = names_then_stop  # type: ignore[method-assign,assignment]
        saved = AsyncMock()
        self.bot._save_entity = saved  # type: ignore[method-assign]
        text, done = await self.bot.answer_speaker_question(GUILD, asked.id, "0", DM)
        self.assertEqual((text, done), (speaker_q.GONE, True))
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

    async def test_a_typed_line_sharing_a_moment_with_a_marker_is_not_shown_again(self) -> None:
        """Typed lines never had a marker in the live channel; one with a spoken marker's key
        must not edit that marker (#2447)."""
        table, posted, _ = await self.left_out_line()
        (run,) = table.left_out.runs
        spoken = run.lines[0]
        typed = Waiting(spoken.speaker, spoken.started_ms, "typed", 1.0, typed=True)
        edits, markers = posted.edit.await_count, set(table.hidden)
        self.assertIsNone(await self.bot._show_lines_again(table, [typed]))
        self.assertEqual((posted.edit.await_count, table.hidden), (edits, markers))

    async def test_the_same_put_back_for_someone_recorded_works(self) -> None:
        table, posted, _ = await self.left_out_line()
        (run,) = table.left_out.runs
        answer = await self.bot.put_back(GUILD, run.id, DM)
        self.assertIn("Put back 1", answer)
        self.assertIn("my boss called again", posted.edit.await_args.kwargs["content"])


class NameQuestions(Refusals):
    """ "Did they mean…?" (#296, #503): the DM is asked, and the answer is written, only while the
    speaker is recorded; a Stop takes their words down."""

    async def test_the_type_it_form_does_not_open_for_a_stopped_speaker(self) -> None:
        table, _, _, _ = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None
        self.assertEqual(self.bot.can_type_answer(GUILD, asked.id, DM), (None, asked.heard))
        self.stop(PLAYER)
        self.assertEqual(
            self.bot.can_type_answer(GUILD, asked.id, DM), (name_questions.GONE, "")
        )  # their words are not shown again

    async def test_the_unanswered_line_shows_no_words_of_a_stopped_speaker(self) -> None:
        table, _, _, _ = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None
        self.assertIn(asked.heard, self.bot._not_answered(table, asked))
        self.stop(PLAYER)
        self.assertEqual(self.bot._not_answered(table, asked), name_questions.GONE)

    async def test_a_question_is_not_posted_after_its_speaker_stops(self) -> None:
        table, _, posted, _ = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None
        table.questions.close(name_questions.EXPIRED, 0.0)
        posted.reset_mock()
        self.stop(PLAYER)
        table.questions.open = asked  # as if it were still waiting to be posted
        await self.bot._ask_dm(table, asked)
        posted.assert_not_awaited()
        self.assertIsNone(table.questions.open)

    async def test_an_answer_that_arrives_while_the_copies_are_still_being_sent(self) -> None:
        # #3210: DM 1 answered before DM 2's copy was sent. DM 1's message must not be edited
        # (it holds their answer and Undo); DM 2's late copy says another DM answered.
        table, _, _, _ = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None
        table.questions.close(name_questions.EXPIRED, 0.0)
        table.questions.open = asked  # as if it were still waiting to be posted
        first, late = MagicMock(id=1, edit=AsyncMock()), MagicMock(id=2, edit=AsyncMock())

        async def ask_live(table: Any, text: str, make_view: Any, on_sent: Any = None) -> Any:
            on_sent(first)  # DM 1's copy is sent and registered
            table.questions.close(name_questions.ANSWERED, 0.0)  # DM 1 answers
            self.bot._retire_copies(table, asked.id, first.id)
            return [first, late]  # DM 2's copy goes out afterwards

        self.bot.ask_live = ask_live  # type: ignore[method-assign]
        await self.bot._ask_dm(table, asked)
        first.edit.assert_not_awaited()
        late.edit.assert_awaited_once_with(content=name_questions.ANSWERED_ELSEWHERE, view=None)

    async def test_an_answer_about_a_stopped_speaker_writes_nothing(self) -> None:
        table, _, _, memory = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None
        self.stop(PLAYER)
        text, done, undo = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        self.assertEqual((text, done, undo), (name_questions.GONE, True, None))
        memory.add_correction.assert_not_awaited()

    async def test_a_stop_while_the_answer_is_saved_fixes_no_line(self) -> None:
        table, _, _, memory = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None

        async def save_then_stop(*_: Any, **__: Any) -> Any:
            self.stop(PLAYER)  # they press Stop while the answer is being saved
            return MagicMock(batch=41)

        memory.add_correction = AsyncMock(side_effect=save_then_stop)
        fixed = AsyncMock()
        self.bot._fix_asked_line = fixed  # type: ignore[method-assign]
        text, done, _ = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        self.assertEqual((text, done), (name_questions.GONE, True))
        fixed.assert_not_awaited()  # their line is not rewritten

    async def test_the_same_answer_for_someone_recorded_fixes_the_line(self) -> None:
        table, _, _, _ = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None
        fixed = AsyncMock(return_value=True)
        self.bot._fix_asked_line = fixed  # type: ignore[method-assign]
        _, done, _ = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        self.assertTrue(done)
        fixed.assert_awaited_once()

    async def test_earlier_lines_are_not_fixed_for_a_stopped_speaker(self) -> None:
        table, _, _, _ = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None
        rewrite = AsyncMock(return_value=True)
        self.bot._rewrite_line = rewrite  # type: ignore[method-assign]
        self.stop(PLAYER)
        changed = await self.bot._fix_earlier_lines(table, asked, "Maren", None)
        self.assertEqual(changed, 0)
        rewrite.assert_not_awaited()

    async def test_an_earlier_line_of_someone_who_stops_meanwhile_is_not_rewritten(self) -> None:
        table, _, _, _ = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None
        other = ts.OTHER_PERSON
        await self.consent.grant(GUILD, other)
        self.said(table, "then Marin speaks again", user=other, at_ms=9_000)
        item = SimpleNamespace(
            speaker=other, started_ms=9_000, heard="then Marin speaks", edits=[(5, 10, "Maren")]
        )

        def plan(*_: Any, **__: Any) -> list[Any]:
            self.stop(other)  # a Stop arrives while the lines are being worked out
            return [item]

        rewrite = AsyncMock(return_value=True)
        self.bot._rewrite_line = rewrite  # type: ignore[method-assign]
        with patch("dmbot.bot.refix.plan", plan):
            changed = await self.bot._fix_earlier_lines(table, asked, "Maren", None)
        self.assertEqual(changed, 0)
        rewrite.assert_not_awaited()


class HidingLines(Refusals):
    """`_hide_lines`: a line becomes an off-topic marker, and is listed on the DM screen, only for
    a speaker who is still recorded."""

    async def table_with_a_line(self) -> tuple[Any, Waiting]:
        table = await self.filtered("1 game\n2 game")
        self.said(table, "we sneak past the guards")
        await self.settle()
        return table, Waiting(PLAYER, 0, "we sneak past the guards", 1.0)

    async def test_a_stopped_speakers_line_is_not_listed_or_marked(self) -> None:
        table, waiting = await self.table_with_a_line()
        self.stop(PLAYER)
        with patch.object(self.bot, "_redraw_left_out") as redraw:
            await self.bot._hide_lines(table, [waiting])
        redraw.assert_not_called()
        self.assertFalse(table.left_out.runs)
        self.assertFalse(table.hidden)

    async def test_the_same_line_for_someone_recorded_is_listed(self) -> None:
        table, waiting = await self.table_with_a_line()
        with patch.object(self.bot, "_redraw_left_out") as redraw:
            await self.bot._hide_lines(table, [waiting])
        redraw.assert_called_once()
        self.assertTrue(table.left_out.runs)


class SidebarConsent(Refusals):
    """`sidebar_has_consent`: the DM sidebar asks core, which answers from the live store."""

    async def test_it_follows_a_stop_at_once(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        self.assertTrue(self.bot.sidebar_has_consent(GUILD, PLAYER))
        self.stop(PLAYER)
        self.assertFalse(self.bot.sidebar_has_consent(GUILD, PLAYER))
        self.assertFalse(self.bot.sidebar_has_consent(GUILD, 424242))  # never agreed


class UndoingAFix(Refusals):
    """`undo_fix` (#296): Undo for a name fix does nothing for a speaker who has since stopped
    (the mid-flow stops are in `test_sessions.py`)."""

    fixed_from_a_suggestion: Any = ts.SaveAndResume.fixed_from_a_suggestion

    async def test_undo_after_a_stop_writes_and_changes_nothing(self) -> None:
        table, _, memory = await self.fixed_from_a_suggestion()
        (note,) = table.fix_notes.notes
        self.stop(PLAYER)
        answer, allow = await self.bot.undo_fix(GUILD, note.id, DM)
        self.assertEqual(answer, fix_notes.STOPPED)
        self.assertIsNone(allow)
        memory.add_correction.assert_not_awaited()  # no "keep as heard" rule for them
        self.assertFalse(note.undone)
        (line,) = list(table.unsaved._waiting)
        self.assertEqual(line.text, "then Hrothgar roars")  # their words were not put back

    async def test_the_same_undo_for_someone_recorded_works(self) -> None:
        table, _, memory = await self.fixed_from_a_suggestion()
        (note,) = table.fix_notes.notes
        answer, allow = await self.bot.undo_fix(GUILD, note.id, DM)
        self.assertNotEqual(answer, fix_notes.STOPPED)
        memory.add_correction.assert_awaited_once()
        self.assertIsNotNone(allow)


class FinishingTopics(Refusals):
    """`_finish_topics` at shutdown (#53): lines still waiting for a topic are kept as game talk
    for the names scan only if their speaker is still recorded."""

    async def table_with_a_waiting_line(self) -> Any:
        table = await self.filtered("1 game\n2 game")
        self.said(table, "we sneak past the guards")
        await self.settle()
        self.assertTrue(table.topics.lines)  # the setup left a line waiting for its topic
        return table

    async def test_a_stopped_speakers_waiting_line_is_not_kept_at_shutdown(self) -> None:
        table = await self.table_with_a_waiting_line()
        self.stop(PLAYER)
        self.bot._closing = True
        await self.bot._finish_topics(table)
        self.assertEqual(table.heard, [])

    async def test_the_same_shutdown_for_someone_recorded_keeps_it(self) -> None:
        table = await self.table_with_a_waiting_line()
        self.bot._closing = True
        await self.bot._finish_topics(table)
        self.assertEqual([text for _, text in table.heard], ["we sneak past the guards"])
