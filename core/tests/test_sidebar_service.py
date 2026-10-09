"""The DM sidebar's ways in (#935): voice messages, typed questions and the table, with the
rules around them. No Discord and no database: a fake host and a fake answer engine."""

import asyncio
import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.ai import AIError
from dmbot.sidebar import service
from dmbot.sidebar.ask import AskLimiter
from dmbot.sidebar.service import Recent, SidebarService
from dmbot.transcript.models import SIDEBAR_ANSWER, SIDEBAR_QUESTION, Line
from tests.test_sidebar_memo import ogg_opus

GUILD, OTHER_GUILD, DM, CO_DM, PLAYER, STARTER = 1, 2, 7, 70, 8, 99
FIREBALL = "find if you need line of sight for fireball"


@dataclass
class FakeAnswer:
    text: str = "No: its origin is a point you choose. (SRD 5.2.1, sure)"
    in_game: bool = True
    refused: bool = False
    model: str = "model-1"
    prompt_version: str = "prompt-1"
    sources: tuple[str, ...] = ("SRD 5.2.1 p. 241",)
    parts: tuple[str, ...] = ()


class FakeAnswerer:
    def __init__(self, answer: FakeAnswer | None = None) -> None:
        self.answer_to_give = answer or FakeAnswer()
        self.asked: list[tuple[Any, str, str]] = []
        self.askers: list[int] = []
        self.error: Exception | None = None
        self.gate: asyncio.Event | None = None

    async def answer(
        self, campaign: Any, question: str, *, asker_id: int, scene: str = ""
    ) -> FakeAnswer:
        self.asked.append((campaign, question, scene))
        self.askers.append(asker_id)
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.answer_to_give


def make_table(guild: int = GUILD, campaign: str = "c1", name: str = "Frostmaiden") -> Any:
    return SimpleNamespace(
        guild_id=guild,
        campaign_id=campaign,
        campaign_name=name,
        dm_user_id=STARTER,  # whoever pressed Start; not necessarily a campaign DM
        dm_user_ids=frozenset({DM, CO_DM}),
        segmenter=SimpleNamespace(session=5),
        recent=Recent.new(),
        sidebar_limiter=AskLimiter(),
    )


@dataclass
class FakeHost:
    tables: dict[int, Any] = field(default_factory=dict)
    agreed: set[tuple[int, int]] = field(default_factory=set)
    saved: list[Line] = field(default_factory=list)
    dms: list[tuple[int, str]] = field(default_factory=list)
    screen: list[str] = field(default_factory=list)
    heard_as: str | None = "find if you need line of sight for fireball"
    transcribed: int = 0
    can_dm: bool = True
    refusal: str = "forbidden"
    withdraw_during_transcribe: bool = False

    def sidebar_has_consent(self, guild_id: int, user_id: int) -> bool:
        return (guild_id, user_id) in self.agreed

    def name_of(self, guild_id: int, user_id: int) -> str:
        return {DM: "Sam", PLAYER: "Mia"}.get(user_id, "Someone")

    def sidebar_server_name(self, guild_id: int) -> str:
        return {GUILD: "Table One", OTHER_GUILD: "Table Two"}.get(guild_id, "x")

    async def sidebar_campaign(self, guild_id: int, campaign_id: str) -> Any:
        return SimpleNamespace(id=campaign_id, guild_id=guild_id)

    def consent_request(self, table: Any) -> tuple[str, Any]:
        return "Can DMbot record you?", "the-consent-buttons"

    async def sidebar_transcribe(self, table: Any, utterance: Any) -> str | None:
        self.transcribed += 1
        if self.withdraw_during_transcribe:
            self.agreed.discard((table.guild_id, utterance.user_id))
        return self.heard_as

    def sidebar_clean(self, table: Any, text: str) -> str:
        return text.replace("Fire ball", "Fireball")

    async def sidebar_send_dm(self, user_id: int, text: str) -> str:
        self.dms.append((user_id, text))
        return "sent" if self.can_dm else self.refusal

    def sidebar_save(self, table: Any, line: Line) -> None:
        self.saved.append(line)

    def sidebar_stt(self) -> str:
        return "deepgram nova-3 api.example"

    async def sidebar_tell_screen(self, table: Any, text: str) -> None:
        self.screen.append(text)


def dm_message(
    user: int = DM,
    content: str = "",
    *,
    voice: bytes | None = None,
    size: int | None = None,
) -> Any:
    attachment = None
    if voice is not None:
        attachment = SimpleNamespace(size=size or len(voice), read=AsyncMock(return_value=voice))
    return SimpleNamespace(
        author=SimpleNamespace(id=user, bot=False),
        guild=None,
        flags=SimpleNamespace(voice=voice is not None),
        attachments=[attachment] if attachment else [],
        content=content,
        channel=SimpleNamespace(send=AsyncMock()),
    )


def sent(message: Any) -> list[str]:
    return [c.args[0] for c in message.channel.send.await_args_list if c.args]


class PrivateMessages(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.host = FakeHost(tables={GUILD: make_table()}, agreed={(GUILD, DM), (GUILD, CO_DM)})
        self.answerer = FakeAnswerer()
        self.sidebar = SidebarService(self.host)
        self.sidebar.answerer = self.answerer

    async def test_a_typed_question_is_answered_privately_and_kept_in_the_raw_transcript(
        self,
    ) -> None:
        message = dm_message(content="find if you need line of sight for Fire ball")
        await self.sidebar.on_dm_message(message)
        self.assertEqual(sent(message), [FakeAnswer().text])
        ((campaign, question, _),) = self.answerer.asked
        self.assertEqual(
            (campaign.id, question), ("c1", "find if you need line of sight for Fireball")
        )
        question_line, answer_line = self.host.saved
        self.assertEqual((question_line.user_id, question_line.sidebar), (DM, SIDEBAR_QUESTION))
        self.assertEqual(question_line.heard, "find if you need line of sight for Fire ball")
        self.assertEqual(question_line.text, "find if you need line of sight for Fireball")
        self.assertEqual((answer_line.user_id, answer_line.sidebar), (DM, SIDEBAR_ANSWER))
        self.assertEqual(answer_line.heard, FakeAnswer().text)

    async def test_every_line_records_where_it_came_from(self) -> None:
        await self.sidebar.on_dm_message(dm_message(content="check how grappling works"))
        asked, answered = self.host.saved
        self.assertEqual((asked.lineage.via, asked.lineage.stt), ("typed", ""))  # no speech
        self.assertEqual(len(asked.lineage.ref), 6)
        self.assertEqual(answered.lineage.reply_to, asked.lineage.ref)
        self.assertEqual(
            (answered.lineage.model, answered.lineage.prompt, answered.lineage.sources),
            ("model-1", "prompt-1", ("SRD 5.2.1 p. 241",)),
        )

    async def test_a_voice_message_records_the_speech_to_text(self) -> None:
        data = ogg_opus(1.0)
        if data is None:
            self.skipTest("this PyAV can't encode Opus")
        await self.sidebar.on_dm_message(dm_message(voice=data))
        asked, _ = self.host.saved
        self.assertEqual(
            (asked.lineage.via, asked.lineage.stt), ("voice-memo", self.host.sidebar_stt())
        )

    async def test_the_engine_is_told_who_asked(self) -> None:
        await self.sidebar.on_dm_message(dm_message(CO_DM, "check how grappling works"))
        self.assertEqual(self.answerer.askers, [CO_DM])

    async def test_a_rule_card_comes_as_its_parts(self) -> None:
        card = (
            "**Fireball** (level 3)\n" + " ".join(["text"] * 40),
            "More of it.\nRead it outside Discord: https://x",
        )
        self.answerer.answer_to_give = FakeAnswer("Fireball: 8d6.", parts=card)
        message = dm_message(content="check the spell description of fireball")
        await self.sidebar.on_dm_message(message)
        self.assertEqual(sent(message), list(card))

    async def test_the_engines_plain_words_are_shown_and_nothing_is_kept(self) -> None:
        self.answerer.error = AIError("That took too long. Ask again.")
        message = dm_message(content="check how grappling works")
        await self.sidebar.on_dm_message(message)
        self.assertEqual(sent(message), ["That took too long. Ask again."])
        self.assertEqual(self.host.saved, [])

    async def test_one_question_at_a_time_for_a_campaign_even_from_two_dms(self) -> None:
        self.answerer.gate = asyncio.Event()
        first = dm_message(DM, "check how grappling works")
        task = asyncio.create_task(self.sidebar.on_dm_message(first))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        second = dm_message(CO_DM, "check how flanking works")
        await self.sidebar.on_dm_message(second)
        self.assertEqual(sent(second), [service.NOT_SOON_AGAIN])
        self.answerer.gate.set()
        await task
        self.assertEqual(len(self.answerer.asked), 1)

    async def test_a_co_dm_may_ask_too(self) -> None:
        message = dm_message(CO_DM, "check how grappling works")
        await self.sidebar.on_dm_message(message)
        self.assertEqual(len(self.answerer.asked), 1)

    async def test_a_player_gets_nothing_and_nothing_is_kept(self) -> None:
        self.host.agreed.add((GUILD, PLAYER))
        message = dm_message(PLAYER, "check how grappling works")
        await self.sidebar.on_dm_message(message)
        self.assertEqual(sent(message), [])  # not a campaign's DM: not even a note
        self.assertEqual((self.answerer.asked, self.host.saved), ([], []))

    async def test_whoever_pressed_start_is_not_thereby_a_dm(self) -> None:
        self.host.agreed.add((GUILD, STARTER))
        message = dm_message(STARTER, "check how grappling works")
        await self.sidebar.on_dm_message(message)
        self.assertEqual((sent(message), self.answerer.asked, self.host.saved), ([], [], []))
        self.assertFalse(
            self.sidebar.ask_at_table(
                self.host.tables[GUILD], STARTER, "Hold on, I need to check how grappling works."
            )
        )

    async def test_no_running_game_says_so_but_not_twice_in_a_row(self) -> None:
        await self.sidebar.on_dm_message(dm_message(content="check how grappling works"))
        self.host.tables.clear()  # the game is over; they are known as a DM now
        first, second = dm_message(content="hello there"), dm_message(content="hello again")
        await self.sidebar.on_dm_message(first)
        await self.sidebar.on_dm_message(second)
        self.assertEqual(sent(first), [service.NO_SESSION])
        self.assertEqual(sent(second), [])

    async def test_messages_from_bots_and_from_servers_are_ignored(self) -> None:
        bot = dm_message(content="check the rules for flanking")
        bot.author.bot = True
        in_server = dm_message(content="check the rules for flanking")
        in_server.guild = object()
        for message in (bot, in_server):
            await self.sidebar.on_dm_message(message)
            self.assertEqual(sent(message), [])
        self.assertEqual(self.answerer.asked, [])

    async def test_someone_who_has_not_agreed_is_asked_and_nothing_is_read_or_kept(self) -> None:
        self.host.agreed.clear()
        message = dm_message(voice=ogg_opus(1.0) or b"x")
        await self.sidebar.on_dm_message(message)
        self.assertEqual(sent(message), [service.NOT_AGREED, "Can DMbot record you?"])
        self.assertEqual(
            message.channel.send.await_args_list[1].kwargs["view"], "the-consent-buttons"
        )
        message.attachments[0].read.assert_not_awaited()  # not even downloaded
        self.assertEqual((self.host.transcribed, self.answerer.asked, self.host.saved), (0, [], []))

    async def test_a_voice_message_is_heard_and_shown_back(self) -> None:
        data = ogg_opus(1.0)
        if data is None:
            self.skipTest("this PyAV can't encode Opus")
        message = dm_message(voice=data)
        await self.sidebar.on_dm_message(message)
        self.assertEqual(self.host.transcribed, 1)
        first = sent(message)[0]
        self.assertTrue(first.startswith("🎙️ “find if you need line of sight for fireball”\n"))
        self.assertIn("No: its origin", first)
        self.assertEqual([x.sidebar for x in self.host.saved], [SIDEBAR_QUESTION, SIDEBAR_ANSWER])

    async def test_a_voice_message_that_is_not_audio_gets_plain_words(self) -> None:
        message = dm_message(voice=b"not audio at all" * 30)
        await self.sidebar.on_dm_message(message)
        self.assertEqual(
            sent(message), ["I couldn't read that voice message. Try sending it again."]
        )
        self.assertEqual((self.host.transcribed, self.host.saved), (0, []))

    async def test_a_huge_file_is_refused_without_downloading_it(self) -> None:
        message = dm_message(voice=b"x", size=memo_limit() + 1)
        await self.sidebar.on_dm_message(message)
        self.assertEqual(sent(message), [service.TOO_LONG])
        message.attachments[0].read.assert_not_awaited()

    async def test_the_consent_question_is_not_repeated_at_every_message(self) -> None:
        self.host.agreed.clear()
        first, second = (
            dm_message(content="check how grappling works"),
            dm_message(content="check how grappling works"),
        )
        await self.sidebar.on_dm_message(first)
        await self.sidebar.on_dm_message(second)
        self.assertEqual(len(sent(first)), 2)
        self.assertEqual(sent(second), [])

    async def test_a_picture_gets_one_short_note(self) -> None:
        message = dm_message()
        message.attachments = [SimpleNamespace(size=10)]
        await self.sidebar.on_dm_message(message)
        self.assertEqual(sent(message), [service.TYPE_INSTEAD])

    async def test_a_long_question_is_echoed_short(self) -> None:
        data = ogg_opus(1.0)
        if data is None:
            self.skipTest("this PyAV can't encode Opus")
        self.host.heard_as = "check " + "word " * 100
        message = dm_message(voice=data)
        await self.sidebar.on_dm_message(message)
        echo = sent(message)[0].split("\n", 1)[0]
        self.assertLessEqual(len(echo), service.ECHO_CHARS + 8)
        self.assertTrue(echo.endswith("…”"))

    async def test_without_an_engine_nothing_is_downloaded_or_written_down(self) -> None:
        self.sidebar.answerer = None
        message = dm_message(voice=ogg_opus(1.0) or b"x")
        await self.sidebar.on_dm_message(message)
        self.assertEqual(sent(message), [service.NOT_YET])
        message.attachments[0].read.assert_not_awaited()
        self.assertEqual(self.host.transcribed, 0)

    async def test_ok_and_thanks_are_not_questions(self) -> None:
        for text in ("ok", "thanks", "👍"):
            message = dm_message(content=text)
            await self.sidebar.on_dm_message(message)
            self.assertEqual(sent(message), [])
        self.assertEqual((self.answerer.asked, self.host.saved), ([], []))
        question = dm_message(content="grappling?")
        await self.sidebar.on_dm_message(question)
        self.assertEqual(len(self.answerer.asked), 1)

    async def test_a_one_word_voice_message_is_not_a_question_either(self) -> None:
        data = ogg_opus(1.0)
        if data is None:
            self.skipTest("this PyAV can't encode Opus")
        self.host.heard_as = "thanks"
        message = dm_message(voice=data)
        await self.sidebar.on_dm_message(message)
        self.assertEqual(sent(message), [service.NO_WORDS])
        self.assertEqual((self.answerer.asked, self.host.saved), ([], []))

    async def test_a_long_answer_is_sent_in_pieces_that_fit(self) -> None:
        self.answerer.answer_to_give = FakeAnswer(("A sentence of some length. " * 200).strip())
        message = dm_message(content="check how grappling works")
        await self.sidebar.on_dm_message(message)
        pieces = sent(message)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(x) <= service.MESSAGE_LIMIT for x in pieces))
        self.assertEqual(" ".join(pieces), self.answerer.answer_to_give.text)

    async def test_no_words_heard(self) -> None:
        data = ogg_opus(1.0)
        if data is None:
            self.skipTest("this PyAV can't encode Opus")
        self.host.heard_as = "  "
        message = dm_message(voice=data)
        await self.sidebar.on_dm_message(message)
        self.assertEqual(sent(message), [service.NO_WORDS])
        self.assertEqual(self.host.saved, [])

    async def test_a_yes_taken_back_while_it_was_being_written_down_keeps_nothing(self) -> None:
        data = ogg_opus(1.0)
        if data is None:
            self.skipTest("this PyAV can't encode Opus")
        self.host.withdraw_during_transcribe = True
        message = dm_message(voice=data)
        await self.sidebar.on_dm_message(message)
        self.assertEqual((sent(message), self.host.saved, self.answerer.asked), ([], [], []))

    async def test_a_yes_taken_back_while_the_answer_was_coming_sends_and_keeps_nothing(
        self,
    ) -> None:
        self.answerer.gate = asyncio.Event()
        message = dm_message(content="check how grappling works")
        task = asyncio.create_task(self.sidebar.on_dm_message(message))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.host.agreed.discard((GUILD, DM))
        self.answerer.gate.set()
        await task
        self.assertEqual((sent(message), self.host.saved), ([], []))

    async def test_the_session_ending_meanwhile_sends_nothing(self) -> None:
        self.answerer.gate = asyncio.Event()
        message = dm_message(content="check how grappling works")
        task = asyncio.create_task(self.sidebar.on_dm_message(message))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.host.tables.clear()
        self.answerer.gate.set()
        await task
        self.assertEqual(sent(message), [])

    async def test_not_ready_failed_and_refused_answers(self) -> None:
        self.sidebar.answerer = None
        message = dm_message(content="check how grappling works")
        await self.sidebar.on_dm_message(message)
        self.assertEqual(sent(message), [service.NOT_YET])
        self.assertEqual(self.host.saved, [])
        self.sidebar.answerer = self.answerer
        self.answerer.error = RuntimeError("boom")
        failed = dm_message(content="check how grappling works")
        await self.sidebar.on_dm_message(failed)
        self.assertEqual(sent(failed), [service.FAILED])
        self.assertEqual(self.host.saved, [])  # no orphan question after a failed answer
        self.answerer.error = None
        self.answerer.answer_to_give = FakeAnswer("You've used all your hours.", refused=True)
        self.host.saved.clear()
        refused = dm_message(content="check how grappling works")
        await self.sidebar.on_dm_message(refused)
        self.assertEqual(sent(refused), ["You've used all your hours."])
        self.assertEqual(self.host.saved, [])  # the plan said no: nothing to remember

    async def test_an_answer_about_discord_or_dmbot_is_not_in_the_transcript(self) -> None:
        self.answerer.answer_to_give = FakeAnswer("Press ⚙️ Menu.", in_game=False)
        await self.sidebar.on_dm_message(dm_message(content="check how to stop recording me"))
        self.assertEqual([x.sidebar for x in self.host.saved], [SIDEBAR_QUESTION])

    async def test_one_question_at_a_time(self) -> None:
        self.answerer.gate = asyncio.Event()
        first = dm_message(content="check how grappling works")
        task = asyncio.create_task(self.sidebar.on_dm_message(first))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        second = dm_message(content="check how flanking works")
        await self.sidebar.on_dm_message(second)
        self.assertEqual(sent(second), [service.NOT_SOON_AGAIN])
        self.answerer.gate.set()
        await task
        self.assertEqual(len(self.answerer.asked), 1)

    async def test_the_scene_goes_with_the_question(self) -> None:
        table = self.host.tables[GUILD]
        self.host.agreed.add((GUILD, PLAYER))
        table.recent.add(PLAYER, "I draw my bow.", 250.0)
        table.recent.add(PLAYER, "ages ago", 1.0)
        self.sidebar._monotonic = lambda: 300.0
        await self.sidebar.on_dm_message(dm_message(content="check how grappling works"))
        (_, _, scene) = self.answerer.asked[0]
        self.assertEqual(scene, "Mia: I draw my bow.")


class TheRulesStayTrue(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.table = make_table()
        self.host = FakeHost(tables={GUILD: self.table}, agreed={(GUILD, DM), (GUILD, PLAYER)})
        self.answerer = FakeAnswerer()
        self.sidebar = SidebarService(self.host)
        self.sidebar.answerer = self.answerer

    async def test_someone_who_stopped_being_recorded_is_not_in_the_scene(self) -> None:
        self.table.recent.add(PLAYER, "I draw my bow.", 100.0)
        self.table.recent.add(DM, "The goblin ducks.", 101.0)
        self.table.recent.drop_speaker(PLAYER)  # what stop_recording does
        await self.sidebar.on_dm_message(dm_message(content="check how grappling works"))
        self.assertNotIn("bow", self.answerer.asked[0][2])
        # ...and if the line were still there, someone who no longer agrees is skipped too
        self.table.recent.add(PLAYER, "Secret plan.", 200.0)
        self.host.agreed.discard((GUILD, PLAYER))
        scene = self.table.recent.scene(
            lambda u: "x", 200.0, lambda u: self.host.sidebar_has_consent(GUILD, u)
        )
        self.assertNotIn("Secret", scene)

    async def test_a_dm_who_stopped_being_dm_while_the_picker_was_open_gets_nothing(self) -> None:
        other = make_table(OTHER_GUILD, "c2", "Strahd")
        self.host.tables[OTHER_GUILD] = other
        self.host.agreed.add((OTHER_GUILD, DM))
        message = dm_message(content="check how grappling works")
        await self.sidebar.on_dm_message(message)
        view = message.channel.send.await_args_list[0].kwargs["view"]
        other.dm_user_ids = frozenset()  # a handover happened meanwhile
        button = next(b for b in view.children if b.label == "Strahd")
        mine = SimpleNamespace(
            user=SimpleNamespace(id=DM), response=SimpleNamespace(edit_message=AsyncMock())
        )
        await button.callback(mine)
        self.assertEqual(self.answerer.asked, [])
        self.assertIn(service.NO_SESSION, sent(message))

    async def test_no_longer_the_dm_after_the_question_was_asked_sends_nothing(self) -> None:
        self.answerer.gate = asyncio.Event()
        message = dm_message(content="check how grappling works")
        task = asyncio.create_task(self.sidebar.on_dm_message(message))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.table.dm_user_ids = frozenset()
        self.answerer.gate.set()
        await task
        self.assertEqual(sent(message), [])

    async def test_a_few_questions_a_minute_in_the_chat(self) -> None:
        now = [1000.0]
        self.sidebar._clock = lambda: now[0]
        messages = [dm_message(content="check how grappling works") for _ in range(8)]
        for message in messages:
            await self.sidebar.on_dm_message(message)
            now[0] += 2
        self.assertEqual(len(self.answerer.asked), service.CHAT_PER_MINUTE)
        self.assertEqual(sent(messages[6]), [service.SLOW_DOWN])
        self.assertEqual(sent(messages[7]), [])  # said once
        now[0] += 60
        later = dm_message(content="check how flanking works")
        await self.sidebar.on_dm_message(later)
        self.assertEqual(len(self.answerer.asked), service.CHAT_PER_MINUTE + 1)

    async def test_strangers_do_not_make_the_notes_grow_forever(self) -> None:
        self.host.tables.clear()
        now = [1000.0]
        self.sidebar._clock = lambda: now[0]
        for user in range(5_000, 5_000 + service.NOTE_KEEP + 200):
            await self.sidebar.on_dm_message(dm_message(user, "hi"))
        now[0] += 120
        await self.sidebar.on_dm_message(dm_message(9_999, "hi"))
        self.assertLess(len(self.sidebar._noted), 10)

    async def test_a_busy_flag_never_sticks_after_a_failure(self) -> None:
        self.answerer.error = RuntimeError("boom")
        await self.sidebar.on_dm_message(dm_message(content="check how grappling works"))
        self.assertEqual(self.sidebar._busy, set())
        self.answerer.error = None
        again = dm_message(content="check how grappling works")
        await self.sidebar.on_dm_message(again)
        self.assertEqual(sent(again), [FakeAnswer().text])


class SeveralGames(unittest.IsolatedAsyncioTestCase):
    async def test_the_dm_picks_which_game_and_only_then_it_goes_on(self) -> None:
        host = FakeHost(
            tables={GUILD: make_table(), OTHER_GUILD: make_table(OTHER_GUILD, "c2", "Strahd")},
            agreed={(GUILD, DM), (OTHER_GUILD, DM)},
        )
        answerer = FakeAnswerer()
        sidebar = SidebarService(host)
        sidebar.answerer = answerer
        message = dm_message(content="check how grappling works")
        await sidebar.on_dm_message(message)
        self.assertEqual(sent(message), [service.WHICH])
        self.assertEqual(answerer.asked, [])  # nothing yet
        view = message.channel.send.await_args_list[0].kwargs["view"]
        self.assertEqual(sorted(b.label for b in view.children), ["Frostmaiden", "Strahd"])
        button = next(b for b in view.children if b.label == "Strahd")
        stranger = SimpleNamespace(
            user=SimpleNamespace(id=PLAYER), response=SimpleNamespace(edit_message=AsyncMock())
        )
        stranger.response.send_message = AsyncMock()
        await button.callback(stranger)
        self.assertEqual(answerer.asked, [])  # not theirs to press
        stranger.response.send_message.assert_awaited_once()
        stranger.response.edit_message.assert_not_awaited()  # the DM's picker is still there
        mine = SimpleNamespace(
            user=SimpleNamespace(id=DM), response=SimpleNamespace(edit_message=AsyncMock())
        )
        await button.callback(mine)
        ((campaign, _, _),) = answerer.asked
        self.assertEqual((campaign.id, campaign.guild_id), ("c2", OTHER_GUILD))
        self.assertEqual([x.sidebar for x in host.saved], [SIDEBAR_QUESTION, SIDEBAR_ANSWER])


class SaidAtTheTable(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.table = make_table()
        self.host = FakeHost(tables={GUILD: self.table}, agreed={(GUILD, DM), (GUILD, PLAYER)})
        self.answerer = FakeAnswerer()
        self.sidebar = SidebarService(self.host)
        self.sidebar.answerer = self.answerer

    async def settle(self) -> None:
        await asyncio.gather(*self.sidebar._tasks)

    async def test_the_dm_asking_aloud_gets_a_private_answer(self) -> None:
        said = "Hold on, I need to find if you need line of sight for fireball."
        self.assertTrue(self.sidebar.ask_at_table(self.table, DM, said))
        await self.settle()
        self.assertEqual(self.host.dms, [(DM, FakeAnswer().text)])
        self.assertEqual(self.answerer.asked[0][1], FIREBALL)
        self.assertEqual(
            [(x.sidebar, x.heard) for x in self.host.saved],
            [(SIDEBAR_QUESTION, FIREBALL), (SIDEBAR_ANSWER, FakeAnswer().text)],
        )
        asked = self.host.saved[0].lineage
        self.assertEqual((asked.via, asked.stt), ("table-trigger", self.host.sidebar_stt()))

    async def test_a_player_saying_the_same_words_starts_nothing(self) -> None:
        said = "Hold on, I need to find if you need line of sight for fireball."
        self.assertFalse(self.sidebar.ask_at_table(self.table, PLAYER, said))
        self.assertEqual((self.sidebar._tasks, self.host.dms, self.answerer.asked), (set(), [], []))

    async def test_ordinary_talk_and_in_character_lines_start_nothing(self) -> None:
        for said, kw in [
            ("I need to find the map", {}),
            ("Hold on, I need to find the dragon's lair on this map", {"in_character": True}),
        ]:
            self.assertFalse(self.sidebar.ask_at_table(self.table, DM, said, **kw))

    async def test_one_a_minute_for_the_table(self) -> None:
        now = [1000.0]
        self.sidebar._clock = lambda: now[0]
        said = "Hold on, I need to check how grappling works."
        self.assertTrue(self.sidebar.ask_at_table(self.table, DM, said))
        await self.settle()
        now[0] += 30
        self.assertFalse(self.sidebar.ask_at_table(self.table, DM, said))
        now[0] += 31
        self.assertTrue(self.sidebar.ask_at_table(self.table, DM, said))
        await self.settle()
        self.assertEqual(len(self.answerer.asked), 2)

    async def test_a_dm_who_stopped_being_recorded_gets_nothing(self) -> None:
        self.host.agreed.discard((GUILD, DM))
        said = "Hold on, I need to check how grappling works."
        self.sidebar.ask_at_table(self.table, DM, said)
        await self.settle()
        self.assertEqual((self.host.dms, self.host.saved, self.answerer.asked), ([], [], []))

    async def test_a_closed_private_chat_is_told_to_the_dm_screen_without_the_answer(self) -> None:
        self.host.can_dm = False
        said = "Hold on, I need to check how grappling works."
        self.sidebar.ask_at_table(self.table, DM, said)
        await self.settle()
        self.assertEqual(self.host.saved, [])  # nobody got an answer: nothing is written
        self.assertEqual(self.host.screen, [service.CANT_DM])  # and never the answer itself

    async def test_other_failures_to_send_say_nothing_on_the_dm_screen(self) -> None:
        self.host.can_dm = False
        self.host.refusal = "failed"
        self.sidebar.ask_at_table(self.table, DM, "Hold on, I need to check how grappling works.")
        await self.settle()
        self.assertEqual(self.host.screen, [])

    async def test_no_engine_means_a_spoken_question_is_quietly_ignored(self) -> None:
        self.sidebar.answerer = None
        said = "Hold on, I need to check how grappling works."
        self.assertFalse(self.sidebar.ask_at_table(self.table, DM, said))
        self.assertEqual((self.host.dms, self.host.saved), ([], []))
        self.sidebar.answerer = self.answerer
        self.assertTrue(self.sidebar.ask_at_table(self.table, DM, said))  # the minute was kept
        await self.settle()

    async def test_a_spoken_question_the_limit_swallows_is_explained_privately_once(self) -> None:
        now = [1000.0]
        self.sidebar._clock = lambda: now[0]
        said = "Hold on, I need to check how grappling works."
        self.assertTrue(self.sidebar.ask_at_table(self.table, DM, said))
        await self.settle()
        self.host.dms.clear()
        now[0] += 5
        self.assertFalse(self.sidebar.ask_at_table(self.table, DM, said))
        self.assertFalse(self.sidebar.ask_at_table(self.table, DM, said))  # not told twice
        await asyncio.gather(*self.sidebar._tasks)
        self.assertEqual(self.host.dms, [(DM, service.ONE_A_MINUTE)])

    async def test_a_spoken_question_while_one_is_being_answered_is_explained(self) -> None:
        self.answerer.gate = asyncio.Event()
        said = "Hold on, I need to check how grappling works."
        self.assertTrue(self.sidebar.ask_at_table(self.table, DM, said))
        await asyncio.sleep(0)
        self.assertFalse(self.sidebar.ask_at_table(self.table, DM, said))
        await asyncio.sleep(0)
        self.assertIn((DM, service.NOT_SOON_AGAIN), self.host.dms)
        self.answerer.gate.set()
        await self.settle()


class TheScene(unittest.TestCase):
    def test_only_the_last_few_minutes_and_a_bounded_size(self) -> None:
        recent = Recent.new()
        recent.add(PLAYER, "old news", 0.0)
        recent.add(PLAYER, "I draw my bow.", 900.0)
        recent.add(DM, "The goblin ducks.", 905.0)
        recent.add(DM, "   ", 906.0)
        names = {PLAYER: "Mia", DM: "Sam"}
        self.assertEqual(
            recent.scene(lambda u: names[u], 910.0), "Mia: I draw my bow.\nSam: The goblin ducks."
        )
        for n in range(200):
            recent.add(PLAYER, "word " * 50, 950.0 + n * 0.1)
        self.assertLessEqual(len(recent.scene(lambda u: names[u], 970.0)), 1_300)


def memo_limit() -> int:
    from dmbot.sidebar import memo

    return memo.MAX_MEMO_BYTES


if __name__ == "__main__":
    unittest.main()
