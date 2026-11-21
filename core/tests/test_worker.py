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

        bot = SimpleNamespace(audio_worker=object(), _recap_audio_loop=loop)
        got = worker.loops(bot)
        self.assertEqual([name for name, _ in got], ["recap-audio"])
        for _, coro in got:
            coro.close()
        bot.audio_worker = None
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
