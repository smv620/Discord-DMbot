"""Off the record by saying it (#2466, slice 2): "Hey DMbot, off the record", said or typed, pauses
the speaker only; the line that said so is in neither transcript; and a stretch off the record
leaves one marker line, for the person, in the saved transcript. Invented names throughout."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.audio.segmenter import Segmenter, Utterance
from dmbot.bot import DMBot, Table
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.consent_dm import ResumeButton
from dmbot.off_the_record import WAKE_MINUTES, Paused
from dmbot.transcript.models import OFF_RECORD, Line

GUILD, VOICE, SCREEN = 1, 20, 30
DM, MIA = 7, 8
C1 = "a1" * 16


class Wake(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.consent = ConsentStore(cast(Any, None))
        self.consent._store(GUILD, frozenset({DM, MIA}))
        stores = SimpleNamespace(get=AsyncMock(return_value=None))
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            self.consent,
            stores,
            MagicMock(),  # type: ignore[arg-type]
        )
        self.bot.transcripts = MagicMock()  # (lines are only kept while there is a transcript)
        self.woken: list[tuple[int, int]] = []
        self.bot._wake_off_the_record = lambda g, u: self.woken.append((g, u))  # type: ignore[method-assign]
        self.table = Table(GUILD, VOICE, SCREEN, dm_user_id=DM, segmenter=Segmenter(GUILD))
        self.table.listening = True
        self.table.campaign_id = C1
        self.table.dm_user_ids = frozenset({DM})
        self.table.transcript_session_id = "session-1"
        self.bot.tables[GUILD] = self.table

    def said(self, user: int, text: str) -> None:
        utterance = Utterance(GUILD, user, 0, 0, bytes(32000), self.table.segmenter.session)
        self.bot._deliver_transcript(utterance, text)

    def kept(self) -> list[str]:
        return [x.heard for x in self.table.unsaved.lines()]

    async def test_saying_it_to_dmbot_pauses_the_speaker_and_the_line_is_in_no_transcript(
        self,
    ) -> None:
        self.said(MIA, "Hey DMbot, off the record")
        self.assertEqual(self.woken, [(GUILD, MIA)])
        self.assertEqual(self.kept(), [])  # not saved, so in neither version
        self.assertEqual(len(self.table.heard), 0)  # nor fed to the names scan
        self.assertEqual(self.table.recent.scene(lambda u: "x", 0.0, lambda u: True), "")

    async def test_the_plain_phrase_is_just_talk_and_is_kept(self) -> None:
        self.said(DM, "Off the record, the baron is a spy, says the informant.")
        self.assertEqual(self.woken, [])
        self.assertEqual(len(self.kept()), 1)

    async def test_typed_in_the_table_chat_does_the_same_and_the_line_is_not_kept(self) -> None:
        self.bot._deliver_typed(self.table, MIA, "hey dmbot, off the record", 1000, 5)
        self.assertEqual(self.woken, [(GUILD, MIA)])
        self.assertEqual(self.kept(), [])
        self.bot._deliver_typed(self.table, MIA, "we take a long rest", 2000, 6)
        self.assertEqual(len(self.kept()), 1)

    async def test_a_marker_is_added_for_the_person_with_when_and_how_long(self) -> None:
        self.bot._add_gaps(self.table, [(MIA, 1_700_000_000.0, 1_700_000_300.0)])
        (marker,) = self.table.unsaved.lines()
        self.assertEqual(
            (marker.user_id, marker.sidebar, marker.started_ms, marker.duration_ms),
            (MIA, OFF_RECORD, 1_700_000_000_000, 300_000),
        )

    async def test_the_tables_break_marks_only_those_at_the_table_who_agreed(self) -> None:
        stranger = 99  # in the voice channel but never agreed
        here = SimpleNamespace(members=[SimpleNamespace(id=DM), SimpleNamespace(id=stranger)])
        self.bot.get_channel = lambda cid: here  # type: ignore[method-assign,assignment]
        self.bot._add_gaps(self.table, [(0, 1000.0, 1900.0), (MIA, 5000.0, 5000.5)])
        # Mia agreed but was not in the channel; a blink marks nobody; the stranger never agreed.
        self.assertEqual(
            [(x.user_id, x.duration_ms) for x in self.table.unsaved.lines()], [(DM, 900_000)]
        )

    async def test_no_transcript_no_marker(self) -> None:
        self.bot.transcripts = None
        self.bot._add_gaps(self.table, [(MIA, 1000.0, 1900.0)])
        self.assertEqual(self.table.unsaved.lines(), [])

    async def test_the_phrase_pauses_for_five_minutes_and_tells_them_privately(self) -> None:
        pause = AsyncMock(return_value=Paused(True, 1_700_000_300, told=True))
        self.bot.off_the_record.announce_pause = pause  # type: ignore[method-assign]
        sent = AsyncMock()
        self.bot._dm_message = sent  # type: ignore[method-assign]
        await DMBot._pause_by_phrase(self.bot, GUILD, MIA)
        pause.assert_awaited_once_with(GUILD, MIA, WAKE_MINUTES, said_it=True)
        self.assertIn("What you said just now was not saved.", sent.await_args.args[1])
        text, view = sent.await_args.args[1], sent.await_args.args[2]
        self.assertIn("off the record", text)
        self.assertIn("<t:1700000300:R>", text)
        self.assertTrue(any(isinstance(i, ResumeButton) for i in view.children))

    async def test_they_are_off_the_record_in_core_before_anything_awaits(self) -> None:
        """A line that arrives a moment after the phrase is dropped by the consent re-check."""
        real = DMBot._wake_off_the_record  # (the fixture stubs it)
        self.bot.off_the_record._listening = lambda g: True
        self.bot._track = MagicMock()  # type: ignore[method-assign]
        real(self.bot, GUILD, MIA)
        self.assertFalse(self.consent.has_consent(GUILD, MIA))  # no await has run yet
        self.assertTrue(self.consent.has_consent(GUILD, DM))
        self.bot._track.call_args.args[0].close()  # (the announcement, not run here)

    async def test_someone_not_recorded_is_not_paused_and_nothing_is_said(self) -> None:
        real = DMBot._wake_off_the_record
        self.bot._track = MagicMock()  # type: ignore[method-assign]
        real(self.bot, GUILD, 555)  # never agreed
        self.bot._track.assert_not_called()

    async def test_the_phrase_is_dropped_even_when_the_session_is_ending(self) -> None:
        self.bot.tables.pop(GUILD)
        self.bot._ending[GUILD] = [self.table]
        self.said(MIA, "Hey DMbot, off the record")
        self.assertEqual((self.woken, self.kept()), ([], []))  # kept out, and nobody paused

    async def test_words_written_down_before_the_phrase_arrived_are_dropped_too(self) -> None:
        later = Line(5_000, MIA, "and then we ambush them", "and then we ambush them")
        earlier = Line(1_000, MIA, "we reach the gate", "we reach the gate")
        self.table.unsaved.add(earlier)
        self.table.unsaved.add(later)  # a clip said after the phrase, written down first
        utterance = Utterance(GUILD, MIA, 4_000, 4_500, bytes(32000), self.table.segmenter.session)
        self.bot._deliver_transcript(utterance, "Hey DMbot, off the record")
        self.assertEqual(self.kept(), ["we reach the gate"])

    async def test_typed_in_the_dmbot_chat_pauses_them_instead_of_asking_a_question(self) -> None:
        self.table.dmbchat_channel_id = 55
        asked: list[str] = []
        self.bot.sidebar.ask_in_chat = lambda table, user, text: asked.append(text)  # type: ignore[method-assign]
        self.bot._hear_dmbchat(self.chat(MIA, "Hey DMbot, off the record"))
        self.assertEqual((self.woken, asked), ([(GUILD, MIA)], []))
        self.bot._hear_dmbchat(self.chat(MIA, "does fireball need line of sight?"))
        self.assertEqual(asked, ["does fireball need line of sight?"])  # a question still is one

    def chat(self, user: int, text: str) -> Any:
        message = MagicMock()
        message.guild = SimpleNamespace(id=GUILD)
        message.channel = SimpleNamespace(id=55)
        message.author = SimpleNamespace(id=user, bot=False)
        message.webhook_id = None
        message.type = discord.MessageType.default
        message.interaction_metadata = None
        message.clean_content = text
        return message

    async def test_someone_already_paused_who_says_it_again_is_paused_again_not_asked(self) -> None:
        self.table.dmbchat_channel_id = 55
        self.consent.pause(GUILD, MIA, 60)
        asked: list[str] = []
        self.bot.sidebar.ask_in_chat = lambda table, user, text: asked.append(text)  # type: ignore[method-assign]
        self.bot._hear_dmbchat(self.chat(MIA, "Hey DMbot, off the record"))
        self.assertEqual((self.woken, asked), ([(GUILD, MIA)], []))
