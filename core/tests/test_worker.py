"""The worker process (#3844): it needs no Discord token, the bot and the worker never both run
the recap audio job, and a headless bot works the servers the database lists. No database, no
Discord."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from dmbot.config import ConfigError, load_settings
from dmbot.recaps.discord import RecapFeatures
from dmbot.worker import __main__ as worker

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
            _recap_audio_loop=loop,
            _recap_jobs_loop=lambda: loop(),
        )
        got = worker.loops(bot)
        self.assertEqual([name for name, _ in got], ["recap-text", "recap-audio"])
        for _, coro in got:
            coro.close()
        bot.audio_worker = bot.recap_worker = None
        self.assertEqual(worker.loops(bot), [])

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

        bot = Bot()
        bot.seen = seen  # type: ignore[attr-defined]
        bot.settings = load_settings({**BASE, "DMBOT_WORKER": "1" if worker_on else "0"})  # type: ignore[attr-defined]
        bot.audio_worker = object()  # type: ignore[attr-defined]
        bot._recap_audio_loop = audio_loop  # type: ignore[attr-defined]
        bot._watched = watched  # type: ignore[attr-defined]
        return bot

    async def test_the_bot_runs_the_audio_job_only_with_the_worker_off(self) -> None:
        for on in (False, True):
            stub = self.stub(worker_on=on)
            tasks = RecapFeatures.recap_tasks(stub)
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
        self.assertEqual(await RecapFeatures.work_guild_ids(stub), [3, 5])
        stub.headless = False
        self.assertEqual(await RecapFeatures.work_guild_ids(stub), [9])


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
            await RecapFeatures._recap_audio_loop(stub, job=job, notices=notices)
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

    bot = Bot()

    async def post(channel: int, text: str, view: Any = None, **kw: Any) -> bool:
        posts.append(text)
        return True

    async def get_campaign(gid: int, cid: str) -> Any:
        return SimpleNamespace(
            recaps_email=email, dm_bot_channel_id=7, dm_accounts=(), guild_id=gid, id=cid
        )

    bot.posts = posts  # type: ignore[attr-defined]
    bot.rows = Rows()  # type: ignore[attr-defined]
    bot.outbox = bot.rows  # type: ignore[attr-defined]
    bot.post = post  # type: ignore[attr-defined]
    bot.campaigns = SimpleNamespace(get=get_campaign)  # type: ignore[attr-defined]
    bot._upkeep_channel = lambda c: (9, False)  # type: ignore[attr-defined]
    bot.settings = SimpleNamespace(worker_on=worker_on, site_url="")  # type: ignore[attr-defined]
    bot._queued = lambda *a: RecapFeatures._queued(bot, *a)  # type: ignore[attr-defined]
    bot._say_failed = lambda *a: RecapFeatures._say_failed(bot, *a)  # type: ignore[attr-defined]
    bot._post_recap_ready = lambda *a: RecapFeatures._post_recap_ready(bot, *a)  # type: ignore[attr-defined]
    return bot


class TheRecapTextJob(unittest.IsolatedAsyncioTestCase):
    """With the worker on a recap job says nothing in Discord itself: each line is an outbox row of
    ids and counts (#4903), and the bot's handler says the same words from it."""

    JOB = SimpleNamespace(
        state="failed", session_at=100, keeps_previous=True, to_confirm=[], title="The Silver Lock"
    )

    async def test_a_failed_recap_is_a_row_with_the_worker_on_and_a_post_with_it_off(self) -> None:
        on = recap_bot(worker_on=True)
        await RecapFeatures.recap_told(on, 1, CID, self.JOB)
        self.assertEqual(on.posts, [])
        self.assertEqual(
            on.rows.rows, [(1, CID, "recap_failed", {"session_at": 100, "keeps": True})]
        )
        off = recap_bot(worker_on=False)
        await RecapFeatures.recap_told(off, 1, CID, self.JOB)
        self.assertEqual(off.rows.rows, [])
        self.assertEqual(len(off.posts), 1)
        self.assertIn("couldn't write", off.posts[0])

    async def test_the_handler_says_the_same_words_the_bot_said_itself(self) -> None:
        said = recap_bot(worker_on=False)
        await RecapFeatures.recap_told(said, 1, CID, self.JOB)
        handled = recap_bot(worker_on=True)
        handled._say_failed = lambda *a: RecapFeatures._say_failed(handled, *a)  # type: ignore[attr-defined]
        await RecapFeatures.deliver_recap_failed(
            handled, 1, CID, {"session_at": 100, "keeps": True}
        )
        self.assertEqual(handled.posts, said.posts)

    async def test_a_ready_recap_is_a_row_with_the_worker_on(self) -> None:
        bot = recap_bot(worker_on=True)
        ready = SimpleNamespace(**{**vars(self.JOB), "state": "ready"})
        await RecapFeatures.recap_told(bot, 1, CID, ready)
        self.assertEqual(bot.rows.rows, [(1, CID, "recap_ready", {"session_at": 100})])
        self.assertEqual(bot.posts, [])

    async def test_a_dm_who_wants_email_gets_no_failed_line_either_way(self) -> None:
        bot = recap_bot(worker_on=True, email=True)
        await RecapFeatures.recap_told(bot, 1, CID, self.JOB)
        self.assertEqual((bot.rows.rows, bot.posts), ([], []))

    async def test_the_allowance_row_holds_nothing_and_the_bot_reads_the_refusal_itself(
        self,
    ) -> None:
        bot = recap_bot(worker_on=True)

        async def refusal(gid: int, cid: str, *, audio: bool) -> str:
            return "No recaps left this month."

        bot._recap_refusal = refusal  # type: ignore[attr-defined]
        self.assertEqual(
            await RecapFeatures._recap_allowance(bot, 1, CID, audio=False),
            "No recaps left this month.",
        )
        self.assertEqual(bot.rows.rows, [(1, CID, "recap_allowance", {})])
        self.assertEqual(bot.posts, [])

    async def test_the_job_round_is_the_workers_with_the_switch_on(self) -> None:
        ran: list[int] = []

        async def due(gid: int) -> list[tuple[str, int]]:
            return [(CID, 100)]

        class Worker:
            async def run(self, gid: int, cid: str, at: int) -> None:
                ran.append(gid)

        async def servers() -> list[int]:
            return [4, 6]

        def bot(on: bool) -> Any:
            b = Silent()
            b.settings = SimpleNamespace(recaps_on=True, worker_on=on)  # type: ignore[attr-defined]
            b.recaps = SimpleNamespace(due=due)  # type: ignore[attr-defined]
            b.recap_worker = Worker()  # type: ignore[attr-defined]
            b.work_guild_ids = servers  # type: ignore[attr-defined]
            b.guilds = []  # type: ignore[attr-defined]
            b.run_recap_jobs = lambda: RecapFeatures.run_recap_jobs(b)  # type: ignore[attr-defined]
            b.release_recap_holds = lambda gid: _none()  # type: ignore[attr-defined]
            b.post_recaps = lambda gid: _none()  # type: ignore[attr-defined]
            return b

        await RecapFeatures.run_recaps(bot(True))  # the bot's round: holds and Post only
        self.assertEqual(ran, [])
        await RecapFeatures.run_recap_jobs(bot(True))  # the worker's
        self.assertEqual(ran, [4, 6])


async def _none() -> None:
    return None
