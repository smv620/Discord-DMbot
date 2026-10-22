"""Stored session transcripts (#41, #125) against a real Postgres, and the
`/transcript` download flow."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.audio.segmenter import Segmenter, Utterance
from dmbot.bot import DMBot, Table
from dmbot.campaigns import CampaignStore
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.dm_screen import messages as screen_messages
from dmbot.sessions import SessionStore
from dmbot.transcript import export
from dmbot.transcript import questions as name_questions
from dmbot.transcript.models import SIDEBAR_ANSWER, SIDEBAR_QUESTION, VIA_VOICE, Line, Lineage
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.suggestions import Suggestion, SuggestionStore
from dmbot.ui import transcripts as ui
from tests.pg import DatabaseTest

GUILD, OTHER_GUILD, DM, PLAYER, STRANGER, SCREEN = 1, 2, 7, 8, 9, 3
START = 1_700_000_000


def line(seconds: int, user: int, text: str) -> Line:
    return Line(START * 1000 + seconds * 1000, user, text, text)


class StoreTests(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.store = TranscriptStore(self.db)
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)

    async def test_lines_are_saved_and_read_back_in_speaking_order(self) -> None:
        sid = await self.store.open_session(GUILD, self.campaign.id, START)
        ids = await self.store.add_lines(
            GUILD, sid, [line(9, PLAYER, "second"), line(2, DM, "first")]
        )
        self.assertEqual(len(ids), 2)
        lines = await self.store.lines(GUILD, sid)
        self.assertEqual([(x.heard, x.text) for x in lines], [("first",) * 2, ("second",) * 2])

    async def test_the_next_session_start_counts_only_later_sessions_with_lines(self) -> None:
        first = await self.store.open_session(GUILD, self.campaign.id, START)
        await self.store.add_lines(GUILD, first, [line(1, PLAYER, "hello")])
        empty = await self.store.open_session(GUILD, self.campaign.id, START + 100)  # no lines yet
        other = await self.campaigns.create(GUILD, "Sunken City", DM)
        elsewhere = await self.store.open_session(GUILD, other.id, START + 50)
        await self.store.add_lines(GUILD, elsewhere, [line(2, PLAYER, "another game")])
        self.assertIsNone(await self.store.next_session_start(GUILD, self.campaign.id, START))
        self.assertIsNone(await self.store.next_session_start(GUILD, self.campaign.id, START + 500))
        later = await self.store.open_session(GUILD, self.campaign.id, START + 200)
        await self.store.add_lines(GUILD, later, [line(3, PLAYER, "next week")])
        self.assertEqual(
            await self.store.next_session_start(GUILD, self.campaign.id, START), START + 200
        )  # the empty one at START + 100 and the other campaign's do not end the window
        self.assertEqual(empty != later, True)

    async def test_a_typed_line_is_saved_and_read_back_as_typed(self) -> None:
        # Typed chat at the table (#1118): the flag survives the database.
        sid = await self.store.open_session(GUILD, self.campaign.id, START)
        typed = Line(
            START * 1000 + 1000, DM, "we take a long rest", "we take a long rest", typed=True
        )
        await self.store.add_lines(GUILD, sid, [typed, line(9, PLAYER, "said")])
        [first, second] = await self.store.lines(GUILD, sid)
        self.assertEqual((first.typed, second.typed), (True, False))
        # In one batch, in either order, each row keeps its own flag; typed lines are speech
        # for the counts.
        more = [line(20, PLAYER, "said"), Line(START * 1000 + 30, DM, "t", "t", typed=True)]
        await self.store.add_lines(GUILD, sid, more)
        flags = {x.heard: x.typed for x in await self.store.lines(GUILD, sid)}
        self.assertEqual(
            (flags["said"], flags["t"], flags["we take a long rest"]), (False, True, True)
        )

    async def test_table_chat_defaults_on_and_is_kept_per_campaign(self) -> None:
        self.assertEqual(
            (self.campaign.table_chat, self.campaign.table_chat_channel_id), (True, None)
        )
        saved = await self.campaigns.set_table_chat(
            GUILD, self.campaign.id, on=False, channel_id=77
        )
        self.assertEqual((saved.table_chat, saved.table_chat_channel_id), (False, 77))
        other = await self.campaigns.create(GUILD, "Other", DM)
        self.assertEqual((other.table_chat, other.table_chat_channel_id), (True, None))

    async def test_a_backup_carries_the_table_chat_switch_and_no_channel(self) -> None:
        await self.campaigns.set_table_chat(GUILD, self.campaign.id, on=False, channel_id=77)
        data = await self.campaigns.export(GUILD, self.campaign.id)
        self.assertIs(data["campaign"]["table_chat"], False)
        self.assertNotIn("table_chat_channel_id", data["campaign"])
        copy = await self.campaigns.import_backup(GUILD + 1, data, DM)
        self.assertEqual((copy.table_chat, copy.table_chat_channel_id), (False, None))
        # Replacing keeps the campaign's own extra channel and takes the switch.
        await self.campaigns.set_table_chat(GUILD, self.campaign.id, on=True, channel_id=88)
        back = await self.campaigns.import_backup(
            GUILD, data, DM, replace_campaign_id=self.campaign.id
        )
        self.assertEqual((back.table_chat, back.table_chat_channel_id), (False, 88))
        del data["campaign"]["table_chat"]  # a backup from before: on, as a new campaign is
        old = await self.campaigns.import_backup(GUILD + 2, data, DM)
        self.assertTrue(old.table_chat)

    async def test_the_channels_that_are_never_chat_cover_every_campaign(self) -> None:
        other = await self.campaigns.create(GUILD, "Second", DM)
        await self.campaigns.set_dm_screen(GUILD, self.campaign.id, 11)
        await self.campaigns.set_dm_screen(GUILD, other.id, 12)
        await self.campaigns.set_transcript_channel(GUILD, other.id, 13)
        self.assertEqual(await self.campaigns.special_channel_ids(GUILD), frozenset({11, 12, 13}))
        self.assertEqual(await self.campaigns.special_channel_ids(GUILD + 9), frozenset())

    async def test_sidebar_lines_are_kept_apart_and_never_edited(self) -> None:
        sid = await self.store.open_session(GUILD, self.campaign.id, START)
        asked_from = Lineage(ref="a1b2c3", via=VIA_VOICE, stt="deepgram nova-3 host")
        answered_from = Lineage(
            reply_to="a1b2c3", model="m-1", prompt="p-1", sources=("SRD p. 1", "house rule 3")
        )
        asked = Line(
            START * 1000 + 5000,
            DM,
            "find flanking",
            "find flanking",
            0,
            "game",
            SIDEBAR_QUESTION,
            asked_from,
        )
        answer = Line(
            START * 1000 + 7000,
            DM,
            "Optional. (sure)",
            "Optional. (sure)",
            0,
            "game",
            SIDEBAR_ANSWER,
            answered_from,
        )
        await self.store.add_lines(GUILD, sid, [asked, answer, line(5, DM, "table speech")])
        # They are not speech: the session counts one line and one speaker, as without them.
        (counted,) = await self.store.sessions(GUILD, self.campaign.id)
        self.assertEqual(counted.lines, 1)
        lines = await self.store.lines(GUILD, sid)
        self.assertEqual(
            [(x.heard, x.sidebar) for x in lines],
            [("find flanking", "question"), ("table speech", ""), ("Optional. (sure)", "answer")],
        )
        # Where each came from is kept as columns, and table speech has none.
        by_kind = {x.sidebar: x for x in lines}
        self.assertEqual(by_kind["question"].lineage, asked_from)
        self.assertEqual(by_kind["answer"].lineage, answered_from)
        self.assertEqual(by_kind[""].lineage, Lineage())
        # An Undo, the off-topic filter or Put it back at the same moment as a sidebar line
        # changes the speech line only: the question and the answer stay as they were.
        key = asked.started_ms
        self.assertEqual(await self.store.relabel_line(GUILD, sid, DM, key, "x"), 1)
        self.assertEqual(await self.store.set_topic(GUILD, sid, DM, key, "off_topic"), 1)
        self.assertEqual(await self.store.set_topics(GUILD, sid, DM, [key], "table_talk"), 1)
        again = await self.store.lines(GUILD, sid)
        self.assertEqual(
            [(x.text, x.topic) for x in again if x.sidebar],
            [("find flanking", "game"), ("Optional. (sure)", "game")],
        )
        speech = next(x for x in again if not x.sidebar)
        self.assertEqual((speech.text, speech.topic), ("x", "table_talk"))
        # With no speech at that moment, nothing at all is changed.
        self.assertEqual(await self.store.relabel_line(GUILD, sid, DM, answer.started_ms, "x"), 0)
        self.assertEqual(
            await self.store.set_topics(GUILD, sid, DM, [answer.started_ms], "off_topic"), 0
        )

    async def test_a_resumed_session_keeps_its_transcript(self) -> None:
        sid = await self.store.open_session(GUILD, self.campaign.id, START)
        await self.store.end_session(GUILD, sid, START + 60)
        self.assertEqual(await self.store.open_session(GUILD, self.campaign.id, START), sid)
        session = await self.store.session(GUILD, sid)
        assert session is not None
        self.assertIsNone(session.ended_at)  # running again

    async def test_each_session_records_which_speech_to_text_wrote_it(self) -> None:
        deepgram = "deepgram nova-3 api.deepgram.com"
        sid = await self.store.open_session(GUILD, self.campaign.id, START, deepgram)
        await self.store.add_lines(GUILD, sid, [line(1, DM, "a")])
        await self.store.open_session(GUILD, self.campaign.id, START, deepgram)  # resumed
        session = await self.store.session(GUILD, sid)
        assert session is not None
        self.assertEqual(session.engines, (deepgram,))  # once, however often it resumes
        # A restart that switched engine: both, in order. No engine (none): nothing new.
        await self.store.open_session(GUILD, self.campaign.id, START, "whisper-local small local")
        await self.store.open_session(GUILD, self.campaign.id, START)
        (listed,) = await self.store.sessions(GUILD, self.campaign.id)
        self.assertEqual(listed.engines, (deepgram, "whisper-local small local"))

    async def test_sessions_are_numbered_and_listed_newest_first_with_counts(self) -> None:
        old = await self.store.open_session(GUILD, self.campaign.id, START)
        empty = await self.store.open_session(GUILD, self.campaign.id, START + 3600)
        new = await self.store.open_session(GUILD, self.campaign.id, START + 86400)
        await self.store.add_lines(GUILD, old, [line(1, DM, "a")])
        await self.store.add_lines(GUILD, new, [line(1, DM, "a"), line(2, PLAYER, "b")])
        await self.store.add_lines(GUILD, new, [line(3, PLAYER, "c")])
        sessions = await self.store.sessions(GUILD, self.campaign.id)
        self.assertEqual([s.id for s in sessions], [new, old])  # nothing said: not listed
        self.assertNotIn(empty, [s.id for s in sessions])
        self.assertEqual([s.number for s in sessions], [2, 1])
        self.assertEqual((sessions[0].lines, sessions[0].speakers), (3, (DM, PLAYER)))
        one = await self.store.session(GUILD, new)
        assert one is not None
        self.assertEqual((one.number, one.lines), (2, 3))
        # #317: the /transcript picker's count, for every campaign at once.
        self.assertEqual(await self.store.session_counts(GUILD), {self.campaign.id: 2})

    async def test_a_line_can_be_relabelled_and_heard_never_changes(self) -> None:
        # #296: an Undo puts the heard words back in a saved line
        sid = await self.store.open_session(GUILD, self.campaign.id, START)
        fixed = Line(START * 1000 + 1000, PLAYER, "I saw Beleros", "I saw Belleros")
        await self.store.add_lines(GUILD, sid, [fixed])
        changed = await self.store.relabel_line(
            GUILD, sid, PLAYER, fixed.started_ms, "I saw Beleros"
        )
        self.assertEqual(changed, 1)
        (back,) = await self.store.lines(GUILD, sid)
        self.assertEqual((back.heard, back.text), ("I saw Beleros", "I saw Beleros"))
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT text FROM transcript_lines WHERE session_id = %s", (sid,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertIsNone(row["text"])  # the same as heard again: stored as NULL
        await self.store.relabel_line(GUILD, sid, PLAYER, fixed.started_ms, "I saw Belleros!")
        (back,) = await self.store.lines(GUILD, sid)
        self.assertEqual((back.heard, back.text), ("I saw Beleros", "I saw Belleros!"))
        self.assertEqual(
            await self.store.relabel_line(GUILD, sid, PLAYER, 1, "x"), 0
        )  # no such line
        self.assertEqual(
            await self.store.relabel_line(OTHER_GUILD, sid, PLAYER, fixed.started_ms, "x"), 0
        )  # never another server's

    async def test_a_lines_topic_and_length_are_kept(self) -> None:
        # #52: the filter's topic is saved later; the length comes with the line.
        sid = await self.store.open_session(GUILD, self.campaign.id, START)
        said = Line(START * 1000 + 1000, PLAYER, "my boss called", "my boss called", 4_200)
        await self.store.add_lines(GUILD, sid, [said])
        (back,) = await self.store.lines(GUILD, sid)
        self.assertEqual((back.duration_ms, back.topic), (4_200, "game"))  # until told
        changed = await self.store.set_topic(GUILD, sid, PLAYER, said.started_ms, "off_topic")
        self.assertEqual(changed, 1)
        (back,) = await self.store.lines(GUILD, sid)
        self.assertEqual(back.topic, "off_topic")
        self.assertEqual(await self.store.set_topic(GUILD, sid, PLAYER, 1, "off_topic"), 0)
        self.assertEqual(
            await self.store.set_topic(OTHER_GUILD, sid, PLAYER, said.started_ms, "game"), 0
        )  # never another server's

    async def test_who_a_line_was_said_as_is_kept(self) -> None:
        # #53: saved with the line, or set later; nothing named reads back as "".
        sid = await self.store.open_session(GUILD, self.campaign.id, START)
        plain, as_npc = line(1, DM, "Hello."), line(2, DM, "Welcome.")
        await self.store.add_lines(GUILD, sid, [plain, replace(as_npc, entity="narrating")])
        back = await self.store.lines(GUILD, sid)
        self.assertEqual([x.entity for x in back], ["", "narrating"])
        self.assertEqual(await self.store.set_entity(GUILD, sid, DM, plain.started_ms, "Hiro"), 1)
        self.assertEqual(await self.store.set_entity(GUILD, sid, DM, as_npc.started_ms, ""), 1)
        back = await self.store.lines(GUILD, sid)
        self.assertEqual([x.entity for x in back], ["Hiro", ""])
        self.assertEqual(await self.store.set_entity(GUILD, sid, DM, 1, "x"), 0)
        self.assertEqual(
            await self.store.set_entity(OTHER_GUILD, sid, DM, plain.started_ms, "x"), 0
        )  # never another server's

    async def test_put_it_back_changes_one_persons_lines_at_once(self) -> None:
        # #677: one statement for a run; another person's line and server are untouched.
        sid = await self.store.open_session(GUILD, self.campaign.id, START)
        lines = [line(1, PLAYER, "a"), line(2, PLAYER, "b"), line(3, DM, "c")]
        await self.store.add_lines(GUILD, sid, lines)
        for said in lines:
            await self.store.set_topic(GUILD, sid, said.user_id, said.started_ms, "off_topic")
        run = [lines[0].started_ms, lines[1].started_ms]
        self.assertEqual(await self.store.set_topics(OTHER_GUILD, sid, PLAYER, run, "game"), 0)
        self.assertEqual(await self.store.set_topics(GUILD, sid, DM, run, "game"), 0)
        self.assertEqual(await self.store.set_topics(GUILD, sid, PLAYER, run, "game"), 2)
        topics = [back.topic for back in await self.store.lines(GUILD, sid)]
        self.assertEqual(topics, ["game", "game", "off_topic"])

    async def test_removing_lines_counts_again(self) -> None:
        sid = await self.store.open_session(GUILD, self.campaign.id, START)
        ids = await self.store.add_lines(GUILD, sid, [line(1, DM, "a"), line(2, PLAYER, "b")])
        await self.store.remove_lines(GUILD, sid, [ids[1]])
        session = await self.store.session(GUILD, sid)
        assert session is not None
        self.assertEqual((session.lines, session.speakers), (1, (DM,)))

    async def test_another_server_sees_nothing(self) -> None:
        sid = await self.store.open_session(GUILD, self.campaign.id, START)
        await self.store.add_lines(GUILD, sid, [line(1, DM, "secret plans")])
        self.assertIsNone(await self.store.session(OTHER_GUILD, sid))
        self.assertEqual(await self.store.lines(OTHER_GUILD, sid), [])
        self.assertEqual(await self.store.sessions(OTHER_GUILD, self.campaign.id), [])
        self.assertEqual(await self.store.session_counts(OTHER_GUILD), {})

    async def test_another_campaign_in_the_same_server_is_listed_apart(self) -> None:
        other = await self.campaigns.create(GUILD, "Strahd", DM)
        mine = await self.store.open_session(GUILD, self.campaign.id, START)
        theirs = await self.store.open_session(GUILD, other.id, START)
        await self.store.add_lines(GUILD, mine, [line(1, DM, "a")])
        await self.store.add_lines(GUILD, theirs, [line(1, DM, "b")])
        listed = await self.store.sessions(GUILD, self.campaign.id)
        self.assertEqual([(s.id, s.number) for s in listed], [(mine, 1)])
        counts = await self.store.session_counts(GUILD)
        self.assertEqual(counts, {self.campaign.id: 1, other.id: 1})

    async def test_deleting_the_campaign_deletes_its_transcripts(self) -> None:
        sid = await self.store.open_session(GUILD, self.campaign.id, START)
        await self.store.add_lines(GUILD, sid, [line(1, DM, "a")])
        await self.campaigns.delete(GUILD, self.campaign.id)
        self.assertIsNone(await self.store.session(GUILD, sid))


class FakeResponse:
    def __init__(self) -> None:
        self.done = False
        self.deferred = False
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.edited: list[tuple[str, Any]] = []

    def is_done(self) -> bool:
        return self.done

    async def defer(self, **_: Any) -> None:
        self.done = self.deferred = True

    async def send_message(self, text: str = "", **kw: Any) -> None:
        self.done = True
        self.sent.append((text, kw))

    async def edit_message(self, *, content: str, view: Any, **_: Any) -> None:
        self.done = True
        self.edited.append((content, view))


def followups(it: Any) -> list[tuple[str, dict[str, Any]]]:
    return [(c.args[0] if c.args else "", c.kwargs) for c in it.followup.send.await_args_list]


def file_text(it: Any) -> str:
    (_, kw), *_ = [f for f in followups(it) if "file" in f[1]]
    return str(kw["file"].fp.read().decode())


class BotTests(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.consent = ConsentStore(self.db)
        self.campaigns = CampaignStore(self.db)
        self.store = TranscriptStore(self.db)
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            self.consent,
            self.campaigns,
            SessionStore(self.db),
            transcripts=self.store,
        )
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        names = {DM: "Sam", PLAYER: "Mia"}
        self.guild = MagicMock(spec=discord.Guild)
        self.guild.id = GUILD
        self.guild.get_member = lambda uid: (
            SimpleNamespace(display_name=names[uid]) if uid in names else None
        )
        self.guild.fetch_member = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "Unknown Member")
        )
        self.bot.get_guild = (  # type: ignore[method-assign]
            lambda gid: self.guild if gid == GUILD else None
        )
        self.bot.post = AsyncMock(return_value=True)  # type: ignore[method-assign]

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
        self.bot.tables[GUILD] = table
        return table

    def said(self, table: Table, user: int, text: str, seconds: int = 1) -> None:
        start_ms = START * 1000 + seconds * 1000
        utterance = Utterance(GUILD, user, start_ms, start_ms, b"", table.segmenter.session)
        self.bot._deliver_transcript(utterance, text)

    def it(self, user_id: int = PLAYER) -> Any:
        return SimpleNamespace(
            client=self.bot,
            guild=self.guild,
            guild_id=GUILD,
            user=SimpleNamespace(id=user_id),
            response=FakeResponse(),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def saved(self, table: Table) -> list[tuple[int, str]]:
        assert table.transcript_session_id is not None
        lines = await self.store.lines(GUILD, table.transcript_session_id)
        return [(x.user_id, x.heard) for x in lines]

    async def test_only_people_who_still_agree_are_saved(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        await self.consent.grant(GUILD, DM)
        table = self.table()
        self.said(table, PLAYER, "I attack the goblin.")
        self.said(table, DM, "Roll for it.", 2)
        self.said(table, STRANGER, "never agreed", 3)
        self.bot.stop_recording(GUILD, DM)  # pressed Stop before the save
        await self.bot.save_transcript(table)
        self.assertEqual(await self.saved(table), [(PLAYER, "I attack the goblin.")])

    async def test_stop_pressed_while_saving_takes_the_lines_back_out(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        await self.consent.grant(GUILD, DM)
        table = self.table()
        self.said(table, PLAYER, "keep me")
        self.said(table, DM, "drop me", 2)
        real = self.store.add_lines
        saving, release = asyncio.Event(), asyncio.Event()

        async def slow(*args: Any) -> list[int]:
            saving.set()
            await release.wait()
            return await real(*args)

        self.store.add_lines = slow  # type: ignore[method-assign,assignment]
        save = asyncio.create_task(self.bot.save_transcript(table))
        await saving.wait()
        self.bot.stop_recording(GUILD, DM)
        await self.consent.revoke(GUILD, DM)
        release.set()
        await save
        self.assertEqual(await self.saved(table), [(PLAYER, "keep me")])

    async def kept(self, table: Table) -> dict[int, str]:
        assert table.transcript_session_id is not None
        session = await self.store.session(GUILD, table.transcript_session_id)
        assert session is not None
        return dict(session.speaker_names)

    async def test_the_speakers_names_are_kept_with_the_session_once(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        await self.consent.grant(GUILD, DM)
        table = self.table()
        self.said(table, PLAYER, "I attack.")
        self.said(table, DM, "Roll.", 2)
        await self.bot.save_transcript(table)
        self.assertEqual(await self.kept(table), {PLAYER: "Mia", DM: "Sam"})
        self.guild.get_member = lambda uid: SimpleNamespace(display_name="Renamed")
        self.said(table, PLAYER, "again", 3)
        await self.bot.save_transcript(table)
        self.assertEqual(await self.kept(table), {PLAYER: "Mia", DM: "Sam"})  # not looked up again

    async def test_someone_who_stopped_while_saving_is_not_named(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        await self.consent.grant(GUILD, DM)
        table = self.table()
        self.said(table, PLAYER, "keep me")
        self.said(table, DM, "drop me", 2)
        real = self.store.add_lines
        saving, release = asyncio.Event(), asyncio.Event()

        async def slow(*args: Any) -> list[int]:
            saving.set()
            await release.wait()
            return await real(*args)

        self.store.add_lines = slow  # type: ignore[method-assign,assignment]
        save = asyncio.create_task(self.bot.save_transcript(table))
        await saving.wait()
        await self.consent.revoke(GUILD, DM)
        release.set()
        await save
        self.assertEqual(await self.kept(table), {PLAYER: "Mia"})

    async def test_a_server_not_there_to_ask_keeps_nothing_and_is_tried_again(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        table = self.table()
        self.said(table, PLAYER, "hello")
        self.bot.get_guild = lambda gid: None  # type: ignore[method-assign,assignment]
        await self.bot.save_transcript(table)
        self.assertEqual(await self.kept(table), {})
        self.bot.get_guild = lambda gid: self.guild  # type: ignore[method-assign]
        self.said(table, PLAYER, "again", 2)
        await self.bot.save_transcript(table)
        self.assertEqual(await self.kept(table), {PLAYER: "Mia"})

    async def test_the_start_up_fill_in_names_older_sessions_and_marks_those_gone(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        table = self.table()
        self.said(table, PLAYER, "hello")
        self.said(table, STRANGER, "left the server", 2)
        await self.consent.grant(GUILD, STRANGER)
        self.bot.get_guild = lambda gid: None  # type: ignore[method-assign,assignment]
        await self.bot.save_transcript(table)  # saved, but with no name kept
        self.bot.get_guild = lambda gid: self.guild  # type: ignore[method-assign]
        with patch.object(DMBot, "guilds", new=[self.guild]), patch("asyncio.sleep", AsyncMock()):
            await self.bot._name_old_speakers()
        self.assertEqual(await self.kept(table), {PLAYER: "Mia", STRANGER: ""})
        self.assertEqual(await self.store.unnamed(GUILD), [])  # one-off: nothing left to do

    async def test_a_failed_start_loses_nothing_and_warns_the_dm_once(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        table = self.table()
        real = self.store.open_session
        self.store.open_session = AsyncMock(side_effect=OSError("down"))  # type: ignore[method-assign]
        self.said(table, PLAYER, "first")
        await self.bot.save_transcript(table)
        await self.bot.save_transcript(table)
        self.bot.post.assert_awaited_once_with(  # type: ignore[attr-defined]
            SCREEN, screen_messages.TRANSCRIPT_NOT_SAVED
        )
        self.store.open_session = real  # type: ignore[method-assign]
        self.said(table, PLAYER, "second", 2)
        await self.bot.save_transcript(table)
        self.assertEqual(await self.saved(table), [(PLAYER, "first"), (PLAYER, "second")])

    async def test_a_failed_save_is_retried(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        table = self.table()
        real = self.store.add_lines
        self.store.add_lines = AsyncMock(side_effect=OSError("down"))  # type: ignore[method-assign]
        self.said(table, PLAYER, "first")
        await self.bot.save_transcript(table)
        self.store.add_lines = real  # type: ignore[method-assign]
        self.said(table, PLAYER, "second", 2)
        await self.bot.save_transcript(table)
        self.assertEqual(await self.saved(table), [(PLAYER, "first"), (PLAYER, "second")])

    async def test_a_restart_keeps_adding_to_the_same_transcript(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        before = self.table()
        self.said(before, PLAYER, "before")
        await self.bot.save_transcript(before)
        after = self.table()  # same campaign and start time, as after a restart
        self.said(after, PLAYER, "after", 2)
        await self.bot.save_transcript(after)
        self.assertEqual(after.transcript_session_id, before.transcript_session_id)
        self.assertEqual(await self.saved(after), [(PLAYER, "before"), (PLAYER, "after")])

    async def test_at_the_end_the_dm_and_everyone_recorded_get_a_download(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        table = self.table()
        self.said(table, PLAYER, "Hello.")
        users: dict[int, Any] = {}

        def get_user(uid: int) -> Any:
            return users.setdefault(uid, SimpleNamespace(id=uid, bot=False, send=AsyncMock()))

        self.bot.get_user = get_user  # type: ignore[method-assign]
        del self.bot.tables[GUILD]
        await self.bot.finish_transcript(table)  # saves the last lines first
        self.assertEqual(set(users), {DM, PLAYER})
        call = users[PLAYER].send.await_args
        self.assertIn("The session for **Frostmaiden** has ended", call.args[0])
        sid = table.transcript_session_id
        self.assertEqual(
            [b.custom_id for b in call.kwargs["view"].children],
            [f"dmbot:transcript:{GUILD}:{sid}:{c}" for c in ("cleaned", "heard", "both")]
            + [f"dmbot:fixline:{GUILD}:{sid}"],  # and a player's suggested fix (#1491)
        )
        session = await self.store.session(GUILD, sid or "")
        assert session is not None and session.ended_at is not None

    async def end_with_gate(self, gate: Any, allows: Any = None) -> dict[int, Any]:
        await self.consent.grant(GUILD, PLAYER)
        table = self.table()
        self.said(table, PLAYER, "Hello.")
        users: dict[int, Any] = {}

        def get_user(uid: int) -> Any:
            return users.setdefault(uid, SimpleNamespace(id=uid, bot=False, send=AsyncMock()))

        self.bot.get_user = get_user  # type: ignore[method-assign]
        self.bot.plan_gate = gate  # type: ignore[method-assign]
        if allows is not None:
            self.bot.plan_allows = allows  # type: ignore[method-assign]
        del self.bot.tables[GUILD]
        await self.bot.finish_transcript(table)
        return users

    async def test_a_plan_without_downloads_tells_only_the_owner_and_sends_no_buttons(
        self,
    ) -> None:
        # Try It with plans enforced (#938): the gate refuses, so no buttons that every press
        # would refuse. Players have nothing to act on, so only the owner (the DM here) is told.
        async def gate(action: str, guild: int, campaign: Any, user: int) -> str | None:
            assert action == "transcript"
            return "Try It campaigns don't keep downloads." if user == DM else "Ask the owner."

        users = await self.end_with_gate(gate, AsyncMock(return_value=False))
        self.assertEqual(set(users), {DM})  # the player was not messaged at all
        call = users[DM].send.await_args
        self.assertIn("Its transcript is kept, but this campaign's plan", call.args[0])
        self.assertIn("doesn't include downloads", call.args[0])
        self.assertIn("Try It campaigns don't keep downloads.", call.args[0])
        self.assertNotIn("view", call.kwargs)
        self.assertNotIn("/transcript", call.args[0])  # that would be refused too

    async def test_the_owner_is_told_even_if_they_were_not_at_the_table(self) -> None:
        campaign = await self.campaigns.get(GUILD, self.campaign.id)
        assert campaign is not None
        outsider = 999
        self.campaigns.get = AsyncMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(
                id=campaign.id, owner_user_id=outsider, dm_user_ids=campaign.dm_user_ids
            )
        )

        async def gate(action: str, guild: int, campaign: Any, user: int) -> str | None:
            return "Pick a plan." if user == outsider else "Ask the owner."

        users = await self.end_with_gate(gate, AsyncMock(return_value=False))
        self.assertEqual(set(users), {outsider})
        self.assertIn("Pick a plan.", users[outsider].send.await_args.args[0])

    async def test_with_no_owner_the_dms_are_told_to_take_it_on_and_players_are_not(self) -> None:
        campaign = await self.campaigns.get(GUILD, self.campaign.id)
        assert campaign is not None
        self.campaigns.get = AsyncMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(
                id=campaign.id, owner_user_id=None, dm_user_ids=campaign.dm_user_ids
            )
        )
        users = await self.end_with_gate(
            AsyncMock(return_value="no owner"), AsyncMock(return_value=False)
        )
        self.assertEqual(set(users), {DM})
        self.assertIn("Take it on", users[DM].send.await_args.args[0])

    async def test_a_plan_with_downloads_keeps_the_buttons_and_adds_no_line(self) -> None:
        users = await self.end_with_gate(AsyncMock(return_value=None), AsyncMock(return_value=True))
        for user in users.values():
            call = user.send.await_args
            self.assertEqual(
                len(call.kwargs["view"].children), 4
            )  # three downloads and ✏️ Fix (#1491)
            self.assertIn("Download the transcript", call.args[0])

    async def test_the_plan_is_asked_at_most_twice_for_the_whole_table(self) -> None:
        gate, allows = AsyncMock(return_value="no"), AsyncMock(return_value=False)
        await self.end_with_gate(gate, allows)
        self.assertEqual((allows.await_count, gate.await_count), (1, 1))  # blocked?; owner's line

    async def test_the_real_gate_tells_only_the_owner_who_is_not_at_the_table(self) -> None:
        import dataclasses

        from dmbot.entitlements import Access

        outsider = 999
        campaign = await self.campaigns.get(GUILD, self.campaign.id)
        assert campaign is not None
        self.campaigns.get = AsyncMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(
                id=campaign.id, owner_user_id=outsider, dm_user_ids=campaign.dm_user_ids
            )
        )
        self.bot.settings = dataclasses.replace(
            self.bot.settings, enforce_plans=True, site_url="https://dmbot.example"
        )
        try_it = Access("paid", "Try It", 480, 1, False)  # plans.json: Try It has no backups
        self.bot.meter = SimpleNamespace(access=AsyncMock(return_value=try_it))  # type: ignore[assignment]
        users = await self.end_with_gate(self.bot.plan_gate)  # the real gate and check
        self.assertEqual(set(users), {outsider})
        text = users[outsider].send.await_args.args[0]
        self.assertIn("Try It campaigns can't make copies or transcripts", text)
        self.assertIn("https://dmbot.example/account", text)

    async def test_a_failing_plan_check_still_sends_the_downloads(self) -> None:
        self.campaigns.get = AsyncMock(side_effect=OSError("down"))  # type: ignore[method-assign]
        with self.assertLogs("dmbot.bot", "ERROR"):
            users = await self.end_with_gate(
                AsyncMock(return_value="no"), AsyncMock(return_value=False)
            )
        self.assertEqual(set(users), {DM, PLAYER})
        for user in users.values():
            self.assertIn("view", user.send.await_args.kwargs)

    async def test_a_missing_campaign_keeps_the_downloads(self) -> None:
        self.campaigns.get = AsyncMock(return_value=None)  # type: ignore[method-assign]
        users = await self.end_with_gate(
            AsyncMock(return_value="no"), AsyncMock(return_value=False)
        )
        self.assertEqual(set(users), {DM, PLAYER})

    async def test_nothing_said_sends_nothing(self) -> None:
        table = self.table()
        self.bot.get_user = MagicMock()  # type: ignore[method-assign]
        await self.bot.finish_transcript(table)
        self.bot.get_user.assert_not_called()

    async def finished_session(self) -> str:
        await self.consent.grant(GUILD, PLAYER)
        table = self.table()
        self.said(table, PLAYER, "We ride at dawn.", 42)
        await self.bot.save_transcript(table)
        del self.bot.tables[GUILD]
        assert table.transcript_session_id is not None
        await self.store.end_session(GUILD, table.transcript_session_id, START + 3600)
        return table.transcript_session_id

    async def test_anyone_in_the_server_can_download_with_the_command(self) -> None:
        sid = await self.finished_session()
        it = self.it(STRANGER)  # never recorded, still in the server
        await ui.transcript_command.callback(it)  # type: ignore[call-arg]
        self.assertTrue(it.response.deferred)  # answered within Discord's 3 seconds
        ((text, kw),) = followups(it)
        self.assertIn("Which session of Frostmaiden?", text)
        view = kw["view"]
        self.assertIn("Session 1", view.pick.options[0].label)
        view.pick = SimpleNamespace(values=[sid])
        it = self.it(STRANGER)
        await view._picked(it)
        self.assertTrue(it.response.deferred)
        # the cleaned file at once, with the word-for-word one a press away
        self.assertIn("[0:00:42] (Mia): We ride at dawn.", file_text(it))
        (_, kw), *_ = [f for f in followups(it) if "file" in f[1]]
        self.assertEqual(kw["file"].filename, "frostmaiden-session-1-cleaned.txt")
        it = await self.press(kw["view"], "🎙 As heard")
        name = next(f for f in followups(it) if "file" in f[1])[1]["file"].filename
        self.assertEqual(name, "frostmaiden-session-1-as-heard.txt")

    async def press(self, view: Any, label: str) -> Any:
        it = self.it(STRANGER)
        button = next(b for b in view.children if b.label == label)
        await button.callback(it)
        return it

    async def test_the_download_marks_the_dm_and_keeps_the_name_of_someone_who_left(self) -> None:
        sid = await self.finished_session()
        await self.store.add_lines(
            GUILD, sid, [Line(START * 1000 + 60_000, DM, "Roll for it.", "Roll for it.")]
        )
        async with self.store.db.guild(GUILD) as conn:  # the name kept when Mia was still here
            await conn.execute(
                "UPDATE transcript_sessions SET speaker_names = %s::jsonb WHERE id = %s",
                (f'{{"{PLAYER}": "Mia then"}}', sid),
            )
        self.guild.get_member = lambda uid: (  # Mia has left the server; Sam is still there
            SimpleNamespace(display_name="Sam") if uid == DM else None
        )
        result = await ui.make_file(self.bot, self.guild, GUILD, sid, PLAYER, (export.AS_HEARD,))
        assert not isinstance(result, str)
        text = result[0].fp.read().decode()
        self.assertIn("(Sam, DM): Roll for it.", text)  # today's name, and the mark
        self.assertIn("(Mia then): We ride at dawn.", text)  # the kept name, not "Someone"

    async def test_everyone_who_can_read_the_raw_file_sees_the_sidebar_lines_and_nobody_the_cleaned(
        self,
    ) -> None:
        sid = await self.finished_session()
        asked = Line(
            START * 1000 + 50_000,
            DM,
            "find if flanking is optional",
            "find if flanking is optional",
            0,
            "game",
            SIDEBAR_QUESTION,
            Lineage(ref="a1b2c3", via=VIA_VOICE, stt="deepgram nova-3 host"),
        )
        answer = Line(
            START * 1000 + 52_000,
            DM,
            "Yes, it is optional. (sure)",
            "Yes, it is optional. (sure)",
            0,
            "game",
            SIDEBAR_ANSWER,
            Lineage(reply_to="a1b2c3", model="m-1", prompt="p-1", sources=("SRD p. 1",)),
        )
        await self.store.add_lines(GUILD, sid, [asked, answer])
        result = await ui.make_file(
            self.bot, self.guild, GUILD, sid, PLAYER, (export.AS_HEARD, export.CLEANED)
        )
        assert not isinstance(result, str)
        heard, cleaned = (f.fp.read().decode() for f in result)
        self.assertIn(
            "(Sam, DM) [DM Sidebar id=a1b2c3 via=voice-memo stt=deepgram/nova-3]: "
            "find if flanking is optional",
            heard,
        )
        self.assertIn(
            "(DMbot) [DM Sidebar reply-to=a1b2c3 model=m-1 prompt=p-1 sources=SRD-p.-1]: "
            "Yes, it is optional. (sure)",
            heard,
        )
        self.assertNotIn("Sidebar", cleaned)
        self.assertNotIn("flanking", cleaned)
        self.assertIn("We ride at dawn.", cleaned)

    async def test_both_versions_come_as_two_files(self) -> None:
        sid = await self.finished_session()
        it = self.it(PLAYER)
        await ui.DownloadButton(GUILD, sid, "both").callback(it)  # sent when it ended
        (_, kw), *_ = [f for f in followups(it) if "files" in f[1]]
        self.assertEqual(
            [f.filename for f in kw["files"]],
            ["frostmaiden-session-1-cleaned.txt", "frostmaiden-session-1-as-heard.txt"],
        )

    async def test_a_running_session_warns_first(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        table = self.table()
        self.said(table, PLAYER, "still going")
        await self.bot.save_transcript(table)
        (session,) = await self.store.sessions(GUILD, self.campaign.id)
        view = ui.SessionPicker(self.bot, GUILD, [session])
        self.assertIn("recording now", view.pick.options[0].label)
        view.pick = SimpleNamespace(values=[session.id])  # type: ignore[assignment]
        it = self.it(PLAYER)
        await view._picked(it)
        text, menu = it.response.edited[0]
        self.assertIn("You'll get the full transcript in a private message", text)
        it = self.it(PLAYER)
        await menu._anyway(it)
        self.assertIn("still recording", file_text(it))

    async def test_an_unknown_or_other_servers_session_is_gone(self) -> None:
        it = self.it()
        await ui.send_file(it, GUILD, "f" * 32)
        self.assertEqual(followups(it)[0][0], ui.GONE)

    async def test_the_button_works_after_a_restart_for_people_in_the_server(self) -> None:
        sid = await self.finished_session()
        button = ui.DownloadButton(GUILD, sid)
        match = ui.DownloadButton.__discord_ui_compiled_template__.fullmatch(
            button.item.custom_id or ""
        )
        assert match is not None
        again = await ui.DownloadButton.from_custom_id(self.it(), button.item, match)
        it = self.it(PLAYER)
        await again.callback(it)
        self.assertIn("We ride at dawn.", file_text(it))
        it = self.it(STRANGER)  # not in the server (any more)
        await again.callback(it)
        self.assertIn("Only people in that server", followups(it)[0][0])

    async def test_names_dmbot_cant_find_show_as_someone(self) -> None:
        names = await ui.display_names(self.guild, (PLAYER, STRANGER))
        self.assertEqual(names, {PLAYER: "Mia"})

    # ---- a player's suggested fix, once the DM accepts it (#1491) ------------------------

    async def fix_setup(self) -> tuple[Table, str]:
        await self.consent.grant(GUILD, PLAYER)
        self.bot.suggestions = SuggestionStore(self.db)
        table = self.table()
        self.said(table, PLAYER, "I saw Beleros")
        await self.bot.save_transcript(table)
        sid = table.transcript_session_id
        assert sid is not None
        return table, sid

    async def test_an_accepted_fix_changes_the_saved_words_of_a_finished_session(self) -> None:
        table, sid = await self.fix_setup()
        del self.bot.tables[GUILD]  # the session is over
        started = START * 1000 + 1000
        done = await self.bot.apply_line_fix(
            GUILD, table.campaign_id or "", sid, PLAYER, started, "I saw Belleros"
        )
        self.assertTrue(done)
        (line,) = await self.store.lines(GUILD, sid)
        self.assertEqual((line.heard, line.text), ("I saw Beleros", "I saw Belleros"))

    async def test_an_accepted_fix_in_a_running_session_goes_through_the_notes_too(self) -> None:
        table, sid = await self.fix_setup()
        started = START * 1000 + 1000
        self.assertTrue(
            await self.bot.apply_line_fix(
                GUILD, table.campaign_id or "", sid, PLAYER, started, "I saw Belleros"
            )
        )
        (note,) = table.fix_notes.answers  # a later Undo of a name fix keeps these words
        self.assertEqual((note.heard, note.written), ("I saw Beleros", "I saw Belleros"))
        (line,) = await self.store.lines(GUILD, sid)
        self.assertEqual((line.heard, line.text), ("I saw Beleros", "I saw Belleros"))

    async def test_a_line_still_waiting_to_be_saved_changes_where_it_waits(self) -> None:
        await self.consent.grant(GUILD, PLAYER)
        self.bot.suggestions = SuggestionStore(self.db)
        table = self.table()
        self.said(table, PLAYER, "I saw Beleros")
        sid = "s" * 32
        table.transcript_session_id = sid
        started = START * 1000 + 1000
        self.assertTrue(
            await self.bot.apply_line_fix(
                GUILD, table.campaign_id or "", sid, PLAYER, started, "I saw Belleros"
            )
        )
        (waiting,) = table.unsaved._waiting
        self.assertEqual((waiting.heard, waiting.text), ("I saw Beleros", "I saw Belleros"))

    async def test_someone_who_stopped_being_recorded_has_nothing_written(self) -> None:
        table, sid = await self.fix_setup()
        self.bot.consent.has_consent = lambda guild_id, user_id: False  # type: ignore[method-assign]
        done = await self.bot.apply_line_fix(
            GUILD, table.campaign_id or "", sid, PLAYER, START * 1000 + 1000, "I saw Belleros"
        )
        self.assertFalse(done)
        (line,) = await self.store.lines(GUILD, sid)
        self.assertEqual(line.text, "I saw Beleros")

    async def test_a_dms_own_edit_is_noted_with_undo_and_learns_no_rule(self) -> None:
        table, sid = await self.fix_setup()
        memory = MagicMock(add_correction=AsyncMock())
        self.bot.memory = memory
        started = START * 1000 + 1000
        done = await self.bot.apply_line_fix(
            GUILD, table.campaign_id or "", sid, PLAYER, started, "I saw Belleros there", noted=True
        )
        self.assertTrue(done)
        (line,) = await self.store.lines(GUILD, sid)
        self.assertEqual((line.heard, line.text), ("I saw Beleros", "I saw Belleros there"))
        (note,) = table.fix_notes.shown()  # listed with Undo, as a whole-line DM edit
        self.assertEqual((note.fix.how, note.fix.written), ("edit", "I saw Belleros there"))
        # A second edit of the same line waits for the first to be undone.
        self.assertFalse(
            await self.bot.apply_line_fix(
                GUILD, table.campaign_id or "", sid, PLAYER, started, "again", noted=True
            )
        )
        text, allow = await self.bot.undo_fix(GUILD, note.id, DM)
        self.assertIn("Undone", text)
        self.assertIsNone(allow)  # no "Allow again": nothing was learned
        memory.add_correction.assert_not_awaited()  # and no "keep as heard" rule for a whole line
        (line,) = await self.store.lines(GUILD, sid)
        self.assertEqual(line.text, "I saw Beleros")  # as heard again

    async def test_a_dm_edit_is_refused_over_a_line_with_a_name_fix_waiting(self) -> None:
        from dmbot.transcript.cleaner import Fix

        table, sid = await self.fix_setup()
        started = START * 1000 + 1000
        fix = Fix(6, 13, "Beleros", "Belleros", "e" * 32, "sound", sure=False)
        table.fix_notes.add(PLAYER, started, "I saw Beleros", (fix,))
        done = await self.bot.apply_line_fix(
            GUILD, table.campaign_id or "", sid, PLAYER, started, "something else", noted=True
        )
        self.assertFalse(done)  # undoing the older fix would bring its words back over this
        (line,) = await self.store.lines(GUILD, sid)
        self.assertEqual(line.text, "I saw Beleros")

    async def test_a_dm_edit_is_refused_over_a_line_with_an_open_question(self) -> None:
        from dmbot.transcript.questions import Asked

        table, sid = await self.fix_setup()
        started = START * 1000 + 1000
        table.questions.open = Asked("q1", PLAYER, "Beleros", (), 0.0, started_ms=started)
        done = await self.bot.apply_line_fix(
            GUILD, table.campaign_id or "", sid, PLAYER, started, "something else", noted=True
        )
        self.assertFalse(done)  # answering it later would write over the edit
        (line,) = await self.store.lines(GUILD, sid)
        self.assertEqual(line.text, "I saw Beleros")

    async def test_the_summary_counts_what_the_cleaner_did(self) -> None:
        from dmbot.transcript.cleaner import Fix

        table, sid = await self.fix_setup()
        started = START * 1000 + 1000
        store = self.bot.suggestions
        assert store is not None
        made = await store.suggest(
            GUILD, table.campaign_id or "", sid, PLAYER, started, "I saw Belleros"
        )
        assert isinstance(made, Suggestion)
        table.names_fixed, table.names_asked = 3, 2
        table.questions.closed_why.update({"a": "answered", "b": name_questions.ENDED})
        edit = Fix(0, 13, "I saw Beleros", "x", "", "edit", sure=False)
        table.fix_notes.add(PLAYER, started, "I saw Beleros", (edit,))
        self.bot.post_message = AsyncMock(return_value=MagicMock())  # type: ignore[method-assign]
        await self.bot.post_session_summary(table, START + 60, True, 0)
        text = cast(Any, self.bot.post_message).await_args.args[1]
        self.assertIn(
            "✏️ Names: DMbot fixed 3 by itself, asked you about 2 (1 not answered), "
            "you corrected 1, players suggested 1 (1 still waiting).",
            text,
        )

    async def test_a_quiet_session_has_no_names_line(self) -> None:
        table, _ = await self.fix_setup()
        self.bot.post_message = AsyncMock(return_value=MagicMock())  # type: ignore[method-assign]
        await self.bot.post_session_summary(table, START + 60, True, 0)
        self.assertNotIn("Names:", cast(Any, self.bot.post_message).await_args.args[1])

    async def test_the_dm_screens_summary_carries_the_fix_a_line_button(self) -> None:
        table, sid = await self.fix_setup()
        self.bot.post_message = AsyncMock(return_value=MagicMock())  # type: ignore[method-assign]
        await self.bot.post_session_summary(table, START + 60, True, 0)
        _, _, view = cast(Any, self.bot.post_message).await_args.args
        ids = [c.item.custom_id for c in view.children]
        self.assertEqual(ids, [f"dmbot:fixline:{GUILD}:{sid}"])
        self.assertEqual(view.children[0].item.label, "✏️ Fix a line")

    async def waiting_setup(self, count: int = 1) -> tuple[Table, list[int]]:
        """`count` open suggestions on lines of two recorded people, oldest first."""
        other = PLAYER + 1
        await self.consent.grant(GUILD, PLAYER)
        await self.consent.grant(GUILD, other)
        self.bot.suggestions = SuggestionStore(self.db)
        table = self.table()
        for i in range(count):
            self.said(table, PLAYER if i % 2 == 0 else other, f"I saw Beleros {i}", i + 1)
        await self.bot.save_transcript(table)
        sid = table.transcript_session_id
        assert sid is not None
        store = self.bot.suggestions
        ids = []
        for i in range(count):
            made = await store.suggest(
                GUILD,
                table.campaign_id or "",
                sid,
                PLAYER if i % 2 == 0 else other,
                START * 1000 + (i + 1) * 1000,
                f"I saw Belleros {i}",
            )
            assert isinstance(made, Suggestion)
            ids.append(made.id)
        self.bot.post_message = AsyncMock(return_value=MagicMock())  # type: ignore[method-assign]
        return table, ids

    def posted(self) -> list[tuple[str, Any]]:
        return [
            (c.args[1], c.args[2] if len(c.args) > 2 else None)
            for c in cast(Any, self.bot.post_message).await_args_list
        ]

    async def test_a_waiting_suggestion_is_put_on_the_dm_screen_again_at_session_start(
        self,
    ) -> None:
        table, (sid,) = await self.waiting_setup(1)
        await self.bot._waiting_suggestions(table)
        ((text, view),) = self.posted()
        self.assertIn("suggests a fix to their line", text)
        self.assertIn("I saw Belleros 0", text)
        self.assertEqual(
            [c.item.custom_id for c in view.children],
            [f"dmbot:linefix:yes:{sid}", f"dmbot:linefix:no:{sid}"],
        )
        self.assertEqual(cast(Any, self.bot.post_message).await_args_list[0].args[0], SCREEN)

    async def test_nothing_for_an_answered_or_dropped_suggestion(self) -> None:
        table, (first, second) = await self.waiting_setup(2)
        store = self.bot.suggestions
        assert store is not None
        await store.decide(GUILD, first, "declined", DM)
        await store.decide(GUILD, second, "dropped", DM)
        await self.bot._waiting_suggestions(table)
        cast(Any, self.bot.post_message).assert_not_awaited()

    async def test_at_most_three_are_shown_oldest_first_and_the_rest_counted(self) -> None:
        table, ids = await self.waiting_setup(5)
        await self.bot._waiting_suggestions(table)
        shown = self.posted()
        self.assertEqual(len(shown), 4)
        for i, (text, view) in enumerate(shown[:3]):
            self.assertIn(f"I saw Belleros {i}", text)
            self.assertEqual(view.children[0].item.custom_id, f"dmbot:linefix:yes:{ids[i]}")
        self.assertEqual(shown[3], ("…and 2 more that go when this session ends.", None))

    async def test_nothing_after_a_restart_in_the_middle_of_a_session(self) -> None:
        table, _ = await self.waiting_setup(1)
        table.resumed = True
        await self.bot._waiting_suggestions(table)
        cast(Any, self.bot.post_message).assert_not_awaited()

    async def test_nothing_if_the_session_ended_meanwhile(self) -> None:
        table, _ = await self.waiting_setup(1)
        del self.bot.tables[GUILD]
        await self.bot._waiting_suggestions(table)
        cast(Any, self.bot.post_message).assert_not_awaited()

    async def test_nothing_for_a_speaker_who_is_no_longer_recorded(self) -> None:
        table, _ = await self.waiting_setup(1)
        await self.consent.revoke(GUILD, PLAYER)
        await self.bot._waiting_suggestions(table)
        cast(Any, self.bot.post_message).assert_not_awaited()

    async def test_a_person_who_stops_while_the_lines_go_up_is_left_out(self) -> None:
        table, _ = await self.waiting_setup(2)  # one line each from two people

        async def stop_the_other(*_: Any, **__: Any) -> Any:
            await self.consent.revoke(GUILD, PLAYER + 1)
            return MagicMock()

        self.bot.post_message = AsyncMock(side_effect=stop_the_other)  # type: ignore[method-assign]
        await self.bot._waiting_suggestions(table)
        self.assertEqual(cast(Any, self.bot.post_message).await_count, 1)  # only the first went up

    async def test_a_failure_costs_the_lines_not_the_session(self) -> None:
        table, _ = await self.waiting_setup(1)
        self.bot.post_message = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
        await self.bot._waiting_suggestions(table)  # does not raise

    async def test_when_a_session_ends_only_its_own_suggestions_stay(self) -> None:
        table, sid = await self.fix_setup()
        store = self.bot.suggestions
        assert store is not None
        made = await store.suggest(
            GUILD, table.campaign_id or "", sid, PLAYER, START * 1000 + 1000, "I saw Belleros"
        )
        assert isinstance(made, Suggestion)
        await self.bot._drop_old_suggestions(table, sid)  # its own session ended: it stays
        kept = await store.get(GUILD, made.id)
        assert kept is not None
        self.assertEqual(kept.state, "open")
        later = await self.store.open_session(GUILD, table.campaign_id or "", START + 86400)
        await self.bot._drop_old_suggestions(table, later)  # the next one ended: it goes
        dropped = await store.get(GUILD, made.id)
        assert dropped is not None
        self.assertEqual(dropped.state, "dropped")
