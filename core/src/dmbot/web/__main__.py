"""Run the website's API: python -m dmbot.web (its own container in docker-compose)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import time

import uvicorn

from dmbot.config import ConfigError
from dmbot.db import Database, DatabaseError
from dmbot.logs import LOG_FORMATS, LOG_LEVELS, configure_logging
from dmbot.schema import WEB_ROLE
from dmbot.sharding import ShardSettings
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.discord import HttpDiscord
from dmbot.web.payments import FakeProvider
from dmbot.web.settings import WebSettings, load_web_settings

log = logging.getLogger("dmbot.web")
CLEANUP_SECONDS = 3600


async def _sweep_expired_sessions(db: Database) -> None:
    while True:
        try:
            removed = await sessions.delete_expired(db, now=int(time.time()))
            if removed:
                log.info("Removed %d expired sign-in sessions", removed)
        except Exception:
            log.exception("Removing expired sessions failed; trying again later")
        await asyncio.sleep(CLEANUP_SECONDS)


async def serve(settings: WebSettings) -> None:
    # A small pool of its own, and no query may run longer than 5 seconds: a busy website
    # can never hold many of the database's connections for long. It never changes the
    # schema (its role, dmbot_web, can't): the bot does, so it only checks it's current.
    db = await Database.open(
        settings.database_url,
        options="-c statement_timeout=5000",
        max_size=4,
        migrate=False,
        # Its limits in the database only hold for its own role (#498).
        require_role=None if settings.any_db_role else WEB_ROLE,
    )
    discord = HttpDiscord(settings.discord_client_id, settings.discord_client_secret)
    payments = (
        FakeProvider(settings.payment_webhook_secret, settings.site_url)
        if settings.payment_provider == "fake"
        else None
    )
    app = create_app(settings, db, discord, payments=payments)
    sweeper = asyncio.create_task(_sweep_expired_sessions(db))
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        server_header=False,
        log_config=None,  # our own logging (ids only)
        access_log=False,  # access lines would include query strings
    )
    try:
        await uvicorn.Server(config).serve()
    finally:
        sweeper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeper
        await discord.close()
        await db.close()


def main() -> int:
    fmt = os.environ.get("LOG_FORMAT", "text").strip().lower()
    level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    configure_logging(
        fmt if fmt in LOG_FORMATS else "text",
        level if level in LOG_LEVELS else "INFO",
        ShardSettings(),
    )
    try:
        settings = load_web_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        asyncio.run(serve(settings))
    except DatabaseError as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
