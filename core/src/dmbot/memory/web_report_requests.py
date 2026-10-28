"""The website's "make the report again" for one session (#2805, the bot half of #2661).

A DM asks on the website; its door queues a row in `web_report_requests` (it cannot touch the
memory tables or call the AI). The bot takes the oldest waiting row, checks again what the website
could not (the campaign is there, the asker is still a DM, the plan allows it, the session is the
campaign's), makes the report with the same step as after a session (so it has the same call
limits), and closes the row `done` or `failed` with one short word. How far the making has got is
the report's own run (`memory_report_runs`), which the page already reads, so the row carries no
progress, and no word of any story. A row quiet for ten minutes is taken again.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from dmbot.campaigns import Campaign
from dmbot.db import Database

log = logging.getLogger(__name__)


RETAKE_AFTER_S = 600  # a taken request quiet this long belongs to a bot that stopped
NOT_TAKEN_AFTER_S = 1800  # a request nobody took in this long ends as failed: the page shows it so
TOUCH_EVERY_S = 60.0  # while it runs, the row is touched so it is not taken again

# The short words a failed row carries (the page words them).
UNAVAILABLE = "unavailable"  # the report step is switched off or has no AI
LIMIT = "limit"  # the extractor's call limit is used
PLAN = "plan"  # the campaign's plan does not allow it (or cannot be checked)
NOT_TAKEN = "not_taken"  # nobody took it in time (the bot was off); closed by the next claim
NOT_A_DM = "not_a_dm"  # the asker stopped being a DM of the campaign
BUSY = "busy"  # a report for the session was already being made (the page has words for it)
FAILED = "failed"  # the campaign or session is gone, or anything unexpected


@dataclass(frozen=True, slots=True)
class Request:
    id: str
    campaign_id: str
    user_id: int
    session_at: int
    claimed_at: int = 0  # this worker's claim: only it may finish the row


class Host(Protocol):
    """What making a report needs from the running bot, so tests can stand in for it."""

    async def get_campaign(self, guild_id: int, campaign_id: str) -> Campaign | None: ...

    async def report_for_session_at(self, guild_id: int, campaign_id: str, started_at: int) -> str:
        """Make the session's report, as after a session. "" when it was made (a report the call
        limit stopped is still made: it says what was left); else one of the words above."""
        ...


class RequestStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Request | None:
        """The oldest waiting request of this server (or one taken and gone quiet), marked as
        being worked on."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            # A request nobody took in half an hour is not made now: the page already shows it
            # as failed, so it is closed the same way here, not run behind the DM's back.
            await conn.execute(
                "UPDATE web_report_requests SET state = 'failed', error = %s, updated_at = %s"
                " WHERE guild_id = %s AND state = 'waiting' AND created_at < %s",
                (NOT_TAKEN, now, guild_id, now - NOT_TAKEN_AFTER_S),
            )
            cur = await conn.execute(
                "UPDATE web_report_requests SET state = 'working', claimed_at = %s,"
                " updated_at = %s, error = ''"
                " WHERE (guild_id, id) = (SELECT guild_id, id FROM web_report_requests"
                " WHERE guild_id = %s AND (state = 'waiting' OR (state = 'working'"
                " AND updated_at < %s)) ORDER BY seq LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, user_id, session_at",
                (now, now, guild_id, now - RETAKE_AFTER_S),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Request(
            str(row["id"]),
            str(row["campaign_id"]),
            int(row["user_id"]),
            int(row["session_at"]),
            now,
        )

    async def touch(self, guild_id: int, req: Request) -> bool:
        """Still working: the row is not taken again. False when it is not this worker's now."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_report_requests SET updated_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (int(self._now()), guild_id, req.id, req.claimed_at),
            )
            return bool(cur.rowcount)

    async def finish(self, guild_id: int, req: Request, error: str) -> bool:
        """Done, or failed with one short word. Only the worker that holds the claim can write."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_report_requests SET state = %s, error = %s, updated_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (
                    "failed" if error else "done",
                    error[:40],
                    int(self._now()),
                    guild_id,
                    req.id,
                    req.claimed_at,
                ),
            )
            return bool(cur.rowcount)


async def apply(host: Host, store: RequestStore, guild_id: int, req: Request) -> str:
    """Check again, then make the report. The word for a failed row, "" when it was made."""
    campaign = await host.get_campaign(guild_id, req.campaign_id)
    if campaign is None:
        return FAILED
    if req.user_id not in campaign.dm_user_ids:  # they may have handed over since asking
        return NOT_A_DM

    async def keep_alive() -> None:
        while True:
            await asyncio.sleep(TOUCH_EVERY_S)
            try:
                if not await store.touch(guild_id, req):
                    log.info("A website report request is no longer this worker's")
            except Exception as exc:  # one database error must not end the keeping alive
                log.warning("A website report request could not be touched: %s", type(exc).__name__)

    ticker = asyncio.create_task(keep_alive())
    try:
        return await host.report_for_session_at(guild_id, req.campaign_id, req.session_at)
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await ticker


async def run_one(host: Host, store: RequestStore, guild_id: int, req: Request) -> bool:
    """Make one claimed request's report and close the row. A bug never leaves the row "working":
    it ends as `failed`. False when the row could not be closed (the loop stops; the row is taken
    again later). Only the outcome word is logged, never anything of the story."""
    try:
        error = await apply(host, store, guild_id, req)
    except Exception as exc:
        log.warning("A website report request failed: %s", type(exc).__name__)
        error = FAILED
    try:
        if not await store.finish(guild_id, req, error):
            log.info("A website report request was taken over by another worker")
    except Exception as exc:
        # (the row stays working and is taken again after ten minutes)
        log.warning("A website report request could not be closed: %s", type(exc).__name__)
        return False
    log.info("Website report request: %s", error or "done")
    return True
