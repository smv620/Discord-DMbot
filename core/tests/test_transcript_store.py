"""Stored session transcripts (#41, #125) against a real Postgres, and the
`/transcript` download flow."""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.audio.segmenter import Segmenter, Utterance
from dmbot.bot import DMBot, Table
from dmbot.campaigns import CampaignStore
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.dm_screen import messages as screen_messages
from dmbot.sessions import SessionStore
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
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
            [f"dmbot:transcript:{GUILD}:{sid}:{c}" for c in ("cleaned", "heard", "both")],
        )
        session = await self.store.session(GUILD, sid or "")
        assert session is not None and session.ended_at is not None

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
