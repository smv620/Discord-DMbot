"""Run the website's API: python -m dmbot.web (its own container in docker-compose)."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import uvicorn

from dmbot.config import ConfigError
from dmbot.db import Database, DatabaseError
from dmbot.logs import LOG_FORMATS, LOG_LEVELS, configure_logging
from dmbot.sharding import ShardSettings
from dmbot.web import sessions
from dmbot.web.app import create_app
from dmbot.web.discord import HttpDiscord
from dmbot.web.settings import WebSettings, load_web_settings

log = logging.getLogger("dmbot.web")
CLEANUP_SECONDS = 3600


async def _sweep_expired_sessions(db: Database) -> None:
    while True:
        try:
            removed = await sessions.delete_expired(db)
            if removed:
                log.info("Removed %d expired sign-in sessions", removed)
        except Exception:
            log.exception("Removing expired sessions failed; trying again later")
        await asyncio.sleep(CLEANUP_SECONDS)


async def serve(settings: WebSettings) -> None:
    db = await Database.open(settings.database_url)
    app = create_app(
        settings, db, HttpDiscord(settings.discord_client_id, settings.discord_client_secret)
    )
    sweeper = asyncio.create_task(_sweep_expired_sessions(db))
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        proxy_headers=True,  # behind Caddy or Cloudflare (#435)
        server_header=False,
        log_config=None,  # our own logging (ids only)
        access_log=False,  # access lines would include query strings
    )
    try:
        await uvicorn.Server(config).serve()
    finally:
        sweeper.cancel()
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
