"""`python -m dmbot.worker`: the bot's own services without a Discord connection (#3844).

It builds the same stores and workers as the bot (`bot.build_bot(headless=True)`), never logs in
(no `DISCORD_TOKEN` is needed or used), works every server that has a campaign
(`DMBot.work_guild_ids`, from the database), and runs only the loops that have moved to it. With
`DMBOT_WORKER=0` it does nothing (the bot runs everything itself, which is the way back), and says
so. Moved so far: the recap text jobs, the import cleaning, the import scans (the name scan, the
story read and the recaps' start) and the recap audio job (the audio one
at a time across processes by the database lease, part 1 of #2128). What a job wants said in
Discord is written to the outbox (part 2) or left in the job's state for the bot's own notices; the
worker never posts.

It touches a file every `BEAT_S` from a task of its own, so the container's healthcheck can tell
a dead process or event loop from a live one. It cannot tell a hung job (a paid audio render may
rightly take twenty minutes); the job's own deadline and the lease's expiry handle that. A loop
that ends by itself is a fault: the worker exits 1 and the container restarts. Counts and stage
words only in the log.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

from dmbot.config import ConfigError, Settings, load_settings
from dmbot.logs import configure_logging

log = logging.getLogger("dmbot.worker")

BEAT_S = 20.0
HEARTBEAT = Path(os.environ.get("DMBOT_WORKER_BEAT", "/tmp/dmbot-worker-alive"))


def _touch(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.touch()


async def _beat(path: Path) -> None:
    while True:
        await asyncio.to_thread(_touch, path)
        await asyncio.sleep(BEAT_S)


async def _wait_for_stop() -> asyncio.Event:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    return stop


def loops(bot: Any) -> list[tuple[str, Coroutine[Any, Any, None]]]:
    """The loops the worker runs: each only where its worker exists on the bot. The bot starts the
    same ones (with `job` off) only when `DMBOT_WORKER` is off, so the two never both run a job."""
    out: list[tuple[str, Coroutine[Any, Any, None]]] = []
    if bot.recap_worker is not None:
        out.append(("recap-text", bot._recap_jobs_loop()))
    if bot.import_job is not None:
        out.append(("import-clean", bot._import_loop(clean=True, scan=False, followups=False)))
        # (its own loop: the name scan and the story read make AI calls that take minutes, and a
        # DM's next file must not wait behind them to be cleaned)
        out.append(("import-scan", bot._import_loop(clean=False, scan=True, followups=False)))
    if bot.audio_worker is not None:
        out.append(("recap-audio", bot._recap_audio_loop(job=True, notices=False)))
    if bot.settings.card_summary_on:
        out.append(("card-sentences", bot._card_loop()))
    return out


async def run(settings: Settings) -> int:
    if not settings.worker_on:
        # Stay up doing nothing (and healthy), so the container is not restarted in a loop.
        log.info("The worker is off (DMBOT_WORKER is 0): the bot runs everything itself")
        beat = asyncio.create_task(_beat(HEARTBEAT), name="beat")
        await (await _wait_for_stop()).wait()
        beat.cancel()
        return 0
    from dmbot import alerts, entitlements, person_key
    from dmbot.bot import build_bot
    from dmbot.db import Database
    from dmbot.ui import dm_links

    entitlements.configure_free_users(settings.free_users)
    person_key.configure(settings.person_key)
    entitlements.configure_free_guilds(settings.free_guilds)
    dm_links.configure_site(settings.site_url)
    alerts.install(alerts.Alerter(settings.mail))
    stop = await _wait_for_stop()  # (first: a stop during start-up still closes the database)
    db = await Database.open(settings.database_url, max_size=settings.database_pool_max)
    tasks: list[asyncio.Task[None]] = []
    try:
        bot = await build_bot(settings, db, headless=True)
        bot.headless = True
        tasks = [asyncio.create_task(_beat(HEARTBEAT), name="beat")]
        tasks += [asyncio.create_task(c, name=n) for n, c in loops(bot)]
        log.info(
            "The worker is running: %s", ", ".join(t.get_name() for t in tasks[1:]) or "nothing"
        )
        stopper = asyncio.create_task(stop.wait())
        await asyncio.wait([stopper, *tasks], return_when=asyncio.FIRST_COMPLETED)
        stopper.cancel()
        return 0 if stop.is_set() else 1  # (a loop that ended by itself is a fault: restart)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            await db.close()


def main() -> None:
    load_dotenv(find_dotenv(usecwd=True))
    try:
        settings = load_settings(worker=True)
    except ConfigError as exc:
        print(f"Configuration problem: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    configure_logging(settings.log_format, settings.log_level, settings.shards)
    code = asyncio.run(run(settings))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
