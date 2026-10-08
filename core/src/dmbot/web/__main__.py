"""Run the website's API: python -m dmbot.web (its own container in docker-compose)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import time
from urllib.parse import urlsplit

import uvicorn

from dmbot import entitlements
from dmbot.config import ConfigError
from dmbot.db import Database, DatabaseError
from dmbot.logs import LOG_FORMATS, LOG_LEVELS, configure_logging
from dmbot.schema import WEB_ROLE
from dmbot.sharding import ShardSettings
from dmbot.web import feedback, sessions
from dmbot.web.admin import HttpGoogle
from dmbot.web.app import create_app
from dmbot.web.discord import HttpDiscord
from dmbot.web.feedback import GitHubDiscussions, Turnstile
from dmbot.web.payments import FakeProvider
from dmbot.web.settings import WebSettings, load_web_settings

log = logging.getLogger("dmbot.web")
CLEANUP_SECONDS = 3600


async def _sweep(db: Database) -> None:
    """Hourly: expired sign-in sessions, and "Say hello" messages over a year old."""
    while True:
        try:
            removed = await sessions.delete_expired(db, now=int(time.time()))
            if removed:
                log.info("Removed %d expired sign-in sessions", removed)
        except Exception:
            log.exception("Removing expired sessions failed; trying again later")
        try:
            # "Say hello" messages and contacts are kept for a year (privacy page, #665).
            forgotten = await feedback.forget_old(db)
            if forgotten:
                log.info("Removed %d messages older than a year", forgotten)
        except Exception as exc:
            log.error("Removing old messages failed (%s); trying again later", type(exc).__name__)
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
    discussions = (
        GitHubDiscussions(settings.feedback_token, settings.feedback_repo)
        if settings.feedback_token
        else None
    )
    google = (
        HttpGoogle(settings.google_client_id, settings.google_client_secret)
        if settings.google_client_id
        else None
    )
    human_check = (
        Turnstile(settings.turnstile_secret, urlsplit(settings.site_url).hostname or "")
        if settings.turnstile_secret
        else None
    )
    entitlements.configure_free_users(settings.free_users)  # #771
    app = create_app(
        settings,
        db,
        discord,
        payments=payments,
        discussions=discussions,
        human_check=human_check,
        google=google,
    )
    sweeper = asyncio.create_task(_sweep(db))
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
        if discussions is not None:
            await discussions.close()
        if human_check is not None:
            await human_check.close()
        if google is not None:
            await google.close()
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
