"""Unanswered "Did they mean…?" questions in the after-session check (#1572): the rules (pure), the
table (a real Postgres), keeping them when a session ends, the DM's answers and the screen. Made-up
names and numbers throughout; no real speech."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.audio.segmenter import Segmenter
from dmbot.bot import DMBot
from dmbot.campaigns import CampaignStore
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.memory.store import MemoryStore
from dmbot.sessions import SessionStore
from dmbot.table import Table
from dmbot.transcript import questions
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.suggestions import SuggestionStore
from dmbot.transcript.waiting_questions import (
    EXPIRE_S,
    MAX_PER_SESSION,
    Unanswered,
    WaitingQuestionStore,
    fix_words,
)
from dmbot.ui import heard_review
from dmbot.ui import names as ui
from tests.pg import DatabaseTest
from tests.test_memory_names import FakeResponse

GUILD, OTHER, DM, PLAYER, MANAGER, SCREEN = 1, 2, 7, 8, 10, 3
START = 1_700_000_000
MS = START * 1000


class Words(unittest.TestCase):
    def test_the_misheard_words_are_written_as_the_name(self) -> None:
        self.assertEqual(fix_words("I met Marin there", "Marin", "Maren"), "I met Maren there")
        self.assertEqual(fix_words("i met MARIN.", "Marin", "Maren"), "i met Maren.")

    def test_a_possessive_ending_stays(self) -> None:
        self.assertEqual(fix_words("Marin's dog", "Marin's", "Maren"), "Maren's dog")

    def test_not_when_the_words_are_missing_or_come_twice(self) -> None:
        self.assertIsNone(fix_words("I met Maren there", "Marin", "Maren"))  # changed since
        self.assertIsNone(fix_words("Marin and Marin", "Marin", "Maren"))  # which one?
        self.assertIsNone(fix_words("Marinade", "Marin", "Maren"))  # whole words only


class Table_(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.now = float(START)
        self.campaigns = CampaignStore(self.db)
        self.transcripts = TranscriptStore(self.db)
        self.store = WaitingQuestionStore(self.db, clock=lambda: self.now)
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        self.session = await self.transcripts.open_session(GUILD, self.campaign.id, START)
        await self.transcripts.add_lines(
            GUILD,
            self.session,
            [Line(MS + 1000, PLAYER, "I met Marin there", "I met Marin there")]
            + [Line(MS + 2000 + i, PLAYER, f"word{i}", f"word{i}") for i in range(40)],
        )

    def item(self, at: int = 1000, heard: str = "Marin") -> Unanswered:
        return Unanswered(PLAYER, MS + at, heard, ("a" * 32, "b" * 32))

    async def test_a_kept_question_shows_with_its_line_as_it_reads_now(self) -> None:
        await self.store.save(GUILD, self.campaign.id, self.session, [self.item()])
        (w,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertEqual((w.heard, w.text, w.speaker_id), ("Marin", "I met Marin there", PLAYER))
        self.assertEqual(w.option_ids, ("a" * 32, "b" * 32))

    async def test_at_most_25_per_session_and_the_oldest_are_dropped(self) -> None:
        items = [self.item(2000 + i, f"w{i}") for i in range(30)]
        saved = await self.store.save(GUILD, self.campaign.id, self.session, items)
        self.assertEqual(saved, MAX_PER_SESSION)
        got = await self.store.waiting(GUILD, self.campaign.id)
        self.assertEqual([w.heard for w in got], [f"w{i}" for i in range(5, 30)])

    async def test_a_word_in_a_line_is_kept_once(self) -> None:
        await self.store.save(GUILD, self.campaign.id, self.session, [self.item()])
        again = await self.store.save(GUILD, self.campaign.id, self.session, [self.item()])
        self.assertEqual(again, 0)

    async def test_a_line_that_is_gone_takes_its_question_out(self) -> None:
        await self.store.save(GUILD, self.campaign.id, self.session, [self.item(at=999_999)])
        self.assertEqual(await self.store.waiting(GUILD, self.campaign.id), [])

    async def test_a_question_expires_after_a_week_quietly(self) -> None:
        await self.store.save(GUILD, self.campaign.id, self.session, [self.item()])
        self.now += EXPIRE_S - 1
        self.assertEqual(len(await self.store.waiting(GUILD, self.campaign.id)), 1)
        self.now += 2
        self.assertEqual(await self.store.waiting(GUILD, self.campaign.id), [])

    async def test_open_questions_are_counted_by_session(self) -> None:
        self.assertEqual(await self.store.count_for_session(GUILD, self.session), 0)
        await self.store.save(
            GUILD, self.campaign.id, self.session, [self.item(), self.item(2000, "w")]
        )
        self.assertEqual(await self.store.count_for_session(GUILD, self.session), 2)
        self.assertEqual(await self.store.count_for_session(GUILD, "x" * 32), 0)  # another session
        self.assertEqual(await self.store.count_for_session(OTHER, self.session), 0)  # server
        (first, _) = await self.store.waiting(GUILD, self.campaign.id)
        await self.store.decide(GUILD, first.id, "answered", DM)
        self.assertEqual(await self.store.count_for_session(GUILD, self.session), 1)
        self.now += EXPIRE_S + 1  # a week on, it expires quietly and may release a held recap
        self.assertEqual(await self.store.count_for_session(GUILD, self.session), 0)

    async def test_only_one_press_closes_it_and_a_failed_save_reopens_it(self) -> None:
        await self.store.save(GUILD, self.campaign.id, self.session, [self.item()])
        (w,) = await self.store.waiting(GUILD, self.campaign.id)
        self.assertTrue(await self.store.decide(GUILD, w.id, "answered", DM))
        self.assertFalse(await self.store.decide(GUILD, w.id, "answered", DM))
        self.assertIsNone(await self.store.get(GUILD, w.id))
        await self.store.reopen(GUILD, w.id)
        self.assertIsNotNone(await self.store.get(GUILD, w.id))

    async def test_another_server_sees_nothing_and_a_speaker_can_be_forgotten(self) -> None:
        await self.store.save(GUILD, self.campaign.id, self.session, [self.item()])
        self.assertEqual(await self.store.waiting(OTHER, self.campaign.id), [])
        self.assertEqual(await self.store.forget_speaker(OTHER, PLAYER), 0)
        self.assertEqual(await self.store.forget_speaker(GUILD, PLAYER), 1)
        self.assertEqual(await self.store.waiting(GUILD, self.campaign.id), [])


class Flow(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.consent = ConsentStore(self.db)
        self.campaigns = CampaignStore(self.db)
        self.memory = MemoryStore(self.db)
        self.transcripts = TranscriptStore(self.db)
        self.store = WaitingQuestionStore(self.db)
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            self.consent,
            self.campaigns,
            SessionStore(self.db),
            memory=self.memory,
            transcripts=self.transcripts,
            suggestions=SuggestionStore(self.db),
            heard_questions=self.store,
        )
        self.posted = AsyncMock(return_value=True)
        self.bot.post = self.posted  # type: ignore[method-assign]
        names = {DM: "Sam", PLAYER: "Mia"}
        self.guild = MagicMock(spec=discord.Guild)
        self.guild.id = GUILD
        self.guild.get_member = lambda uid: (
            SimpleNamespace(display_name=names[uid]) if uid in names else None
        )
        self.bot.get_guild = lambda gid: self.guild if gid == GUILD else None  # type: ignore[method-assign]
        await self.consent.grant(GUILD, PLAYER)
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        self.maren = await ui.save_name(self.memory, self.campaign, "Maren", "npc", [], [])
        self.marron = await ui.save_name(self.memory, self.campaign, "Marron", "npc", [], [])
        self.session = await self.transcripts.open_session(GUILD, self.campaign.id, START)
        await self.transcripts.add_lines(
            GUILD,
            self.session,
            [Line(MS + 1000, PLAYER, "I met Marin there", "I met Marin there")],
        )

    def asked(self, heard: str = "Marin", at: int = 1000, speaker: int = PLAYER) -> Any:
        return questions.Asked(
            id="q1",
            speaker=speaker,
            heard=heard,
            options=((self.maren.id, "Maren"), (self.marron.id, "Marron")),
            asked_at=0.0,
            started_ms=MS + at,
            line="I met Marin there",
        )

    def table(self) -> Table:
        table = Table(
            guild_id=GUILD,
            voice_channel_id=2,
            screen_channel_id=SCREEN,
            dm_user_id=DM,
            segmenter=Segmenter(GUILD),
            campaign_id=self.campaign.id,
            campaign_name="Frostmaiden",
            started_at=START,
        )
        table.transcript_session_id = self.session
        self.bot.tables[GUILD] = table
        return table

    async def kept(self) -> int:
        table = self.table()
        table.unanswered = [self.asked()]
        await self.bot.save_unanswered(table)
        (w,) = await self.store.waiting(GUILD, self.campaign.id)
        return w.id

    async def line(self) -> tuple[str, str]:
        (line,) = await self.transcripts.lines(GUILD, self.session)
        return line.heard, line.text

    # ---- keeping them when the session ends ----

    async def test_a_session_end_keeps_the_unanswered_and_tells_the_dm_screen_how_many(
        self,
    ) -> None:
        table = self.table()
        table.unanswered = [self.asked()]
        await self.bot.save_unanswered(table)
        self.assertEqual(len(await self.store.waiting(GUILD, self.campaign.id)), 1)
        (call,) = self.posted.await_args_list
        self.assertEqual(call.args[0], SCREEN)
        self.assertIn("1 word needs a check", call.args[1])
        self.assertNotIn("Marin", call.args[1])  # how many, never what was said

    async def test_nothing_is_kept_for_a_speaker_who_stopped_or_never_agreed(self) -> None:
        table = self.table()
        table.unanswered = [self.asked(speaker=PLAYER + 50)]
        await self.bot.save_unanswered(table)
        self.assertEqual(await self.store.waiting(GUILD, self.campaign.id), [])
        self.posted.assert_not_awaited()

    async def test_an_expired_or_ended_question_is_remembered_and_a_stopped_speakers_goes(
        self,
    ) -> None:
        table = self.table()
        self.bot._keep_unanswered(table, self.asked())
        self.assertEqual(len(table.unanswered), 1)
        split = questions.Asked(
            "q2", PLAYER, "x", self.asked().options, 0.0, line="a b", prev_line="c"
        )
        self.bot._keep_unanswered(table, split)  # a name split across two pieces: no one line
        self.assertEqual(len(table.unanswered), 1)
        for n in range(40):
            self.bot._keep_unanswered(table, self.asked(heard=f"w{n}"))
        self.assertEqual(len(table.unanswered), MAX_PER_SESSION)  # the oldest went first

    # ---- the DM's answers ----

    async def test_a_picked_name_fixes_the_cleaned_line_not_the_raw_one_and_learns_the_rule(
        self,
    ) -> None:
        qid = await self.kept()
        answer, done = await self.bot.answer_waiting_question(GUILD, qid, "0", DM)
        self.assertTrue(done)
        self.assertIn("is now written **Maren**", answer)
        self.assertEqual(await self.line(), ("I met Marin there", "I met Maren there"))
        (rule,) = await self.memory.corrections(GUILD, self.campaign.id)
        self.assertEqual((rule.heard, rule.action, rule.entity_id), ("Marin", "fix", self.maren.id))
        self.assertEqual(await self.store.waiting(GUILD, self.campaign.id), [])

    async def test_keep_as_heard_adds_the_keep_rule_and_leaves_the_line(self) -> None:
        qid = await self.kept()
        answer, done = await self.bot.answer_waiting_question(GUILD, qid, questions.KEEP, DM)
        self.assertTrue(done)
        self.assertIn("won't change", answer)
        (rule,) = await self.memory.corrections(GUILD, self.campaign.id)
        self.assertEqual((rule.action, rule.entity_id), ("keep", None))
        self.assertEqual(await self.line(), ("I met Marin there", "I met Marin there"))

    async def test_a_typed_name_that_is_new_waits_in_check_new_names(self) -> None:
        qid = await self.kept()
        answer, done = await self.bot.answer_waiting_question(
            GUILD, qid, questions.TYPE, DM, "Marek"
        )
        self.assertTrue(done)
        self.assertIn("Check new names", answer)
        self.assertEqual(await self.line(), ("I met Marin there", "I met Marek there"))

    async def test_only_a_dm_answers_and_nothing_is_written_otherwise(self) -> None:
        qid = await self.kept()
        for who in (MANAGER, PLAYER):
            answer, done = await self.bot.answer_waiting_question(GUILD, qid, "0", who)
            self.assertEqual((answer, done), (questions.ONLY_DM, False))
        self.assertEqual(await self.memory.corrections(GUILD, self.campaign.id), [])
        self.assertEqual(len(await self.store.waiting(GUILD, self.campaign.id)), 1)

    async def test_a_second_press_does_nothing_more(self) -> None:
        qid = await self.kept()
        await self.bot.answer_waiting_question(GUILD, qid, "0", DM)
        answer, done = await self.bot.answer_waiting_question(GUILD, qid, "1", DM)
        self.assertEqual((answer, done), (questions.EXPIRED, True))
        self.assertEqual(len(await self.memory.corrections(GUILD, self.campaign.id)), 1)

    async def test_a_line_that_changed_since_stays_but_the_rule_is_still_learned(self) -> None:
        qid = await self.kept()
        await self.transcripts.relabel_line(GUILD, self.session, PLAYER, MS + 1000, "I met Maren")
        answer, done = await self.bot.answer_waiting_question(GUILD, qid, "0", DM)
        self.assertTrue(done)
        self.assertIn("That line stays as heard", answer)
        self.assertEqual(len(await self.memory.corrections(GUILD, self.campaign.id)), 1)

    async def test_a_speaker_who_stopped_meanwhile_has_nothing_written(self) -> None:
        qid = await self.kept()
        self.bot.stop_recording(GUILD, PLAYER)
        answer, done = await self.bot.answer_waiting_question(GUILD, qid, "0", DM)
        self.assertEqual((answer, done), (questions.GONE, True))
        self.assertEqual(await self.memory.corrections(GUILD, self.campaign.id), [])
        self.assertEqual(await self.line(), ("I met Marin there", "I met Marin there"))

    async def test_a_name_removed_since_is_not_written(self) -> None:
        qid = await self.kept()
        await self.memory.set_entity_status(
            GUILD, self.campaign.id, self.maren.id, "rejected", source="dm"
        )
        self.bot.lookup.mark_stale(GUILD, self.campaign.id)  # type: ignore[union-attr]
        answer, done = await self.bot.answer_waiting_question(GUILD, qid, "0", DM)
        self.assertEqual((answer, done), (questions.NAME_GONE, True))
        self.assertEqual(await self.line(), ("I met Marin there", "I met Marin there"))

    async def test_a_secret_name_typed_by_the_dm_is_never_written_in_the_transcript(self) -> None:
        await ui.save_name(self.memory, self.campaign, "Vane", "npc", [], ["Silas Vane"])
        qid = await self.kept()
        answer, done = await self.bot.answer_waiting_question(
            GUILD, qid, questions.TYPE, DM, "Silas Vane"
        )
        self.assertEqual((answer, done), (questions.TYPED_SECRET, False))
        self.assertEqual(await self.line(), ("I met Marin there", "I met Marin there"))
        self.assertEqual(await self.memory.corrections(GUILD, self.campaign.id), [])
        self.assertEqual(len(await self.store.waiting(GUILD, self.campaign.id)), 1)  # still open

    async def test_a_stale_button_leaves_the_question_open(self) -> None:
        qid = await self.kept()
        answer, done = await self.bot.answer_waiting_question(GUILD, qid, "7", DM)
        self.assertEqual((answer, done), (questions.EXPIRED, False))
        self.assertEqual(len(await self.store.waiting(GUILD, self.campaign.id)), 1)

    async def test_later_leaves_the_question_waiting_for_next_time(self) -> None:
        await self.kept()
        it = self.it()
        review = heard_review.HeardReview(self.campaign.id)
        await review.current_text(it)
        await review._later(it)
        self.assertEqual(len(await self.store.waiting(GUILD, self.campaign.id)), 1)

    async def test_a_dm_who_is_no_longer_one_sees_no_more_questions(self) -> None:
        await self.kept()
        review = heard_review.HeardReview(self.campaign.id)
        self.assertIsNone(await review.current_text(self.it(MANAGER)))

    # ---- the screen ----

    def it(self, user_id: int = DM) -> Any:
        user = MagicMock(spec=discord.Member)
        user.id = user_id
        user.guild_permissions = discord.Permissions(manage_guild=True)
        return SimpleNamespace(
            client=self.bot,
            type=discord.InteractionType.component,
            guild=SimpleNamespace(id=GUILD),
            guild_id=GUILD,
            user=user,
            response=FakeResponse(),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_the_dm_sees_the_question_with_the_names_and_the_other_buttons(self) -> None:
        await self.kept()
        it = self.it()
        await heard_review.start_heard_review(it, self.campaign.id)
        text, kw = it.response.sent[0]
        self.assertIn('Mia say "Marin"', text)
        self.assertNotIn("Ignore", text)  # after the game there is nothing to ignore
        labels = [str(b.label) for b in kw["view"].children]
        self.assertEqual(
            labels,
            ["Maren", "Marron", questions.TYPE_LABEL, questions.keep_label("Marin"), "⏳ Later"],
        )

    async def test_a_manager_who_is_not_a_dm_sees_nothing(self) -> None:
        await self.kept()
        it = self.it(MANAGER)
        await heard_review.start_heard_review(it, self.campaign.id)
        self.assertEqual(it.response.sent[0][0], heard_review.ONLY_DMS)

    async def test_with_nothing_waiting_it_says_so(self) -> None:
        it = self.it()
        await heard_review.start_heard_review(it, self.campaign.id)
        self.assertEqual(it.response.sent[0][0], heard_review.NOTHING_WAITING)

    async def test_the_button_works_after_a_restart(self) -> None:
        button = heard_review.HeardReviewButton(self.campaign.id)
        cid = button.item.custom_id or ""
        self.assertLess(len(cid), 100)
        match = heard_review.HeardReviewButton.__discord_ui_compiled_template__.fullmatch(cid)
        assert match is not None
        again = await heard_review.HeardReviewButton.from_custom_id(self.it(), button.item, match)
        self.assertEqual(again.campaign_id, self.campaign.id)


if __name__ == "__main__":
    unittest.main()
