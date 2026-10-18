"""Run the website's API: python -m dmbot.web (its own container in docker-compose)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import time
from dataclasses import replace
from urllib.parse import urlsplit

import uvicorn

from dmbot import alerts, entitlements, mail
from dmbot.ai import DEFAULT_MODELS, AIModelTier, AnthropicClient, Feature
from dmbot.config import ConfigError
from dmbot.db import Database, DatabaseError
from dmbot.logs import LOG_FORMATS, LOG_LEVELS, configure_logging
from dmbot.schema import WEB_ROLE
from dmbot.sharding import ShardSettings
from dmbot.web import feedback, move_sync, sessions, strays, support
from dmbot.web.admin import HttpGoogle
from dmbot.web.app import create_app
from dmbot.web.discord import HttpDiscord
from dmbot.web.feedback import GitHubDiscussions, Turnstile
from dmbot.web.moving import EmailedMoveCodes, forget_old_asks
from dmbot.web.payments import FakeProvider, PaymentProvider
from dmbot.web.settings import WebSettings, load_web_settings
from dmbot.web.stripe import StripeProvider
from dmbot.web.support_ai import SupportHelper, SupportSpend

log = logging.getLogger("dmbot.web")
CLEANUP_SECONDS = 3600
MOVE_SYNC_SECONDS = 60
STRAY_SWEEP_SECONDS = 600  # the database hands each one out at most once an hour


async def _sweep(db: Database, helper: SupportHelper) -> None:
    """Hourly: expired sign-in sessions, "Say hello" messages over a year old, support tickets
    over 12 months old, and answered support tickets quiet for two weeks (closed)."""
    while True:
        try:
            removed = await sessions.delete_expired(db, now=int(time.time()))
            if removed:
                log.info("Removed %d expired sign-in sessions", removed)
        except Exception:
            log.exception("Removing expired sessions failed; trying again later")
        try:
            old = await forget_old_asks(db, now=int(time.time()))
            if old:
                log.info("Removed %d old move-code asks", old)
        except Exception as exc:
            log.error("Removing old move-code asks failed (%s); trying again", type(exc).__name__)
        try:
            # "Say hello" messages and contacts are kept for a year (privacy page, #665).
            forgotten = await feedback.forget_old(db)
            if forgotten:
                log.info("Removed %d messages older than a year", forgotten)
        except Exception as exc:
            log.error("Removing old messages failed (%s); trying again later", type(exc).__name__)
        try:
            # Support tickets are kept for 12 months (privacy page, #1340).
            expired = await support.forget_old(db)
            if expired:
                log.info("Removed %d support tickets older than 12 months", expired)
        except Exception as exc:
            log.error("Removing old support tickets failed (%s); trying again", type(exc).__name__)
        try:
            # Answered tickets nobody has written to for two weeks are finished (#1505).
            closed = await support.close_stale(db, int(time.time()))
            if closed:
                log.info(
                    "Closed %d answered support tickets after %d quiet days",
                    closed,
                    support.SUPPORT_AUTO_CLOSE_DAYS,
                )
        except Exception as exc:
            log.error("Closing old answered tickets failed (%s); trying again", type(exc).__name__)
        try:
            # The team's weekly count of support questions, once a week (#1514).
            if await helper.weekly_report() == "sent":
                log.info("Sent the weekly support email to the team")
        except Exception as exc:
            log.error("The weekly support email failed (%s); trying again", type(exc).__name__)
        await asyncio.sleep(CLEANUP_SECONDS)


async def _sync_moves(db: Database, payments: PaymentProvider) -> None:
    """Every minute: bring the payment company's records up to date for accounts that moved
    (#1230). Cheap when nothing waits (a small partial index)."""
    while True:
        try:
            done = await move_sync.sync_moves(db, payments)
            if done:
                log.info("Updated the payment records of %d moved accounts", done)
        except Exception:
            log.exception("Updating moved accounts' payment records failed; trying again")
        await asyncio.sleep(MOVE_SYNC_SECONDS)


async def _help_with_tickets(helper: SupportHelper) -> None:
    """The support helper's pass (#1431), every minute: tickets waiting for DMbot's helper."""
    while True:
        try:
            await helper.run_once()
        except Exception:
            log.exception("The support helper's pass failed")
        await asyncio.sleep(60)


async def _sweep_strays(db: Database, payments: PaymentProvider) -> None:
    """Cancel recap audio add-ons that must not charge and could not be cancelled at once
    (#1399). Cheap when nothing waits (a tiny table)."""
    while True:
        try:
            await strays.sweep(db, payments)
        except Exception:
            log.exception("Cancelling stray recap audio subscriptions failed; trying again")
        await asyncio.sleep(STRAY_SWEEP_SECONDS)


async def serve(settings: WebSettings) -> None:
    # A small pool of its own, and no query may run longer than 5 seconds: a busy website
    # can never hold many of the database's connections for long. It never changes the
    # schema (its role, dmbot_web, can't): the bot does, so it only checks it's current.
    # Owner alerts (#1907) by email: the website has no Discord login, so a mail failure is
    # only logged here (the bot's own process sends the private message).
    alerter = alerts.Alerter(settings.mail)
    alerts.install(alerter)
    alerter.say_if_off()
    db = await Database.open(
        settings.database_url,
        options="-c statement_timeout=5000",
        max_size=4,
        migrate=False,
        # Its limits in the database only hold for its own role (#498).
        require_role=None if settings.any_db_role else WEB_ROLE,
    )
    discord = HttpDiscord(settings.discord_client_id, settings.discord_client_secret)
    payments: PaymentProvider | None = None
    if settings.payment_provider == "fake":
        payments = FakeProvider(settings.payment_webhook_secret, settings.site_url)
    elif settings.payment_provider == "stripe" and settings.stripe is not None:
        payments = StripeProvider(settings.stripe, settings.payment_webhook_secret)
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
    # Account emails (sign-in links, #1231); nothing is sent until SMTP is set up in .env.
    mailer = (
        mail.Mailer(
            db,
            settings.mail,
            site_url=settings.site_url,
            link_secret=settings.link_token_secret,
        )
        if settings.mail.configured
        else None
    )
    # A move code is mailed like a sign-in link, so it needs the same mailer (#1246).
    move_codes = EmailedMoveCodes(db, mailer) if mailer is not None else None
    # DMbot's support helper (#1431): needs the AI key to answer; without it tickets still get their
    # thank-you and the team's replies work, and nothing is answered by the AI.
    ai_client: AnthropicClient | None = None
    if settings.ai_key:
        models = replace(DEFAULT_MODELS, deep=settings.support_ai_model or DEFAULT_MODELS.deep)
        ai_client = AnthropicClient(settings.ai_key, models, watch=SupportSpend())
    helper = SupportHelper(
        db,
        ai_client.tier(AIModelTier.DEEP, Feature.SUPPORT) if ai_client is not None else None,
        mailer,
        team_email=settings.support_team_email,
        site_url=settings.site_url,
        link_secret=settings.link_token_secret,
        reply_domain=settings.mail_reply_domain,
    )
    app = create_app(
        settings,
        db,
        discord,
        payments=payments,
        discussions=discussions,
        human_check=human_check,
        google=google,
        mailer=mailer,
        move_codes=move_codes,
        helper=helper,
    )
    sweeper = asyncio.create_task(_sweep(db, helper))
    helping = asyncio.create_task(_help_with_tickets(helper)) if ai_client is not None else None
    syncer = asyncio.create_task(_sync_moves(db, payments)) if payments is not None else None
    stray_sweeper = (
        asyncio.create_task(_sweep_strays(db, payments)) if payments is not None else None
    )
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
        if helping is not None:
            helping.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await helping
        if ai_client is not None:
            await ai_client.close()
        if syncer is not None:
            syncer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await syncer
        if stray_sweeper is not None:
            stray_sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stray_sweeper
        await discord.close()
        if discussions is not None:
            await discussions.close()
        if human_check is not None:
            await human_check.close()
        if google is not None:
            await google.close()
        if isinstance(payments, StripeProvider):
            await payments.close()
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
