"""The worker process (#3844): it needs no Discord token, the bot and the worker never both run
the recap audio job, and a headless bot works the servers the database lists. No database, no
Discord."""

from __future__ import annotations

import asyncio
import contextlib
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from dmbot.bot import DMBot
from dmbot.config import ConfigError, load_settings
from dmbot.recaps.discord import RecapFeatures
from dmbot.worker import __main__ as worker

F: Any = RecapFeatures  # (stand-in bots stand in for the class: untyped on purpose)
BASE = {"DISCORD_TOKEN": "t", "EARS_SHARED_SECRET": "s", "DATABASE_URL": "postgresql://x/y"}
NO_TOKEN = {k: v for k, v in BASE.items() if k != "DISCORD_TOKEN"}


class TheSettings(unittest.TestCase):
    def test_the_worker_needs_no_discord_token_and_the_bot_still_does(self) -> None:
        self.assertEqual(load_settings(NO_TOKEN, worker=True).discord_token, "")
        with self.assertRaisesRegex(ConfigError, "DISCORD_TOKEN"):
            load_settings(NO_TOKEN)

    def test_the_worker_still_needs_the_database(self) -> None:
        with self.assertRaisesRegex(ConfigError, "DATABASE_URL"):
            load_settings({"EARS_SHARED_SECRET": "s"}, worker=True)


class TheLoops(unittest.IsolatedAsyncioTestCase):
    def test_the_worker_runs_the_audio_job_only_where_the_audio_worker_exists(self) -> None:
        async def loop(**kw: Any) -> None:
            return None

        bot = SimpleNamespace(
            audio_worker=object(),
            recap_worker=object(),
            import_job=object(),
            _recap_audio_loop=loop,
            _recap_jobs_loop=lambda: loop(),
            _import_loop=loop,
            _card_loop=lambda: loop(),
            settings=SimpleNamespace(card_summary_on=False),
        )
        got = worker.loops(bot)
        self.assertEqual(
            [name for name, _ in got], ["recap-text", "import-clean", "import-scan", "recap-audio"]
        )
        for _, coro in got:
            coro.close()
        bot.audio_worker = bot.recap_worker = bot.import_job = None
        self.assertEqual(worker.loops(bot), [])

    def test_the_worker_runs_the_card_round_only_when_it_is_on(self) -> None:
        async def loop(**kw: Any) -> None:
            return None

        bot = SimpleNamespace(
            audio_worker=None,
            recap_worker=None,
            import_job=None,
            _card_loop=lambda: loop(),
            settings=SimpleNamespace(card_summary_on=True),
        )
        got = worker.loops(bot)
        self.assertEqual([name for name, _ in got], ["card-sentences"])
        for _, coro in got:
            coro.close()

    def stub(self, *, worker_on: bool) -> Any:
        seen: list[dict[str, bool]] = []

        async def audio_loop(**kw: bool) -> None:
            seen.append(kw)

        def watched(coro: Any, name: str) -> Any:
            return (name, coro)

        class Bot:
            """Every part the bot may lack is None, as on a bot started without it."""

            def __getattr__(self, name: str) -> None:
                return None

        bot: Any = Bot()
        bot.seen = seen
        bot.settings = load_settings({**BASE, "DMBOT_WORKER": "1" if worker_on else "0"})
        bot.audio_worker = object()
        bot._recap_audio_loop = audio_loop
        bot._watched = watched
        return bot

    async def test_the_bot_runs_the_audio_job_only_with_the_worker_off(self) -> None:
        for on in (False, True):
            stub = self.stub(worker_on=on)
            tasks = F.recap_tasks(stub)
            named = {t[0]: t[1] for t in tasks if isinstance(t, tuple)}
            await named["recap-audio"]
            self.assertEqual(stub.seen, [{"job": not on}])


class TheServers(unittest.IsolatedAsyncioTestCase):
    async def test_a_headless_bot_works_the_servers_the_database_lists(self) -> None:
        async def listed() -> list[int]:
            return [3, 5]

        stub = SimpleNamespace(
            headless=True,
            campaigns=SimpleNamespace(guilds_with_campaigns=listed),
            guilds=[SimpleNamespace(id=9)],
        )
        self.assertEqual(await F.work_guild_ids(stub), [3, 5])
        stub.headless = False
        self.assertEqual(await F.work_guild_ids(stub), [9])


class TheAudioLoop(unittest.IsolatedAsyncioTestCase):
    async def one_round(self, job: bool, notices: bool) -> list[str]:
        calls: list[str] = []

        async def run_audio() -> None:
            calls.append("job")

        async def refresh() -> None:
            calls.append("refresh")

        async def say() -> None:
            calls.append("notices")

        sleeps: list[float] = []

        async def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) > 1:  # (the first sleep is the start-up wait; the next ends the round)
                raise asyncio.CancelledError

        stub = SimpleNamespace(
            run_recap_audio=run_audio, refresh_audio_waits=refresh, run_audio_notices=say
        )
        with (
            patch("dmbot.recaps.discord.asyncio.sleep", sleep),
            self.assertRaises(asyncio.CancelledError),
        ):
            await F._recap_audio_loop(stub, job=job, notices=notices)
        return calls

    async def test_job_and_notices_are_separate_halves_of_one_loop(self) -> None:
        self.assertEqual(await self.one_round(True, False), ["job"])
        self.assertEqual(await self.one_round(False, True), ["refresh", "notices"])
        self.assertEqual(await self.one_round(True, True), ["job", "refresh", "notices"])


CID = "0123456789abcdef0123456789abcdef"  # (a campaign id: 32 hex, as a button needs)


class Rows:
    """An outbox stand-in that remembers what was written."""

    def __init__(self) -> None:
        self.rows: list[tuple[int, str, str, dict[str, Any]]] = []

    async def add_now(self, gid: int, cid: str, kind: str, payload: Any) -> int:
        from dmbot.outbox import clean_payload

        self.rows.append((gid, cid, kind, clean_payload(payload)))  # (the real ids-only check)
        return len(self.rows)


class Silent:
    """A bot part that may be missing (None), as on a bot started without it."""

    def __getattr__(self, name: str) -> None:
        return None


def recap_bot(*, worker_on: bool, email: bool = False) -> Any:
    posts: list[str] = []

    class Bot(Silent):
        pass

    bot: Any = Bot()

    async def post(channel: int, text: str, view: Any = None, **kw: Any) -> bool:
        posts.append(text)
        return True

    async def get_campaign(gid: int, cid: str) -> Any:
        return SimpleNamespace(
            recaps_email=email, dm_bot_channel_id=7, dm_accounts=(), guild_id=gid, id=cid
        )

    bot.posts = posts
    bot.rows = Rows()
    bot.outbox = bot.rows
    bot.post = post
    bot.campaigns = SimpleNamespace(get=get_campaign)
    bot._upkeep_channel = lambda c: (9, False)
    bot.settings = SimpleNamespace(worker_on=worker_on, site_url="")
    bot._queued = lambda *a: F._queued(bot, *a)
    bot._say_failed = lambda *a: F._say_failed(bot, *a)
    bot._post_recap_ready = lambda *a: F._post_recap_ready(bot, *a)
    bot._say_unchecked = lambda *a: F._say_unchecked(bot, *a)
    return bot


class TheRecapTextJob(unittest.IsolatedAsyncioTestCase):
    """With the worker on a recap job says nothing in Discord itself: each line is an outbox row of
    ids and counts (#4903), and the bot's handler says the same words from it."""

    JOB = SimpleNamespace(
        state="failed", session_at=100, keeps_previous=True, to_confirm=[], title="The Silver Lock"
    )

    async def test_a_failed_recap_is_a_row_with_the_worker_on_and_a_post_with_it_off(self) -> None:
        on = recap_bot(worker_on=True)
        await F.recap_told(on, 1, CID, self.JOB)
        self.assertEqual(on.posts, [])
        self.assertEqual(
            on.rows.rows, [(1, CID, "recap_failed", {"session_at": 100, "keeps": True})]
        )
        off = recap_bot(worker_on=False)
        await F.recap_told(off, 1, CID, self.JOB)
        self.assertEqual(off.rows.rows, [])
        self.assertEqual(len(off.posts), 1)
        self.assertIn("couldn't write", off.posts[0])

    async def test_the_handler_says_the_same_words_the_bot_said_itself(self) -> None:
        said = recap_bot(worker_on=False)
        await F.recap_told(said, 1, CID, self.JOB)
        handled = recap_bot(worker_on=True)
        handled._say_failed = lambda *a: F._say_failed(handled, *a)
        await F.deliver_recap_failed(handled, 1, CID, {"session_at": 100, "keeps": True})
        self.assertEqual(handled.posts, said.posts)

    async def test_a_ready_recap_is_a_row_with_the_worker_on(self) -> None:
        bot = recap_bot(worker_on=True)
        ready = SimpleNamespace(**{**vars(self.JOB), "state": "ready"})
        await F.recap_told(bot, 1, CID, ready)
        self.assertEqual(bot.rows.rows, [(1, CID, "recap_ready", {"session_at": 100})])
        self.assertEqual(bot.posts, [])

    async def test_a_dm_who_wants_email_gets_no_failed_line_either_way(self) -> None:
        bot = recap_bot(worker_on=True, email=True)
        await F.recap_told(bot, 1, CID, self.JOB)
        self.assertEqual((bot.rows.rows, bot.posts), ([], []))

    async def test_the_allowance_row_holds_only_a_flag_and_the_bot_reads_the_refusal_itself(
        self,
    ) -> None:
        bot = recap_bot(worker_on=True)

        async def refusal(gid: int, cid: str, *, audio: bool) -> str:
            return "No recaps left this month."

        bot._recap_refusal = refusal
        self.assertEqual(
            await F._recap_allowance(bot, 1, CID, audio=False),
            "No recaps left this month.",
        )
        self.assertEqual(bot.rows.rows, [(1, CID, "recap_allowance", {"audio": False})])
        self.assertEqual(bot.posts, [])

    async def test_the_job_round_is_the_workers_with_the_switch_on(self) -> None:
        ran: list[int] = []

        async def due(gid: int) -> list[tuple[str, int, int]]:
            return [(CID, 100, 5)]

        class Worker:
            async def run(self, gid: int, cid: str, at: int) -> None:
                ran.append(gid)

        async def servers() -> list[int]:
            return [4, 6]

        def bot(on: bool) -> Any:
            b: Any = Silent()
            b.settings = SimpleNamespace(recaps_on=True, worker_on=on)
            b.recaps = SimpleNamespace(due_aged=due)
            b.recap_slots = None
            b._run_recap_job = lambda *a: F._run_recap_job(b, *a)
            b.recap_worker = Worker()
            b.work_guild_ids = servers
            b._log_queue_depth = lambda *a, **k: _none()
            b.guilds = []
            b.run_recap_jobs = lambda: F.run_recap_jobs(b)
            b.release_recap_holds = lambda gid: _none()
            b.post_recaps = lambda gid: _none()
            return b

        await F.run_recaps(bot(True))  # the bot's round: holds and Post only
        self.assertEqual(ran, [])
        await F.run_recap_jobs(bot(True))  # the worker's
        self.assertEqual(ran, [4, 6])


async def _none() -> None:
    return None


class TheOtherRecapLines(unittest.IsolatedAsyncioTestCase):
    """The other lines a recap job used to post itself (#4903), said by the bot from the row."""

    def bot(self, *, posts: bool = True, counts_only: bool = False) -> Any:
        bot = recap_bot(worker_on=True)
        notes: list[int] = []

        async def post(channel: int, text: str, view: Any = None, **kw: Any) -> bool:
            bot.posts.append(text)
            return posts

        async def note(gid: int, cid: str, dm: int, added: int) -> None:
            notes.append(added)

        async def get_job(*_a: Any) -> Any:
            return SimpleNamespace(dm_user_id=5)

        bot.notes = notes
        bot.post = post
        bot._private_names_note = note
        bot.recaps = SimpleNamespace(get=get_job)
        bot._upkeep_channel = lambda c: (9, counts_only)
        bot._say_names = lambda *a: F._say_names(bot, *a)
        bot._say_allowance = lambda *a: F._say_allowance(bot, *a)
        bot._say_unchecked = lambda *a: F._say_unchecked(bot, *a)
        return bot

    async def test_names_say_the_rows_count_and_the_private_note_follows_the_post(self) -> None:
        bot = self.bot()
        await F.deliver_recap_names(bot, 1, CID, {"session_at": 100, "added": 3})
        self.assertEqual((len(bot.posts), bot.notes), (1, [3]))

    async def test_a_refused_names_post_raises_and_sends_no_private_note(self) -> None:
        bot = self.bot(posts=False)
        with self.assertRaises(RuntimeError):
            await F.deliver_recap_names(bot, 1, CID, {"session_at": 100, "added": 3})
        self.assertEqual(bot.notes, [])  # (a retry cannot repeat it)

    async def test_names_for_a_recap_that_is_gone_say_nothing(self) -> None:
        bot = self.bot()

        async def gone(*_a: Any) -> None:
            return None

        bot.recaps = SimpleNamespace(get=gone)
        await F.deliver_recap_names(bot, 1, CID, {"session_at": 100, "added": 3})
        self.assertEqual((bot.posts, bot.notes), ([], []))

    async def test_the_audio_allowance_row_is_read_back_as_the_audio_refusal(self) -> None:
        bot = self.bot()
        asked: list[bool] = []

        async def refusal(gid: int, cid: str, *, audio: bool) -> str | None:
            asked.append(audio)
            return "No narrations left." if audio else None

        bot._recap_refusal = refusal
        await F._recap_allowance(bot, 1, CID, audio=True)
        self.assertEqual(bot.rows.rows[-1][2:], ("recap_allowance", {"audio": True}))
        await F.deliver_recap_allowance(bot, 1, CID, {"audio": True})
        self.assertEqual(asked[-1], True)
        self.assertEqual(bot.posts, ["📜 Recap: No narrations left."])

    async def test_an_allowance_that_has_room_again_says_nothing(self) -> None:
        bot = self.bot()

        async def refusal(gid: int, cid: str, *, audio: bool) -> None:
            return None

        bot._recap_refusal = refusal
        await F.deliver_recap_allowance(bot, 1, CID, {"audio": False})
        self.assertEqual(bot.posts, [])

    async def test_the_dm_bot_channel_gets_the_fixed_allowance_line(self) -> None:
        bot = self.bot(counts_only=True)

        async def refusal(gid: int, cid: str, *, audio: bool) -> str:
            return "You have used 2 of 2 on the Hobby plan."

        bot._recap_refusal = refusal
        await F.deliver_recap_allowance(bot, 1, CID, {"audio": False})
        self.assertEqual(len(bot.posts), 1)
        self.assertNotIn("Hobby", bot.posts[0])

    async def test_unchecked_is_a_row_with_the_worker_on_and_said_from_it(self) -> None:
        bot = self.bot()
        job = SimpleNamespace(session_at=100, keeps_previous=False)
        await F.recap_unchecked(bot, 1, CID, job)
        self.assertEqual(
            bot.rows.rows, [(1, CID, "recap_unchecked", {"session_at": 100, "keeps": False})]
        )
        self.assertEqual(bot.posts, [])
        await F.deliver_recap_unchecked(bot, 1, CID, {"session_at": 100, "keeps": False})
        self.assertEqual(len(bot.posts), 1)


class TheFairOrder(unittest.IsolatedAsyncioTestCase):
    """The jobs a round takes are the ones that have waited longest, whichever server they are in
    (#3845); with the worker on, up to three run at once, each under a slot."""

    def bot(self, *, worker_on: bool, free_slots: int = 3) -> Any:
        ran: list[tuple[int, str]] = []
        waits = {1: [("a", 1, 900)], 2: [("b", 1, 100), ("c", 1, 300)], 3: [("d", 1, 200)]}

        async def due_aged(gid: int) -> list[tuple[str, int, int]]:
            return waits[gid]

        class Worker:
            async def run(self, gid: int, cid: str, at: int) -> None:
                ran.append((gid, cid))

        async def servers() -> list[int]:
            return [1, 2, 3]

        left = [free_slots]

        class Slots:
            @contextlib.asynccontextmanager
            async def hold(self) -> Any:
                got = left[0] > 0
                left[0] -= 1
                yield got

        b: Any = Silent()
        b.ran = ran
        b.settings = SimpleNamespace(recaps_on=True, worker_on=worker_on)
        b.recaps = SimpleNamespace(due_aged=due_aged)
        b.recap_worker = Worker()
        b.recap_slots = Slots()
        b.work_guild_ids = servers
        b._log_queue_depth = lambda *a, **k: _none()
        b._run_recap_job = lambda *a: F._run_recap_job(b, *a)
        return b

    async def test_without_the_worker_the_two_longest_waits_run_in_order(self) -> None:
        bot = self.bot(worker_on=False)
        await F.run_recap_jobs(bot)
        self.assertEqual(bot.ran, [(2, "b"), (3, "d")])  # (not server 1's, which sorts first)

    async def test_with_the_worker_three_run_at_once_the_longest_waits_first(self) -> None:
        bot = self.bot(worker_on=True)
        await F.run_recap_jobs(bot)
        self.assertEqual(sorted(bot.ran), [(2, "b"), (2, "c"), (3, "d")])

    async def test_a_job_with_no_slot_left_waits_for_the_next_round(self) -> None:
        bot = self.bot(worker_on=True, free_slots=1)
        await F.run_recap_jobs(bot)
        self.assertEqual(len(bot.ran), 1)


class TheImportLoop(unittest.IsolatedAsyncioTestCase):
    """Cleaning and the scans are the worker's halves of the import loop (each in a loop of its
    own); the news stays the bot's (#4904 3b-4c): each half runs only where its flag is set, over
    the servers the database lists."""

    async def one_round(self, clean: bool, followups: bool, scan: bool = False) -> list[str]:
        calls: list[str] = []

        class Store:
            async def resume(self, gid: int, *, older_than_s: int) -> None:
                calls.append(f"resume {gid}")

        class Job:
            store = Store()

            async def run_round(self, gid: int, at_once: int) -> None:
                calls.append(f"clean {gid}")

        async def follow(gid: int, *, scan: bool = True, news: bool = True) -> int:
            calls.append(f"follow {gid} scan={scan} news={news}")
            return 0

        async def servers() -> list[int]:
            return [4]

        sleeps: list[float] = []

        async def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) > 1:
                raise asyncio.CancelledError

        bot: Any = Silent()
        bot.import_job = Job()
        bot.settings = SimpleNamespace(server_jobs_at_once=1)
        bot.run_import_followups = follow
        bot.work_guild_ids = servers
        with patch("dmbot.bot.asyncio.sleep", sleep), self.assertRaises(asyncio.CancelledError):
            await DMBot._import_loop(bot, clean=clean, scan=scan, followups=followups)
        return calls

    async def test_the_worker_cleans_and_scans_and_the_bot_tells_the_news(self) -> None:
        self.assertEqual(await self.one_round(True, False), ["resume 4", "clean 4"])
        # (the worker's two loops: cleaning alone, and the scans alone)
        self.assertEqual(
            await self.one_round(False, False, scan=True), ["follow 4 scan=True news=False"]
        )
        # (the bot with the worker on: the news alone)
        self.assertEqual(await self.one_round(False, True), ["follow 4 scan=False news=True"])
        # (the bot with the worker off: everything, as before)
        self.assertEqual(
            await self.one_round(True, True, scan=True),
            ["resume 4", "clean 4", "follow 4 scan=True news=True"],
        )

    async def test_with_the_worker_on_the_bot_runs_neither_cleaning_nor_scans(self) -> None:
        # (the loop the bot starts: `clean` and `scan` both off with DMBOT_WORKER; see `bot.py`)
        self.assertEqual(
            await self.one_round(False, True, scan=False), ["follow 4 scan=False news=True"]
        )
