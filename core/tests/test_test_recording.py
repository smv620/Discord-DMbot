"""Test recordings (#1019): the files, the library, and the rules around saving. No database,
no Discord, no network. Ids here are made up."""

import asyncio
import json
import os
import re
import stat
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

from dmbot import consent_dm as c
from dmbot.audio.segmenter import Segmenter, Utterance
from dmbot.bot import DMBot, Table
from dmbot.config import ConfigError, Settings, load_settings, parse_server_ids
from dmbot.devtools import test_library as cli
from dmbot.test_recording import files, flac, library
from dmbot.test_recording.session import TestSession
from dmbot.test_recording.store import TestVoiceStore

GUILD = 987654321098765432  # a made-up server and accounts, 18 digits like real ones
DM, ALEX, SAM = 123456789012345678, 223456789012345678, 323456789012345678
BIG_NUMBER = re.compile(r"\d{17,20}")
STARTED = 1_700_000_000


def pcm(seconds: float = 1.0, level: int = 1000) -> bytes:
    n = int(16000 * seconds)
    return b"".join((level + i % 50).to_bytes(2, "little", signed=True) for i in range(n))


class Files(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "rec"
        self.addCleanup(self._tmp.cleanup)

    def test_the_folder_and_the_secret_are_owner_only(self) -> None:
        key = files.load_key(self.root)
        self.assertEqual(len(key), 32)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.root / files.KEY_FILE).stat().st_mode), 0o600)
        self.assertEqual(files.load_key(self.root), key)  # the same next time

    def test_a_voice_code_stands_for_one_person_in_one_server(self) -> None:
        key = files.load_key(self.root)
        code = files.voice_code(key, GUILD, ALEX)
        self.assertRegex(code, r"^v-[0-9a-f]{12}$")
        self.assertEqual(code, files.voice_code(key, GUILD, ALEX))
        self.assertNotEqual(code, files.voice_code(key, GUILD, SAM))
        self.assertNotEqual(code, files.voice_code(key, GUILD + 1, ALEX))
        self.assertNotEqual(code, files.voice_code(b"another secret" * 3, GUILD, ALEX))
        self.assertNotIn(str(ALEX), code)

    def test_only_a_commit_id_is_written_down(self) -> None:
        self.assertEqual(files.short_commit("AB12CD34EF56"), "ab12cd3")
        for bad in (None, "", "not a commit", "ab", "ghijkl", "ab12; rm -rf"):
            self.assertIsNone(files.short_commit(bad))

    def test_a_manifest_is_written_whole_or_not_at_all(self) -> None:
        files.ensure_root(self.root)
        target = self.root / "x.json"
        files.write_json(target, {"a": 1})
        files.write_json(target, {"a": 2})
        self.assertEqual(files.read_json(target), {"a": 2})
        self.assertEqual([p.name for p in self.root.iterdir()], ["x.json"])  # no temp left


class Flac(unittest.TestCase):
    def test_it_round_trips_exactly(self) -> None:
        for seconds in (0.02, 0.5, 2.37):
            audio = pcm(seconds)
            self.assertEqual(flac.decode(flac.encode(audio)), audio)

    def test_silence_and_odd_lengths_round_trip(self) -> None:
        quiet = bytes(2 * 1234)
        self.assertEqual(flac.decode(flac.encode(quiet)), quiet)

    def test_nothing_or_half_a_sample_is_refused(self) -> None:
        for bad in (b"", b"\x01"):
            with self.assertRaises(ValueError):
                flac.encode(bad)


class Session(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "rec"
        self.addCleanup(self._tmp.cleanup)
        self.key = files.load_key(self.root)
        self.session = TestSession(
            self.root, self.key, GUILD, STARTED, settings={"transcriber": "deepgram nova-3"}
        )

    def manifest(self) -> dict[str, Any]:
        data = files.read_json(self.session.folder / files.MANIFEST)
        assert data is not None
        return data

    async def say(self, user: int, start_s: float, audio: bytes, text: str = "Hello there") -> bool:
        return await self.session.add_utterance(
            user,
            is_dm=user == DM,
            start_ms=STARTED * 1000 + int(start_s * 1000),
            end_ms=STARTED * 1000 + int(start_s * 1000) + 1000,
            pcm=audio,
            text=text,
            allowed=lambda: True,
        )

    async def test_the_folder_is_named_by_the_start_and_is_owner_only(self) -> None:
        when = datetime.fromtimestamp(STARTED, UTC)
        self.assertRegex(self.session.folder.name, rf"^{when:%Y-%m-%d-%H%M}-[0-9a-f]{{6}}$")
        self.assertEqual(stat.S_IMODE(self.session.folder.stat().st_mode), 0o700)

    async def test_speakers_are_made_up_numbers_the_dm_first(self) -> None:
        await self.say(ALEX, 1.0, pcm())  # a player speaks first
        await self.say(DM, 3.0, pcm())
        await self.say(SAM, 5.0, pcm())
        speakers = self.manifest()["speakers"]
        self.assertEqual(
            [(s["speaker"], s["role"]) for s in speakers],
            [(1002, "Player 1"), (1001, "DM"), (1003, "Player 2")],
        )

    async def test_times_are_on_one_session_clock(self) -> None:
        await self.say(DM, 12.5, pcm())
        (item,) = self.manifest()["utterances"]
        self.assertEqual((item["start_ms"], item["end_ms"]), (12_500, 13_500))
        self.assertRegex(item["file"], r"^audio/0001-1001\.flac$")

    async def test_the_audio_is_saved_and_reads_back_exactly(self) -> None:
        audio = pcm(1.5, 700)
        await self.say(DM, 0.0, audio)
        (item,) = self.manifest()["utterances"]
        saved = (self.session.folder / item["file"]).read_bytes()
        self.assertEqual(flac.decode(saved), audio)
        self.assertEqual(stat.S_IMODE((self.session.folder / item["file"]).stat().st_mode), 0o600)

    async def test_nothing_in_the_manifest_names_anyone(self) -> None:
        await self.say(DM, 0.0, pcm(), "I attack the goblin")
        await self.say(ALEX, 2.0, pcm(), "I cast shield")
        self.session.stopped(ALEX, STARTED * 1000 + 9000)
        self.session.shown("sidebar question", STARTED * 1000 + 4000, "find flanking")
        self.session.finish({"complete": True})
        text = (self.session.folder / files.MANIFEST).read_text(encoding="utf-8")
        self.assertIsNone(BIG_NUMBER.search(text), "a Discord-sized number is in the manifest")
        for who in (DM, ALEX, SAM, GUILD):
            self.assertNotIn(str(who), text)
        # and not in any file name either
        names = " ".join(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
        for who in (DM, ALEX, SAM, GUILD):
            self.assertNotIn(str(who), names)

    async def test_it_records_what_dmbot_made_of_the_session(self) -> None:
        await self.say(DM, 1.0, pcm(), "The goblin ducks")
        self.session.shown("sidebar answer", STARTED * 1000 + 3000, "No. A point you choose.")
        produced = self.manifest()["produced"]
        self.assertEqual(
            produced["transcript"], [{"at_ms": 1000, "speaker": 1001, "text": "The goblin ducks"}]
        )
        self.assertEqual(produced["shown"][0]["kind"], "sidebar answer")

    async def test_nothing_is_saved_without_the_yes(self) -> None:
        saved = await self.session.add_utterance(
            DM, is_dm=True, start_ms=0, end_ms=1000, pcm=pcm(), text="x", allowed=lambda: False
        )
        self.assertFalse(saved)
        self.assertEqual(list((self.session.folder / files.AUDIO).iterdir()), [])
        self.assertEqual(self.manifest()["utterances"], [])

    async def test_a_stop_pressed_while_it_is_saved_leaves_nothing(self) -> None:
        for flips_at in (2, 3):  # after the encode, and after the file was written
            calls = {"n": 0}

            def allowed(limit: int = flips_at, counter: dict[str, int] = calls) -> bool:
                counter["n"] += 1
                return counter["n"] < limit

            saved = await self.session.add_utterance(
                DM, is_dm=True, start_ms=0, end_ms=1000, pcm=pcm(), text="x", allowed=allowed
            )
            self.assertFalse(saved)
            self.assertEqual(list((self.session.folder / files.AUDIO).iterdir()), [])
        self.assertEqual(self.manifest()["utterances"], [])

    async def test_forgetting_a_person_deletes_their_files_and_lines_only(self) -> None:
        await self.say(DM, 0.0, pcm(), "dm line")
        await self.say(ALEX, 2.0, pcm(), "alex line")
        self.session.stopped(ALEX, STARTED * 1000 + 3000)
        self.session.forget(ALEX)
        manifest = self.manifest()
        self.assertEqual([u["speaker"] for u in manifest["utterances"]], [1001])
        self.assertEqual(len(list((self.session.folder / files.AUDIO).iterdir())), 1)
        lines = manifest["produced"]["transcript"]
        self.assertEqual([x["text"] for x in lines], ["dm line"])
        self.assertEqual(manifest["consent_events"][0]["event"], "stopped saving")
        self.assertEqual(manifest["removed_speakers"], [{"speaker": 1002, "role": "Player 1"}])


class Library(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "rec"
        self.addCleanup(self._tmp.cleanup)
        self.key = files.load_key(self.root)

    async def make(
        self, short: str, people: tuple[int, ...] = (DM, ALEX), *, end: bool = True
    ) -> TestSession:
        session = TestSession(self.root, self.key, GUILD, STARTED, settings={}, short_id=short)
        for i, user in enumerate(people):
            await session.add_utterance(
                user,
                is_dm=user == DM,
                start_ms=STARTED * 1000 + i * 2000,
                end_ms=STARTED * 1000 + i * 2000 + 1000,
                pcm=pcm(),
                text=f"line {i}",
                allowed=lambda: True,
            )
        if end:
            session.finish({"complete": True})
        return session

    async def test_the_listing_shows_sizes_and_names_but_no_ids(self) -> None:
        await self.make("aaaaaa")
        (info,) = library.listing(self.root)
        self.assertEqual((info.speakers, info.utterances, info.complete), (2, 2, True))
        self.assertGreater(info.size, 0)
        line = library.describe(info)
        self.assertIsNone(BIG_NUMBER.search(line))
        self.assertNotIn(str(DM), line)

    async def test_keeping_marks_a_finished_session_with_a_name_and_a_note(self) -> None:
        s = await self.make("aaaaaa")
        library.keep(self.root, s.folder.name, "two-speaker-live", "all lines heard")
        (info,) = library.listing(self.root)
        self.assertEqual((info.kept, info.note), ("two-speaker-live", "all lines heard"))
        self.assertIn("two-speaker-live", library.describe(info))

    async def test_an_unfinished_session_cant_be_kept(self) -> None:
        s = await self.make("aaaaaa", end=False)
        with self.assertRaises(library.LibraryError):
            library.keep(self.root, s.folder.name, "case", "")

    async def test_a_name_is_used_once_and_must_be_sensible(self) -> None:
        a, b = await self.make("aaaaaa"), await self.make("bbbbbb")
        library.keep(self.root, a.folder.name, "case", "")
        with self.assertRaises(library.LibraryError):
            library.keep(self.root, b.folder.name, "case", "")
        for bad in ("", "  ", "a/b", "..\\x"):
            with self.assertRaises(library.LibraryError):
                library.keep(self.root, b.folder.name, bad, "")
        with self.assertRaises(library.LibraryError):
            library.keep(self.root, "../outside", "x", "")
        with self.assertRaises(library.LibraryError):
            library.keep(self.root, "no-such-folder", "x", "")

    async def test_stop_saving_deletes_a_persons_files_from_every_session(self) -> None:
        first, second = await self.make("aaaaaa"), await self.make("bbbbbb", (ALEX, SAM))
        touched = library.delete_person(self.root, self.key, GUILD, ALEX)
        self.assertEqual(touched, 2)
        for session, left in ((first, 1), (second, 1)):
            manifest = files.read_json(session.folder / files.MANIFEST)
            assert manifest is not None
            self.assertEqual(len(manifest["utterances"]), left)
            self.assertEqual(len(list((session.folder / files.AUDIO).iterdir())), left)
            self.assertEqual(manifest["removed_speakers"][0]["role"], "Player 1")

    async def test_a_kept_case_that_lost_a_speaker_is_marked_incomplete(self) -> None:
        s = await self.make("aaaaaa")
        library.keep(self.root, s.folder.name, "case", "")
        library.delete_person(self.root, self.key, GUILD, ALEX)
        (info,) = library.listing(self.root)
        self.assertTrue(info.incomplete)
        self.assertFalse(info.complete)
        self.assertIn("INCOMPLETE", library.describe(info))

    async def test_an_unkept_session_with_nobody_left_is_deleted(self) -> None:
        await self.make("aaaaaa", (ALEX,))
        library.delete_person(self.root, self.key, GUILD, ALEX)
        self.assertEqual(library.listing(self.root), [])

    async def test_somebody_elses_files_and_another_servers_are_left_alone(self) -> None:
        s = await self.make("aaaaaa", (DM, SAM))
        self.assertEqual(library.delete_person(self.root, self.key, GUILD, ALEX), 0)
        self.assertEqual(
            library.delete_person(self.root, self.key, GUILD + 1, SAM), 0
        )  # other server
        manifest = files.read_json(s.folder / files.MANIFEST)
        assert manifest is not None
        self.assertEqual(len(manifest["utterances"]), 2)

    async def test_unkept_sessions_go_after_seven_days_and_kept_ones_stay(self) -> None:
        old, kept, fresh = (
            await self.make("aaaaaa"),
            await self.make("bbbbbb"),
            await self.make("cccccc"),
        )
        library.keep(self.root, kept.folder.name, "case", "")
        then = time.time() - 8 * 86400
        for folder in (old.folder, kept.folder):
            for path in [folder, *folder.rglob("*")]:
                os.utime(path, (then, then))
        self.assertEqual(library.cleanup(self.root, time.time()), 1)
        names = {i.folder for i in library.listing(self.root)}
        self.assertEqual(names, {kept.folder.name, fresh.folder.name})
        self.assertEqual(library.cleanup(self.root, time.time()), 0)

    async def test_the_command_lists_and_keeps(self) -> None:
        s = await self.make("aaaaaa")
        self.assertEqual(cli.main(["--dir", str(self.root), "list"]), 0)
        self.assertEqual(
            cli.main(
                ["--dir", str(self.root), "keep", s.folder.name, "--name", "c", "--note", "n"]
            ),
            0,
        )
        self.assertEqual(cli.main(["--dir", str(self.root), "keep", "nope", "--name", "c"]), 1)


class Settings_(unittest.TestCase):
    BASE: ClassVar[dict[str, str]] = {
        "DISCORD_TOKEN": "t",
        "EARS_SHARED_SECRET": "s",
        "DATABASE_URL": "postgresql://x",
    }

    def test_nothing_is_saved_unless_a_server_is_listed(self) -> None:
        settings = load_settings(self.BASE)
        self.assertEqual(settings.test_recording_guilds, frozenset())
        self.assertEqual(settings.test_recordings_dir, Path("/var/lib/dmbot/test-recordings"))

    def test_servers_and_folder_come_from_the_environment(self) -> None:
        settings = load_settings(
            {
                **self.BASE,
                "DMBOT_TEST_RECORDING_GUILDS": f"{GUILD}, 12345",
                "DMBOT_TEST_RECORDINGS_DIR": "/tmp/x",
            }
        )
        self.assertEqual(settings.test_recording_guilds, frozenset({GUILD, 12345}))
        self.assertEqual(settings.test_recordings_dir, Path("/tmp/x"))

    def test_a_typo_stops_start_up_without_echoing_the_value(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            load_settings({**self.BASE, "DMBOT_TEST_RECORDING_GUILDS": "my-server"})
        self.assertNotIn("my-server", str(caught.exception))
        self.assertEqual(parse_server_ids(""), frozenset())
        with self.assertRaises(ValueError):
            parse_server_ids("0")


class FakeVoice(TestVoiceStore):
    """The second yes in memory (the real one needs the database, tested in CI)."""

    def __init__(self) -> None:
        super().__init__(MagicMock())
        self.revoked: list[tuple[int, int]] = []

    async def load(self, guild_id: int) -> None:
        self._yes.setdefault(guild_id, set())

    async def grant(self, guild_id: int, user_id: int) -> int:
        self._yes.setdefault(guild_id, set()).add(user_id)
        return 1

    async def revoke(self, guild_id: int, user_id: int) -> None:
        self.stop_now(guild_id, user_id)
        self.revoked.append((guild_id, user_id))


class InTheBot(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "rec"

    def bot(self, guilds: frozenset[int]) -> DMBot:
        self.voice = FakeVoice()
        bot = DMBot(
            Settings(
                discord_token="t",
                ears_secret="s",
                test_recording_guilds=guilds,
                test_recordings_dir=self.root,
            ),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            test_voice=self.voice,
        )
        self.agreed: set[int] = {DM, ALEX}
        bot.consent.has_consent = lambda g, u: u in self.agreed  # type: ignore[method-assign,assignment]
        self.post = AsyncMock(return_value=True)
        bot.post = self.post  # type: ignore[method-assign]
        return bot

    def table(self) -> Table:
        return Table(
            guild_id=GUILD,
            voice_channel_id=2,
            screen_channel_id=3,
            dm_user_id=DM,
            segmenter=Segmenter(GUILD),
            campaign_id="c1",
            campaign_name="Test",
            dm_user_ids=frozenset({DM}),
            started_at=STARTED,
        )

    def utterance(self, user: int, at_s: float = 1.0) -> Utterance:
        ms = STARTED * 1000 + int(at_s * 1000)
        return Utterance(GUILD, user, ms, ms + 980, pcm(), 1)

    async def test_nothing_is_saved_in_a_server_that_is_not_listed(self) -> None:
        bot = self.bot(frozenset({GUILD + 1}))
        table = self.table()
        await bot._start_test_session(table)
        self.assertIsNone(table.test_session)
        self.assertFalse(self.root.exists())
        self.post.assert_not_awaited()
        self.assertFalse(bot.test_voice_listed(GUILD))

    async def test_nothing_is_saved_when_no_server_is_listed_at_all(self) -> None:
        bot = self.bot(frozenset())
        table = self.table()
        await bot._start_test_session(table)
        self.assertIsNone(table.test_session)
        self.assertFalse(self.root.exists())

    async def test_a_listed_server_says_so_once_in_the_dm_screen(self) -> None:
        bot = self.bot(frozenset({GUILD}))
        table = self.table()
        await bot._start_test_session(table)
        await bot._start_test_session(table)  # not twice
        assert table.test_session is not None
        self.post.assert_awaited_once()
        assert self.post.await_args is not None
        self.assertIn("This is a test session", self.post.await_args.args[1])

    async def test_only_people_who_said_both_yeses_are_saved(self) -> None:
        bot = self.bot(frozenset({GUILD}))
        table = self.table()
        await bot._start_test_session(table)
        assert table.test_session is not None and bot.test_voice is not None
        await bot.grant_test_voice(GUILD, DM)  # the DM said yes to both
        # Alex is recorded but never pressed Save my voice for tests
        await bot._save_test_audio(table, self.utterance(DM), "dm line")
        await bot._save_test_audio(table, self.utterance(ALEX, 3.0), "alex line")
        manifest = files.read_json(table.test_session.folder / files.MANIFEST)
        assert manifest is not None
        self.assertEqual([u["speaker"] for u in manifest["utterances"]], [1001])
        # and a yes to saving without the recording yes saves nothing either
        await bot.grant_test_voice(GUILD, SAM)
        await bot._save_test_audio(table, self.utterance(SAM, 5.0), "sam line")
        manifest = files.read_json(table.test_session.folder / files.MANIFEST)
        assert manifest is not None
        self.assertEqual(len(manifest["utterances"]), 1)

    async def test_stop_saving_stops_at_once_and_deletes_every_file(self) -> None:
        bot = self.bot(frozenset({GUILD}))
        table = self.table()
        bot.tables[GUILD] = table
        await bot._start_test_session(table)
        assert table.test_session is not None and bot.test_voice is not None
        await bot.grant_test_voice(GUILD, ALEX)
        await bot._save_test_audio(table, self.utterance(ALEX), "hello")
        folder = table.test_session.folder
        self.assertEqual(len(list((folder / files.AUDIO).iterdir())), 1)
        await bot.stop_saving_voice(GUILD, ALEX)
        self.assertFalse(bot.test_voice_saving(GUILD, ALEX))
        self.assertEqual(list((folder / files.AUDIO).iterdir()), [])
        await bot._save_test_audio(table, self.utterance(ALEX, 4.0), "more")  # saves nothing now
        self.assertEqual(list((folder / files.AUDIO).iterdir()), [])
        self.assertIn((GUILD, ALEX), self.voice.revoked)

    async def test_stopping_the_recording_stops_the_saving_too(self) -> None:
        bot = self.bot(frozenset({GUILD}))
        table = self.table()
        bot.tables[GUILD] = table
        await bot._start_test_session(table)
        assert table.test_session is not None
        await bot.grant_test_voice(GUILD, ALEX)
        await bot._save_test_audio(table, self.utterance(ALEX), "hello")
        bot.stop_recording(GUILD, ALEX)  # Stop recording me, in any form
        self.assertFalse(bot.test_voice_saving(GUILD, ALEX))
        await asyncio.sleep(0.05)  # the deletion runs in the background
        self.assertEqual(list((table.test_session.folder / files.AUDIO).iterdir()), [])

    async def test_a_finished_session_is_marked_finished(self) -> None:
        bot = self.bot(frozenset({GUILD}))
        table = self.table()
        await bot._start_test_session(table)
        assert table.test_session is not None
        bot._finish_test_session(table, True)
        manifest = files.read_json(table.test_session.folder / files.MANIFEST)
        assert manifest is not None
        self.assertTrue(manifest["ended"])
        self.assertEqual(json.dumps(manifest["settings"]).count("api."), 0)  # no company host

    async def test_what_the_sidebar_says_goes_in_the_session(self) -> None:
        bot = self.bot(frozenset({GUILD}))
        table = self.table()
        bot.tables[GUILD] = table
        await bot._start_test_session(table)
        assert table.test_session is not None
        from dmbot.transcript.models import SIDEBAR_QUESTION, Line

        bot.sidebar_save(
            table,
            Line(
                STARTED * 1000 + 2000,
                DM,
                "find flanking",
                "find flanking",
                sidebar=SIDEBAR_QUESTION,
            ),
        )
        table.test_session.flush()
        manifest = files.read_json(table.test_session.folder / files.MANIFEST)
        assert manifest is not None
        self.assertEqual(manifest["produced"]["shown"][0]["kind"], "sidebar question")


class FakeActions:
    """What the consent buttons ask of the bot, in memory."""

    outside_engine = None
    company = None
    sheets = None

    def __init__(self, *, listed: bool = True, recorded: bool = True) -> None:
        self.listed, self._recorded = listed, recorded
        self.saving: set[int] = set()
        self.stopped: list[int] = []
        self.guild = SimpleNamespace(
            id=GUILD, name="Test", fetch_member=AsyncMock(return_value=object())
        )

    def get_guild(self, guild_id: int, /) -> object:
        return self.guild

    def test_voice_listed(self, guild_id: int) -> bool:
        return self.listed

    def test_voice_saving(self, guild_id: int, user_id: int) -> bool:
        return user_id in self.saving

    async def recorded(self, guild_id: int, user_id: int) -> bool:
        return self._recorded

    async def grant_test_voice(self, guild_id: int, user_id: int) -> None:
        self.saving.add(user_id)

    async def stop_saving_voice(self, guild_id: int, user_id: int) -> None:
        self.stopped.append(user_id)
        self.saving.discard(user_id)


def press(actions: FakeActions, user: int = ALEX) -> Any:
    return SimpleNamespace(
        client=actions,
        user=SimpleNamespace(id=user),
        guild_id=None,
        response=SimpleNamespace(send_message=AsyncMock()),
    )


def said(interaction: Any) -> str:
    return str(interaction.response.send_message.await_args.args[0])


class TheSecondQuestion(unittest.IsolatedAsyncioTestCase):
    """The consent message in a test server asks for the second yes, apart from the first."""

    def test_the_request_is_the_same_everywhere_and_a_test_server_adds_the_second_question(
        self,
    ) -> None:
        plain = c.request_text("Server", voice=None, dm=None, cloud=False)
        test = c.request_text("Server", voice=None, dm=None, cloud=False, test_voice=True)
        self.assertNotIn("Save my voice", plain)
        self.assertTrue(test.startswith(plain))  # the terms people agree to don't change
        for needed in (
            "This is a test server",
            f"**{c.TEST_VOICE_LABEL}**",
            "on DMbot's own computer",
            "only to re-check that DMbot still works",
            "never published or shared",
            f"**{c.STOP_SAVING_LABEL}**",
            "deleted",
            "nothing of your voice is kept",
        ):
            self.assertIn(needed, test)

    def test_the_button_appears_only_in_a_test_server(self) -> None:
        def ids(view: Any) -> list[str]:
            return [str(getattr(i, "custom_id", "")) for i in view.children]

        self.assertFalse(any("testvoice" in i for i in ids(c.request_view(GUILD))))
        self.assertIn(f"dmbot:testvoice:yes:{GUILD}", ids(c.request_view(GUILD, test_voice=True)))

    def test_the_menu_offers_save_or_stop_by_what_is_so(self) -> None:
        def ids(state: str | None, recording: bool = True) -> list[str]:
            view = c.options_view(GUILD, None, recording=recording, sheets=False, test_voice=state)
            return [getattr(i, "custom_id", "") for i in view.children]

        self.assertNotIn(f"dmbot:testvoice:yes:{GUILD}", ids(None))
        self.assertIn(f"dmbot:testvoice:yes:{GUILD}", ids("ask"))
        self.assertIn(f"dmbot:testvoice:stop:{GUILD}", ids("saving"))
        self.assertNotIn(f"dmbot:testvoice:yes:{GUILD}", ids("saving"))
        self.assertNotIn(
            f"dmbot:testvoice:yes:{GUILD}", ids("ask", recording=False)
        )  # not recorded

    def test_the_labels_fit(self) -> None:
        for label in (c.TEST_VOICE_LABEL, c.STOP_SAVING_LABEL):
            self.assertLessEqual(len(label), 25)

    async def test_saving_asks_nothing_of_someone_who_is_not_recorded(self) -> None:
        actions = FakeActions(recorded=False)
        interaction = press(actions)
        await c.SaveVoiceButton(GUILD).callback(interaction)
        self.assertEqual(actions.saving, set())
        self.assertIn("First agree to be recorded", said(interaction))

    async def test_saving_is_refused_outside_a_test_server(self) -> None:
        actions = FakeActions(listed=False)
        await c.SaveVoiceButton(GUILD).callback(press(actions))
        self.assertEqual(actions.saving, set())

    async def test_the_second_yes_is_given_and_said_back(self) -> None:
        actions = FakeActions()
        interaction = press(actions)
        await c.SaveVoiceButton(GUILD).callback(interaction)
        self.assertEqual(actions.saving, {ALEX})
        self.assertIn(c.STOP_SAVING_LABEL, said(interaction))

    async def test_stop_saving_stops_and_says_so(self) -> None:
        actions = FakeActions()
        actions.saving.add(ALEX)
        interaction = press(actions)
        await c.StopSavingButton(GUILD).callback(interaction)
        self.assertEqual((actions.stopped, actions.saving), ([ALEX], set()))
        self.assertIn("stopped saving your voice", said(interaction))

    def test_both_buttons_come_back_after_a_restart(self) -> None:
        self.assertIn(c.SaveVoiceButton, c.CONSENT_BUTTONS)
        self.assertIn(c.StopSavingButton, c.CONSENT_BUTTONS)
        for cls, custom in (
            (c.SaveVoiceButton, f"dmbot:testvoice:yes:{GUILD}"),
            (c.StopSavingButton, f"dmbot:testvoice:stop:{GUILD}"),
        ):
            self.assertIsNotNone(cls.__discord_ui_compiled_template__.fullmatch(custom))


if __name__ == "__main__":
    unittest.main()
