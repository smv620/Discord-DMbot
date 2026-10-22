"""The DM sidebar's ways in (#935): voice messages, typed questions and the table, with the
rules around them. No Discord and no database: a fake host and a fake answer engine."""

import asyncio
import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY, AsyncMock

from dmbot.ai import AIError
from dmbot.sidebar import service
from dmbot.sidebar.ask import ASK_PER_MINUTE, AskLimiter
from dmbot.sidebar.service import Recent, SidebarService
from dmbot.transcript.models import (
    SIDEBAR_ANSWER,
    SIDEBAR_QUESTION,
    SIDEBAR_TABLE_QUESTION,
    Line,
)
from tests.test_sidebar_memo import ogg_opus

GUILD, OTHER_GUILD, DM, CO_DM, PLAYER, STARTER = 1, 2, 7, 70, 8, 99
FIREBALL = "find if you need line of sight for fireball"


@dataclass
class FakeAnswer:
    text: str = "No: its origin is a point you choose. (SRD 5.2.1, confident)"
    in_game: bool = True
    refused: bool = False
    model: str = "model-1"
    prompt_version: str = "prompt-1"
    sources: tuple[str, ...] = ("SRD 5.2.1 p. 241",)
    parts: tuple[str, ...] = ()
    one_for_dm: bool = False
    question: str = ""  # the AI's few-word restatement of a table question
    clash: Any = None  # two house rules that disagree (#2284)


class FakeAnswerer:
    def __init__(self, answer: FakeAnswer | None = None) -> None:
        self.answer_to_give = answer or FakeAnswer()
        self.asked: list[tuple[Any, str, str]] = []
        self.askers: list[int] = []
        self.table_modes: list[bool] = []  # whether each question was a table's (#1187)
        self.error: Exception | None = None
        self.gate: asyncio.Event | None = None

    async def answer(
        self, campaign: Any, question: str, *, asker_id: int, scene: str = "", table: bool = False
    ) -> FakeAnswer:
        self.asked.append((campaign, question, scene))
        self.askers.append(asker_id)
        self.table_modes.append(table)
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.answer_to_give


def make_table(
    guild: int = GUILD, campaign: str = "c1", name: str = "Frostmaiden", chat: int | None = None
) -> Any:
    return SimpleNamespace(
        guild_id=guild,
        campaign_id=campaign,
        campaign_name=name,
        dm_user_id=STARTER,  # whoever pressed Start; not necessarily a campaign DM
        dm_user_ids=frozenset({DM, CO_DM}),
        segmenter=SimpleNamespace(session=5),
        recent=Recent.new(),
        sidebar_limiter=AskLimiter(),
        dmbchat_channel_id=chat,
        dmbchat_nudged=set(),
        person_limits={},
    )


@dataclass
class FakeHost:
    tables: dict[int, Any] = field(default_factory=dict)
    agreed: set[tuple[int, int]] = field(default_factory=set)
    saved: list[Line] = field(default_factory=list)
    dms: list[tuple[int, str]] = field(default_factory=list)
    views: list[Any] = field(default_factory=list)  # the view under each private answer (#1957)
    screen: list[str] = field(default_factory=list)
    heard_as: str | None = "find if you need line of sight for fireball"
    transcribed: int = 0
    can_dm: bool = True
    refusal: str = "forbidden"
    withdraw_during_transcribe: bool = False
    house_rules: list[tuple[int, int, str]] = field(default_factory=list)
    house_rule_result: str = "started"
    chat: list[str] = field(default_factory=list)  # posts in the DMbot chat (#1187)
    nudged: list[int] = field(default_factory=list)
    house_link: str | None = None
    character_names: dict[int, str] = field(default_factory=dict)
    guide: str | None = None  # Guide me's answer (#1739); None: not on for this campaign
    guide_asked: list[int] = field(default_factory=list)
    guide_raises: bool = False
    guide_withdraws: bool = False  # consent goes while the answer is being made

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

    async def sidebar_send_dm(self, user_id: int, text: str, view: Any = None) -> str:
        self.dms.append((user_id, text))
        self.views.append(view)
        return "sent" if self.can_dm else self.refusal

    def sidebar_save(self, table: Any, line: Line) -> None:
        self.saved.append(line)

    def sidebar_stt(self) -> str:
        return "deepgram nova-3 api.example"

    async def sidebar_tell_screen(self, table: Any, text: str) -> None:
        self.screen.append(text)

    def sidebar_house_rule(self, table: Any, user_id: int, text: str) -> str:
        self.house_rules.append((table.guild_id, user_id, text))
        return self.house_rule_result

    async def sidebar_post_chat(self, table: Any, text: str) -> bool:
        self.chat.append(text)
        return True

    async def sidebar_table_name(self, table: Any, user_id: int) -> str:
        return self.character_names.get(user_id) or self.name_of(table.guild_id, user_id)

    async def sidebar_links(self, table: Any) -> tuple[str | None, tuple[str, ...]]:
        return self.house_link, ()

    async def sidebar_nudge_chat(self, table: Any, user_id: int) -> None:
        self.nudged.append(user_id)

    async def sidebar_recap_feedback(self, table: Any, user_id: int, text: str) -> bool:
        asked = self.__dict__.setdefault("feedback_asked", [])
        asked.append((user_id, text))
        return bool(self.__dict__.get("feedback_handled", False))

    async def sidebar_guide(self, table: Any, user_id: int) -> str | None:
        self.guide_asked.append(user_id)
        if self.guide_raises:
            raise RuntimeError("boom")
        if self.guide_withdraws:
            self.agreed.discard((table.guild_id, user_id))
        return self.guide


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
        self.answerer.answer_to_give = FakeAnswer("Press 🗂️ Menu.", in_game=False)
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
        self.assertEqual(len(self.answerer.asked), ASK_PER_MINUTE)
        self.assertEqual(sent(messages[6]), [service.TOO_MANY])
        self.assertEqual(sent(messages[7]), [])  # said once
        now[0] += 60
        later = dm_message(content="check how flanking works")
        await self.sidebar.on_dm_message(later)
        self.assertEqual(len(self.answerer.asked), ASK_PER_MINUTE + 1)

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

    async def test_asked_by_typing_in_the_table_chat_says_so_and_has_no_speech_engine(self) -> None:
        said = "Hold on, I need to find if you need line of sight for fireball."
        self.assertTrue(self.sidebar.ask_at_table(self.table, DM, said, typed=True))
        await self.settle()
        asked = self.host.saved[0].lineage
        self.assertEqual((asked.via, asked.stt), ("table-chat", ""))

    async def test_a_player_saying_the_same_words_starts_nothing(self) -> None:
        said = "Hold on, I need to find if you need line of sight for fireball."
        self.assertFalse(self.sidebar.ask_at_table(self.table, PLAYER, said))
        self.assertEqual((self.sidebar._tasks, self.host.dms, self.answerer.asked), (set(), [], []))

    async def test_ordinary_talk_and_in_character_lines_start_nothing(self) -> None:
        cases: list[tuple[str, dict[str, Any]]] = [
            ("I need to find the map", {}),
            ("Hold on, I need to find the dragon's lair on this map", {"in_character": True}),
        ]
        for said, kw in cases:
            self.assertFalse(self.sidebar.ask_at_table(self.table, DM, said, **kw))

    async def test_six_questions_a_minute_for_the_table(self) -> None:
        now = [1000.0]
        self.sidebar._clock = lambda: now[0]
        said = "Hold on, I need to check how grappling works."
        for _ in range(ASK_PER_MINUTE):
            self.assertTrue(self.sidebar.ask_at_table(self.table, DM, said))
            await self.settle()
            now[0] += 2
        self.assertFalse(self.sidebar.ask_at_table(self.table, DM, said))
        now[0] += 60
        self.assertTrue(self.sidebar.ask_at_table(self.table, DM, said))
        await self.settle()
        self.assertEqual(len(self.answerer.asked), ASK_PER_MINUTE + 1)

    async def test_hey_dmbot_gets_a_private_answer(self) -> None:
        self.assertTrue(
            self.sidebar.ask_at_table(self.table, DM, "Hey DMbot, what's the range of fireball?")
        )
        await self.settle()
        self.assertEqual(self.answerer.asked[0][1], "what's the range of fireball")
        self.assertEqual(self.host.dms, [(DM, FakeAnswer().text)])  # the DM's chat only
        self.assertEqual(self.host.screen, [])  # never the screen, a channel or the voice room
        self.assertEqual(
            [x.sidebar for x in self.host.saved], [SIDEBAR_QUESTION, SIDEBAR_ANSWER]
        )  # the raw transcript keeps its lines, as for "hold on"

    async def test_a_player_saying_hey_dmbot_starts_nothing(self) -> None:
        self.assertFalse(
            self.sidebar.ask_at_table(
                self.table, PLAYER, "Hey DMbot, what's the range of fireball?"
            )
        )
        self.assertEqual((self.sidebar._tasks, self.host.dms, self.answerer.asked), (set(), [], []))

    async def test_the_phrase_in_the_middle_of_a_line_asks_what_follows_it(self) -> None:
        said = "so they're in the cave, hey DMbot what's the range of fireball"
        self.assertTrue(self.sidebar.ask_at_table(self.table, DM, said))
        await self.settle()
        self.assertEqual(self.answerer.asked[0][1], "what's the range of fireball")

    async def test_a_story_about_dmbot_starts_nothing(self) -> None:
        self.assertFalse(
            self.sidebar.ask_at_table(self.table, DM, "and DMbot said earlier it was far")
        )
        self.assertFalse(self.sidebar.ask_at_table(self.table, DM, "the DMbot screen is on"))

    async def test_one_question_at_a_time_with_the_wake_phrase_too(self) -> None:
        self.answerer.gate = asyncio.Event()
        self.assertTrue(
            self.sidebar.ask_at_table(self.table, DM, "Hey DMbot, how does grapple work")
        )
        await asyncio.sleep(0)
        self.assertFalse(self.sidebar.ask_at_table(self.table, DM, "DMbot, what about prone"))
        await asyncio.sleep(0)
        self.assertIn((DM, service.NOT_SOON_AGAIN), self.host.dms)
        self.answerer.gate.set()
        await self.settle()

    async def test_the_session_limit_stops_a_looping_line(self) -> None:
        self.table.sidebar_limiter = AskLimiter(per_minute=1000, per_session=3)
        for _ in range(3):
            self.assertTrue(
                self.sidebar.ask_at_table(self.table, DM, "DMbot, how does grapple work")
            )
            await self.settle()
        self.assertFalse(self.sidebar.ask_at_table(self.table, DM, "DMbot, how does grapple work"))
        await asyncio.gather(*self.sidebar._tasks)
        self.assertEqual(len(self.answerer.asked), 3)
        self.assertEqual(self.host.dms[-1], (DM, service.TOO_MANY))

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
        self.table.sidebar_limiter = AskLimiter(per_minute=1)
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
        self.assertEqual(self.host.dms, [(DM, service.TOO_MANY)])

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


class TypedHouseRules(unittest.IsolatedAsyncioTestCase):
    """A typed "house rule: …" in the DM's private chat (#960): the same proposal as one
    said aloud; not a question, so no AI and no sidebar line."""

    RULE = "house rule: potions are a bonus action"

    def setUp(self) -> None:
        self.host = FakeHost(tables={GUILD: make_table()}, agreed={(GUILD, DM), (GUILD, CO_DM)})
        self.answerer = FakeAnswerer()
        self.sidebar = SidebarService(self.host)
        self.sidebar.answerer = self.answerer

    async def test_a_dms_typed_rule_goes_to_the_dm_screen_with_one_short_reply(self) -> None:
        message = dm_message(content=self.RULE)
        await self.sidebar.on_dm_message(message)
        self.assertEqual(self.host.house_rules, [(GUILD, DM, self.RULE)])
        self.assertEqual(sent(message), [service.HOUSE_SENT])
        self.assertIn("Press **Save**", service.HOUSE_SENT)

    async def test_it_is_not_a_question_no_ai_and_nothing_written_down(self) -> None:
        await self.sidebar.on_dm_message(dm_message(content=self.RULE))
        self.assertEqual(self.answerer.asked, [])
        self.assertEqual(self.host.saved, [])
        self.assertEqual(self.host.dms, [])

    async def test_it_works_before_the_answers_are_switched_on(self) -> None:
        self.sidebar.answerer = None
        message = dm_message(content=self.RULE)
        await self.sidebar.on_dm_message(message)
        self.assertEqual(sent(message), [service.HOUSE_SENT])

    async def test_a_second_dm_of_the_campaign_may_too(self) -> None:
        await self.sidebar.on_dm_message(dm_message(CO_DM, self.RULE))
        self.assertEqual(self.host.house_rules, [(GUILD, CO_DM, self.RULE)])

    async def test_a_players_typed_rule_never_becomes_a_proposal(self) -> None:
        self.host.agreed.add((GUILD, PLAYER))
        message = dm_message(PLAYER, self.RULE)
        await self.sidebar.on_dm_message(message)
        self.assertEqual(self.host.house_rules, [])
        self.assertEqual(sent(message), [])  # a stranger gets nothing

    async def test_a_known_dm_with_no_game_is_told_and_nothing_is_offered(self) -> None:
        self.sidebar.tables_of(DM)  # seen as a DM
        self.host.tables.clear()
        message = dm_message(content=self.RULE)
        await self.sidebar.on_dm_message(message)
        self.assertEqual(sent(message), [service.HOUSE_NO_SESSION])
        self.assertEqual(self.host.house_rules, [])

    async def test_a_dm_who_has_not_agreed_is_asked_and_nothing_is_offered(self) -> None:
        self.host.agreed.discard((GUILD, DM))
        message = dm_message(content=self.RULE)
        await self.sidebar.on_dm_message(message)
        self.assertEqual(self.host.house_rules, [])
        self.assertEqual(sent(message)[0], service.HOUSE_NOT_AGREED)

    async def test_each_reason_it_was_not_offered_has_its_own_words(self) -> None:
        for result, words in (
            ("too-soon", service.HOUSE_SOON),
            ("repeat", service.HOUSE_REPEAT),
            ("not-now", service.HOUSE_NOT_NOW),
        ):
            self.host.house_rule_result = result
            self.sidebar._noted.clear()  # (a refusal is said once in a while)
            message = dm_message(content=self.RULE)
            await self.sidebar.on_dm_message(message)
            self.assertEqual(sent(message), [words])

    async def test_other_typed_words_are_still_questions_or_nothing(self) -> None:
        for text in ("the house rules say we can", "ok", "thanks"):
            await self.sidebar.on_dm_message(dm_message(content=text))
        self.assertEqual(self.host.house_rules, [])  # no proposal from any of them
        self.assertEqual([q for _, q, _ in self.answerer.asked], ["the house rules say we can"])

    async def test_a_question_that_starts_like_a_rule_is_still_a_question(self) -> None:
        await self.sidebar.on_dm_message(
            dm_message(content="for this table, how does grappling work?")
        )
        self.assertEqual(self.host.house_rules, [])
        self.assertEqual(len(self.answerer.asked), 1)

    async def test_a_stranger_gets_nothing_and_nothing_is_kept(self) -> None:
        message = dm_message(PLAYER, self.RULE)
        await self.sidebar.on_dm_message(message)
        self.assertEqual((sent(message), self.host.house_rules, self.sidebar._noted), ([], [], {}))

    async def test_a_long_message_is_cut_before_it_is_read(self) -> None:
        await self.sidebar.on_dm_message(dm_message(content=self.RULE + " word" * 1000))
        ((_, _, text),) = self.host.house_rules
        self.assertLessEqual(len(text), service.MESSAGE_LIMIT)

    async def test_refusals_are_not_said_for_every_message(self) -> None:
        self.host.house_rule_result = "too-soon"
        messages = [dm_message(content=self.RULE) for _ in range(20)]
        for message in messages:
            await self.sidebar.on_dm_message(message)
        self.assertEqual(sum(len(sent(m)) for m in messages), 1)

    async def test_with_several_games_the_dm_picks_which_one(self) -> None:
        self.host.tables[OTHER_GUILD] = make_table(OTHER_GUILD, "c2", "Strahd")
        self.host.agreed.add((OTHER_GUILD, DM))
        message = dm_message(content=self.RULE)
        await self.sidebar.on_dm_message(message)
        self.assertEqual(sent(message), [service.HOUSE_WHICH])
        self.assertEqual(self.host.house_rules, [])  # nothing yet
        view = message.channel.send.await_args_list[0].kwargs["view"]
        button = next(b for b in view.children if b.label == "Strahd")
        stranger = SimpleNamespace(
            user=SimpleNamespace(id=PLAYER), response=SimpleNamespace(edit_message=AsyncMock())
        )
        stranger.response.send_message = AsyncMock()
        await button.callback(stranger)
        self.assertEqual(self.host.house_rules, [])  # not theirs to press
        mine = SimpleNamespace(
            user=SimpleNamespace(id=DM), response=SimpleNamespace(edit_message=AsyncMock())
        )
        await button.callback(mine)
        self.assertEqual(self.host.house_rules, [(OTHER_GUILD, DM, self.RULE)])
        self.assertEqual(self.answerer.asked, [])
        self.assertEqual(sent(message)[-1], service.HOUSE_SENT)

    async def test_a_dm_removed_before_the_pick_is_told(self) -> None:
        self.host.tables[OTHER_GUILD] = make_table(OTHER_GUILD, "c2", "Strahd")
        self.host.agreed.add((OTHER_GUILD, DM))
        message = dm_message(content=self.RULE)
        await self.sidebar.on_dm_message(message)
        view = message.channel.send.await_args_list[0].kwargs["view"]
        button = next(b for b in view.children if b.label == "Strahd")
        self.host.tables[OTHER_GUILD].dm_user_ids = frozenset({CO_DM})
        mine = SimpleNamespace(
            user=SimpleNamespace(id=DM), response=SimpleNamespace(edit_message=AsyncMock())
        )
        await button.callback(mine)
        self.assertEqual(self.host.house_rules, [])
        self.assertEqual(sent(message)[-1], service.HOUSE_NO_SESSION)


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


class TableQuestions(unittest.IsolatedAsyncioTestCase):
    """Anyone at the table may ask; the answer is posted in the DMbot chat (#1187)."""

    CHAT = 555

    def setUp(self) -> None:
        self.table = make_table(chat=self.CHAT)
        self.host = FakeHost(
            tables={GUILD: self.table},
            agreed={(GUILD, DM), (GUILD, PLAYER)},
            character_names={PLAYER: "Mira"},
        )
        self.answerer = FakeAnswerer(FakeAnswer(text="No. You pick a point within 150 feet."))
        self.sidebar = SidebarService(self.host)
        self.sidebar.answerer = self.answerer
        self.now = [1000.0]
        self.sidebar._clock = lambda: self.now[0]

    async def settle(self) -> None:
        await asyncio.gather(*self.sidebar._tasks)

    async def test_a_players_hey_dmbot_is_answered_in_the_chat_in_the_owners_form(self) -> None:
        said = "Hey DMbot, does fireball need line of sight?"
        self.assertTrue(self.sidebar.ask_at_table(self.table, PLAYER, said))
        await self.settle()
        self.assertEqual(
            self.host.chat,
            [
                "Q (Mira): Does fireball need line of sight?\n"
                "Answer: No. You pick a point within 150 feet."
            ],
        )
        self.assertEqual(self.host.dms, [])  # nothing private
        self.assertEqual(self.answerer.table_modes, [True])  # a table's prompt (no DM side)

    async def test_the_name_is_the_nickname_without_a_linked_character(self) -> None:
        self.host.character_names.clear()
        self.sidebar.ask_at_table(self.table, PLAYER, "Hey DMbot, how does grappling work?")
        await self.settle()
        self.assertTrue(self.host.chat[0].startswith("Q (Mia): "))

    async def test_what_people_said_cannot_format_the_message_or_ping(self) -> None:
        self.host.character_names[PLAYER] = "**Mira** <@123456789012345678>"
        self.sidebar.ask_at_table(
            self.table,
            PLAYER,
            "Hey DMbot, is @everyone <@&123456789012345678> *bold* a thing in grappling?",
        )
        await self.settle()
        line = self.host.chat[0]
        self.assertNotIn("<@", line.replace("<@\u200b", ""))
        self.assertNotIn("@everyone", line.replace("@​everyone", ""))
        self.assertIn("\\*\\*Mira\\*\\*", line)

    async def test_a_long_question_is_cut_to_one_short_line(self) -> None:
        self.sidebar.ask_at_table(self.table, PLAYER, "Hey DMbot, " + "grapple rules " * 60)
        await self.settle()
        first = self.host.chat[0].splitlines()[0]
        self.assertLessEqual(len(first), service.Q_CHARS + 20)
        self.assertTrue(first.endswith("…"), first)

    async def test_a_players_hold_on_i_need_to_find_does_nothing(self) -> None:
        said = "Hold on, I need to find if you need line of sight for fireball."
        self.assertFalse(self.sidebar.ask_at_table(self.table, PLAYER, said))
        self.assertEqual((self.host.chat, self.host.dms, self.answerer.asked), ([], [], []))

    async def test_the_dms_hold_on_stays_private_and_their_hey_dmbot_goes_to_the_chat(self) -> None:
        self.sidebar.ask_at_table(self.table, DM, "Hold on, I need to check how grappling works.")
        await self.settle()
        self.assertEqual((len(self.host.dms), self.host.chat), (1, []))
        self.sidebar.ask_at_table(self.table, DM, "Hey DMbot, how does grappling work?")
        await self.settle()
        self.assertEqual(len(self.host.chat), 1)

    async def test_without_a_chat_channel_a_players_question_has_nowhere_to_go(self) -> None:
        self.table.dmbchat_channel_id = None
        self.assertFalse(
            self.sidebar.ask_at_table(self.table, PLAYER, "Hey DMbot, how does grappling work?")
        )

    async def test_a_message_typed_in_the_chat_from_someone_who_agreed_is_a_question(self) -> None:
        self.assertTrue(
            self.sidebar.ask_in_chat(self.table, PLAYER, "does fireball need line of sight?")
        )
        await self.settle()
        self.assertEqual(len(self.host.chat), 1)
        self.assertEqual(self.answerer.asked[0][1], "does fireball need line of sight?")

    async def test_someone_who_has_not_agreed_is_shown_the_button_once_and_nothing_is_asked(
        self,
    ) -> None:
        for _ in range(3):
            self.assertFalse(self.sidebar.ask_in_chat(self.table, 31, "does fireball hurt?"))
        await self.settle()
        self.assertEqual((self.host.nudged, self.answerer.asked, self.host.saved), ([31], [], []))
        self.assertEqual(self.host.chat, [])

    async def test_two_questions_a_minute_for_each_person(self) -> None:
        for n in range(2):
            self.assertTrue(self.sidebar.ask_in_chat(self.table, PLAYER, f"question number {n}"))
            await self.settle()
            self.now[0] += 2
        self.assertFalse(self.sidebar.ask_in_chat(self.table, PLAYER, "one more question"))
        await self.settle()
        self.assertIn(service.TOO_MANY, self.host.chat)  # the existing over-limit line
        # another person is not held back by theirs
        self.host.agreed.add((GUILD, 31))
        self.assertTrue(self.sidebar.ask_in_chat(self.table, 31, "a question from another"))
        await self.settle()
        self.now[0] += 60
        self.assertTrue(self.sidebar.ask_in_chat(self.table, PLAYER, "and now again"))

    async def test_the_table_wide_limit_still_holds_for_everyone_together(self) -> None:
        for n in range(ASK_PER_MINUTE):
            user = 40 + n
            self.host.agreed.add((GUILD, user))
            self.assertTrue(self.sidebar.ask_in_chat(self.table, user, f"question number {n}"))
            await self.settle()
            self.now[0] += 1
        self.host.agreed.add((GUILD, 99))
        self.assertFalse(self.sidebar.ask_in_chat(self.table, 99, "one too many for the table"))

    async def test_withdrawing_consent_meanwhile_posts_and_keeps_nothing(self) -> None:
        self.answerer.gate = asyncio.Event()
        self.sidebar.ask_in_chat(self.table, PLAYER, "does fireball need line of sight?")
        await asyncio.sleep(0)
        self.host.agreed.discard((GUILD, PLAYER))
        self.answerer.gate.set()
        await self.settle()
        self.assertEqual((self.host.chat, self.host.saved), ([], []))

    async def test_a_players_lines_are_table_q_lines_and_the_dms_stay_sidebar_lines(self) -> None:
        self.sidebar.ask_in_chat(self.table, PLAYER, "does fireball need line of sight?")
        await self.settle()
        question, answer = self.host.saved
        self.assertEqual((question.sidebar, question.lineage.via), (SIDEBAR_TABLE_QUESTION, "chat"))
        self.assertEqual(question.lineage.stt, "")  # typed: no speech engine
        self.assertEqual(answer.sidebar, SIDEBAR_ANSWER)
        self.assertEqual(answer.lineage.reply_to, question.lineage.ref)
        self.assertEqual((answer.lineage.model, answer.lineage.prompt), ("model-1", "prompt-1"))
        self.host.saved.clear()
        self.sidebar.ask_at_table(self.table, PLAYER, "Hey DMbot, how does grappling work?")
        await self.settle()
        self.assertEqual(self.host.saved[0].lineage.via, "table-trigger")
        self.assertEqual(self.host.saved[0].lineage.stt, self.host.sidebar_stt())
        self.host.saved.clear()
        self.now[0] += 60
        self.sidebar.ask_at_table(self.table, DM, "Hey DMbot, how does grappling work?")
        await self.settle()
        self.assertEqual(self.host.saved[0].sidebar, SIDEBAR_QUESTION)  # the DM's keep their tag

    async def test_a_players_question_that_needs_the_dm_gets_the_fixed_line(self) -> None:
        self.answerer.answer_to_give = FakeAnswer(text="That's one for your DM.", one_for_dm=True)
        self.sidebar.ask_in_chat(self.table, PLAYER, "what is behind the door?")
        await self.settle()
        self.assertEqual(
            self.host.chat, ["Q (Mira): What is behind the door?\nAnswer: That's one for your DM."]
        )
        self.assertEqual(self.host.dms, [])

    async def test_a_dms_question_that_needs_their_side_is_answered_privately(self) -> None:
        public = FakeAnswer(text="That's one for your DM.", one_for_dm=True)
        private = FakeAnswer(text="Lord Veyn in disguise.")
        answerer = self.answerer
        original = answerer.answer

        async def by_mode(*args: Any, table: bool = False, **kwargs: Any) -> FakeAnswer:
            answerer.answer_to_give = public if table else private
            return await original(*args, table=table, **kwargs)

        answerer.answer = by_mode  # type: ignore[method-assign]
        self.sidebar.ask_in_chat(self.table, DM, "who is the masked stranger?")
        await self.settle()
        self.assertEqual(self.answerer.table_modes, [True, False])
        self.assertEqual(self.host.dms, [(DM, "Lord Veyn in disguise.")])
        self.assertEqual(
            self.host.chat,
            ["Q (Sam): Who is the masked stranger?\nAnswer: Sent to the DM privately."],
        )
        self.assertNotIn("Veyn", " ".join(self.host.chat))

    async def test_the_house_rules_link_is_preferred_and_other_links_are_dropped(self) -> None:
        self.host.house_link = "https://docs.example.org/our-house-rules"
        self.answerer.answer_to_give = FakeAnswer(
            text="Yes, crits are maximum. https://evil.example.net/x",
            sources=("house rule 3",),
        )
        self.sidebar.ask_in_chat(self.table, PLAYER, "do crits do maximum damage?")
        await self.settle()
        answer = self.host.chat[0].split("Answer: ", 1)[1]
        self.assertIn("https://docs.example.org/our-house-rules", answer)
        self.assertNotIn("evil.example.net", answer)
        self.assertEqual(answer.count("http"), 1)

    async def test_a_dndbeyond_link_is_kept_and_a_second_link_is_not(self) -> None:
        self.answerer.answer_to_give = FakeAnswer(
            text="Yes. https://www.dndbeyond.com/sources/basic-rules and https://www.dndbeyond.com/x"
        )
        self.sidebar.ask_in_chat(self.table, PLAYER, "does grappling need a free hand?")
        await self.settle()
        self.assertEqual(self.host.chat[0].count("http"), 1)
        self.assertIn("www.dndbeyond.com/sources/basic-rules", self.host.chat[0])

    async def test_a_link_from_another_campaign_never_shows(self) -> None:
        self.host.house_link = None  # this campaign has none; another's is not offered
        self.answerer.answer_to_give = FakeAnswer(
            text="Yes. https://docs.example.org/other-campaign"
        )
        self.sidebar.ask_in_chat(self.table, PLAYER, "do crits do maximum damage?")
        await self.settle()
        self.assertNotIn("http", self.host.chat[0])

    async def test_another_campaigns_chat_in_the_same_server_is_not_this_tables(self) -> None:
        other = make_table(chat=777)  # a different campaign's channel, never this table's id
        self.assertNotEqual(other.dmbchat_channel_id, self.table.dmbchat_channel_id)

    async def test_a_link_typed_in_a_question_is_not_repeated_by_dmbot(self) -> None:
        self.answerer.answer_to_give = FakeAnswer(text="Yes. https://www.dndbeyond.com/rules")
        self.sidebar.ask_in_chat(self.table, PLAYER, "is https://evil.example.net/x allowed here")
        await self.settle()
        self.assertEqual(self.host.chat[0].count("http"), 1)
        self.assertNotIn("evil", self.host.chat[0])

    async def test_a_long_answer_is_one_message_inside_discords_limit(self) -> None:
        self.answerer.answer_to_give = FakeAnswer(
            text="x" * 50, parts=("a" * 1900, "b" * 1900, "c" * 500)
        )
        self.sidebar.ask_in_chat(self.table, PLAYER, "read me the whole grapple rule")
        await self.settle()
        self.assertEqual(len(self.host.chat), 1)
        self.assertLessEqual(len(self.host.chat[0]), service.MESSAGE_LIMIT)

    async def test_consent_withdrawn_while_the_name_is_looked_up_posts_nothing(self) -> None:
        gate = asyncio.Event()
        original = self.host.sidebar_table_name

        async def slow(table: Any, user_id: int) -> str:
            await gate.wait()
            return await original(table, user_id)

        self.host.sidebar_table_name = slow  # type: ignore[method-assign]
        self.sidebar.ask_in_chat(self.table, PLAYER, "does fireball need line of sight?")
        for _ in range(10):
            await asyncio.sleep(0)
        self.host.agreed.discard((GUILD, PLAYER))
        gate.set()
        await self.settle()
        self.assertEqual((self.host.chat, self.host.saved), ([], []))

    async def test_a_question_the_table_refuses_does_not_use_up_the_persons_allowance(self) -> None:
        for n in range(ASK_PER_MINUTE):  # others fill the table's minute
            self.host.agreed.add((GUILD, 40 + n))
            self.assertTrue(self.sidebar.ask_in_chat(self.table, 40 + n, f"question number {n}"))
            await self.settle()
        self.assertFalse(self.sidebar.ask_in_chat(self.table, PLAYER, "one more for the table"))
        self.assertEqual(len(self.table.person_limits[PLAYER]._recent), 0)  # not charged
        self.now[0] += 61
        self.assertTrue(self.sidebar.ask_in_chat(self.table, PLAYER, "now it is my turn"))
        await self.settle()

    async def test_when_the_private_answer_cannot_be_sent_the_table_is_told_only_that(self) -> None:
        self.answerer.answer_to_give = FakeAnswer(text="That's one for your DM.", one_for_dm=True)
        self.host.can_dm = False
        self.sidebar.ask_in_chat(self.table, DM, "who is the masked stranger?")
        await self.settle()
        self.assertEqual(len(self.host.chat), 1)
        self.assertIn(service.COULDNT_SEND, self.host.chat[0])
        self.assertEqual(self.host.saved, [])
        self.assertTrue(self.host.screen)  # the DM is told where they will see it
        self.assertEqual(self.sidebar._busy, set())  # and the table is free again

    async def test_the_q_line_uses_the_ais_few_words_and_falls_back_to_the_shortened_text(
        self,
    ) -> None:
        self.answerer.answer_to_give = FakeAnswer(
            text="No. A point you choose.", question="Does fireball need line of sight"
        )
        self.sidebar.ask_in_chat(
            self.table, PLAYER, "so like when we are in the cave and the wizard casts it, does it"
        )
        await self.settle()
        self.assertTrue(self.host.chat[0].startswith("Q (Mira): Does fireball need line of sight?"))
        self.now[0] += 60
        self.answerer.answer_to_give = FakeAnswer(text="No.")  # no restatement given
        self.sidebar.ask_in_chat(self.table, PLAYER, "does fireball need line of sight")
        await self.settle()
        self.assertTrue(self.host.chat[1].startswith("Q (Mira): Does fireball need line of sight?"))


class SaidAsAnNpc(SaidAtTheTable):
    """#1624: a spoken trigger said as a named NPC never asks DMbot anything."""

    HOLD_ON = "Hold on, I need to find the key to the cellar."
    HEY = "Hey DMbot, what's the range of fireball?"

    async def test_narrating_still_asks(self) -> None:
        from dmbot.transcript.models import NARRATING

        self.assertTrue(self.sidebar.ask_at_table(self.table, DM, self.HOLD_ON, entity=NARRATING))
        await self.settle()
        self.assertEqual(len(self.host.dms), 1)

    async def test_an_unknown_entity_still_asks(self) -> None:
        self.assertTrue(self.sidebar.ask_at_table(self.table, DM, self.HEY, entity=""))
        await self.settle()
        self.assertEqual(len(self.answerer.asked), 1)

    async def test_a_line_said_as_a_named_npc_asks_nothing(self) -> None:
        for said in (self.HOLD_ON, self.HEY):
            self.assertFalse(
                self.sidebar.ask_at_table(self.table, DM, said, entity="Innkeeper Orla")
            )
        self.assertEqual((self.sidebar._tasks, self.host.dms, self.answerer.asked), (set(), [], []))

    async def test_a_player_saying_hey_dmbot_as_a_character_asks_nothing(self) -> None:
        self.assertFalse(self.sidebar.ask_at_table(self.table, PLAYER, self.HEY, entity="Mira"))
        self.assertEqual(self.answerer.asked, [])

    def test_the_pure_test(self) -> None:
        from dmbot.sidebar.ask import voiced_by_npc
        from dmbot.transcript.models import NARRATING

        self.assertFalse(voiced_by_npc(""))
        self.assertFalse(voiced_by_npc(NARRATING))
        self.assertTrue(voiced_by_npc("Innkeeper Orla"))


class RecapFeedback(unittest.IsolatedAsyncioTestCase):
    """ "Hey DMbot, the recap was too long-winded" from the DM (#2076): handled privately, never
    answered by the engine, and the transcript holds the question and a private note only."""

    SAID = "Hey DMbot, the recap was too long-winded"

    def setUp(self) -> None:
        self.host = FakeHost(
            tables={GUILD: make_table(chat=555)}, agreed={(GUILD, DM), (GUILD, PLAYER)}
        )
        self.host.feedback_handled = True  # type: ignore[attr-defined]
        self.answerer = FakeAnswerer()
        self.sidebar = SidebarService(self.host)
        self.sidebar.answerer = self.answerer
        self.table = self.host.tables[GUILD]

    async def settle(self) -> None:
        await asyncio.sleep(0)
        await asyncio.gather(*self.sidebar._tasks)

    async def test_the_dms_feedback_goes_to_the_recap_handler_and_not_the_engine(self) -> None:
        self.assertTrue(self.sidebar.ask_at_table(self.table, DM, self.SAID))
        await self.settle()
        self.assertEqual(len(self.host.feedback_asked), 1)  # type: ignore[attr-defined]
        self.assertEqual(self.answerer.asked, [])
        self.assertEqual(self.host.chat, [])  # nothing for the table

    async def test_the_transcript_holds_the_question_and_a_private_note_only(self) -> None:
        self.sidebar.ask_at_table(self.table, DM, self.SAID)
        await self.settle()
        question, answer = self.host.saved
        self.assertEqual(question.sidebar, SIDEBAR_QUESTION)
        self.assertEqual((answer.sidebar, answer.text), (SIDEBAR_ANSWER, service.PRIVATE_NOTE))

    async def test_a_player_saying_it_goes_the_normal_way(self) -> None:
        self.sidebar.ask_at_table(self.table, PLAYER, self.SAID)
        await self.settle()
        self.assertEqual(getattr(self.host, "feedback_asked", []), [])

    async def test_with_no_recap_waiting_it_is_an_ordinary_question(self) -> None:
        self.host.feedback_handled = False  # type: ignore[attr-defined]
        self.sidebar.ask_at_table(self.table, DM, self.SAID)
        await self.settle()
        self.assertEqual(len(self.answerer.asked), 1)

    async def test_typed_feedback_is_the_buttons_not_the_sidebar(self) -> None:
        self.sidebar.ask_in_chat(self.table, DM, "the recap was too long-winded")
        await self.settle()
        self.assertEqual(getattr(self.host, "feedback_asked", []), [])

    async def test_a_question_about_the_recap_is_an_ordinary_question(self) -> None:
        self.sidebar.ask_at_table(self.table, DM, "Hey DMbot, what was in the recap?")
        await self.settle()
        self.assertEqual(getattr(self.host, "feedback_asked", []), [])


class GuideMe(unittest.IsolatedAsyncioTestCase):
    """ "Hey DMbot, what's next?" from the DM (#1739): Guide me's answer, privately, and never in a
    transcript."""

    GUIDE = "🧭 Probably **Chapter 2** (heard: Bryn). SECRET STORY"
    SAID = "Hey DMbot, what's next?"

    def setUp(self) -> None:
        self.host = FakeHost(
            tables={GUILD: make_table(chat=555)},
            agreed={(GUILD, DM), (GUILD, PLAYER), (GUILD, CO_DM)},
            guide=self.GUIDE,
        )
        self.answerer = FakeAnswerer()
        self.sidebar = SidebarService(self.host)
        self.sidebar.answerer = self.answerer
        self.table = self.host.tables[GUILD]

    async def settle(self) -> None:
        await asyncio.sleep(0)
        await asyncio.gather(*self.sidebar._tasks)

    async def test_the_dm_gets_the_guide_privately_even_with_a_table_chat(self) -> None:
        self.assertTrue(self.sidebar.ask_at_table(self.table, DM, self.SAID))
        await self.settle()
        self.assertEqual(self.host.dms, [(DM, self.GUIDE)])
        self.assertEqual(self.host.chat, [])  # nothing for the table
        self.assertEqual(self.answerer.asked, [])  # no AI

    async def test_no_transcript_line_holds_the_reply(self) -> None:
        self.sidebar.ask_at_table(self.table, DM, self.SAID)
        await self.settle()
        question, answer = self.host.saved
        self.assertEqual(question.sidebar, SIDEBAR_QUESTION)
        self.assertEqual((answer.sidebar, answer.text), (SIDEBAR_ANSWER, service.PRIVATE_NOTE))
        for line in self.host.saved:
            self.assertNotIn("SECRET STORY", f"{line.heard} {line.text}")

    async def test_a_players_whats_next_goes_the_normal_way(self) -> None:
        self.sidebar.ask_at_table(self.table, PLAYER, self.SAID)
        await self.settle()
        self.assertEqual(self.host.guide_asked, [])
        self.assertEqual(len(self.answerer.asked), 1)
        self.assertTrue(all(self.GUIDE not in text for text in self.host.chat))

    async def test_a_question_typed_in_the_table_chat_goes_the_normal_way(self) -> None:
        self.sidebar.ask_in_chat(self.table, DM, "what's next in the adventure?")
        await self.settle()
        self.assertEqual(self.host.guide_asked, [])
        self.assertEqual(len(self.answerer.asked), 1)

    async def test_when_guide_me_is_not_on_the_usual_answer_comes(self) -> None:
        self.host.guide = None  # watch, off, a restored confirmation, or no plan
        self.sidebar.ask_at_table(self.table, DM, self.SAID)
        await self.settle()
        self.assertEqual(self.host.guide_asked, [DM])
        self.assertEqual(len(self.answerer.asked), 1)
        self.assertNotIn(self.GUIDE, [text for _, text in self.host.dms])

    async def test_a_typed_private_message_is_answered_the_same_way(self) -> None:
        message = dm_message(content="what's next?")
        await self.sidebar.on_dm_message(message)
        self.assertEqual(self.host.dms, [(DM, self.GUIDE)])
        self.assertEqual(self.answerer.asked, [])

    async def test_guide_me_works_when_the_answer_engine_is_missing(self) -> None:
        self.sidebar.answerer = None  # no AI key: Guide me needs none (#1745)
        self.assertTrue(self.sidebar.ask_at_table(self.table, DM, self.SAID))
        await self.settle()
        self.assertEqual(self.host.dms, [(DM, self.GUIDE)])
        self.assertEqual(self.host.chat, [])

    async def test_with_no_engine_anything_but_guide_me_is_still_quietly_nothing(self) -> None:
        self.sidebar.answerer = None
        self.assertFalse(self.sidebar.ask_at_table(self.table, DM, "Hey DMbot, what time is it?"))
        self.assertFalse(self.sidebar.ask_at_table(self.table, PLAYER, self.SAID))  # not a DM's
        self.assertFalse(self.sidebar.ask_in_chat(self.table, DM, "what's next in the adventure?"))
        self.assertEqual((self.host.dms, self.host.guide_asked), ([], []))

    async def test_with_no_engine_guide_me_off_says_nothing_and_asks_no_engine(self) -> None:
        self.sidebar.answerer = None
        self.host.guide = None  # Guide me can't be given
        self.sidebar.ask_at_table(self.table, DM, self.SAID)
        await self.settle()
        self.assertEqual(self.host.dms, [])

    async def test_a_typed_whats_next_works_without_an_engine_but_other_questions_wait(
        self,
    ) -> None:
        self.sidebar.answerer = None
        await self.sidebar.on_dm_message(dm_message(content="what's next?"))
        self.assertEqual(self.host.dms, [(DM, self.GUIDE)])
        other = dm_message(content="how does grappling work?")
        await self.sidebar.on_dm_message(other)
        other.channel.send.assert_awaited_once()  # the usual "not yet" note
        self.assertEqual(len(self.host.dms), 1)

    async def test_a_typed_whats_next_with_guide_me_off_and_no_engine_is_told_not_yet(self) -> None:
        self.sidebar.answerer = None
        self.host.guide = None  # Guide me can't be given
        message = dm_message(content="what's next?")
        await self.sidebar.on_dm_message(message)
        self.assertEqual(self.host.dms, [])
        message.channel.send.assert_awaited_once_with(service.NOT_YET, allowed_mentions=ANY)

    async def test_other_questions_about_next_are_not_taken_for_it(self) -> None:
        for said in (
            "Hey DMbot, what's next in initiative?",
            "Hey DMbot, what time is it?",
            "Hey DMbot, if they go north, what's next?",
        ):
            self.host.guide_asked.clear()
            self.sidebar.ask_at_table(self.table, DM, said)
            await self.settle()
            self.assertEqual(self.host.guide_asked, [], said)

    async def test_a_failing_guide_falls_back_to_the_usual_answer_once(self) -> None:
        self.host.guide_raises = True
        self.sidebar.ask_at_table(self.table, DM, self.SAID)
        await self.settle()
        self.assertEqual(len(self.answerer.asked), 1)

    async def test_consent_withdrawn_while_the_guide_is_made_sends_and_saves_nothing(self) -> None:
        self.host.guide_withdraws = True
        self.sidebar.ask_at_table(self.table, DM, self.SAID)
        await self.settle()
        self.assertEqual((self.host.dms, self.host.saved), ([], []))

    async def test_the_private_path_without_a_table_chat_also_gets_it(self) -> None:
        self.table.dmbchat_channel_id = None
        self.sidebar.ask_at_table(self.table, DM, self.SAID)
        await self.settle()
        self.assertEqual(self.host.dms, [(DM, self.GUIDE)])

    async def test_typed_in_the_tables_text_chat_goes_the_usual_way(self) -> None:
        self.sidebar.ask_at_table(self.table, DM, self.SAID, typed=True)
        await self.settle()
        self.assertEqual(self.host.guide_asked, [])

    async def test_a_message_that_could_not_be_sent_says_so_without_the_story(self) -> None:
        self.host.can_dm = False
        self.host.refusal = "failed"
        self.sidebar.ask_at_table(self.table, DM, self.SAID)
        await self.settle()
        await asyncio.sleep(0)
        for _, text in self.host.dms:
            self.assertNotIn("SECRET STORY", text.replace(self.GUIDE, "") + "")
        self.assertEqual(self.host.saved, [])  # nothing is kept for a reply that never arrived

    async def test_nothing_public_or_in_a_test_session_holds_the_reply(self) -> None:
        self.sidebar.ask_at_table(self.table, DM, self.SAID)
        await self.settle()
        self.assertNotIn("SECRET STORY", " ".join(self.host.chat))
        self.assertNotIn("SECRET STORY", " ".join(self.host.screen))

    async def test_a_dm_who_stops_being_recorded_gets_nothing(self) -> None:
        self.host.agreed.discard((GUILD, DM))
        self.sidebar.ask_at_table(self.table, DM, self.SAID)
        await self.settle()
        self.assertEqual((self.host.dms, self.host.saved), ([], []))


class GuidePhrases(unittest.TestCase):
    def test_what_counts_and_what_does_not(self) -> None:
        from dmbot.sidebar.guide_phrase import is_whats_next

        for yes in (
            "what's next",
            "What's next?",
            "whats next",
            "What’s next?",
            "Hey DMbot, what's next?",
            "DMbot what happens next in the adventure",
            "what does the book have next",
            "where are we in the adventure?",
            "hey dm bot where are we in the book please",
            "what's next, DMbot?",
            "ok so tell me what's next now",
            "um what's next in the story",
            "Hey DMbot, can you tell me what comes next?",
            "what's coming up",
            "what chapter are we in",
            "dmbot where are we in the story please",
            "whats up next",
        ):
            self.assertTrue(is_whats_next(yes), yes)
        for no in (
            "what's next in initiative",
            "what's next for the goblin",
            "what time is it",
            "if they go north, what's next",
            "what's next if they go north",
            "I wish I knew what's next",
            "next",
            "",
            "where are we",
            "what's next on the menu",
            "what happens next if the fireball hits",
            "what's next on the clock",
            "where are we in the round",
            "what's up",
            "what's next in the round",
        ):
            self.assertFalse(is_whats_next(no), no)
