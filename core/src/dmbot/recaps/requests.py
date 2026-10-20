"""The bot's side of what a DM types on the recap page (#2113).

The website has no AI key, so its notes to DMbot are queued as rows (`recap_requests`); this
module claims them, sorts the words with the same sort and gate as the DM screen's Tell DMbot
(`recaps.feedback`), and writes the points back on the row for the page to poll. The words are
the DM's own about one recap: they are never logged (counts only), a request older than an hour
is deleted whatever happened, and a claimed one that stalled is picked up again.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb

from dmbot.db import Database
from dmbot.recaps.feedback import NOTHING_REPLY, Sorted

log = logging.getLogger(__name__)

RETAKE_AFTER_S = 120  # a claim this old belongs to a bot that stopped
SORT_TIMEOUT_S = RETAKE_AFTER_S - 30  # the sort always ends before its claim can be taken back
KEEP_FOR_S = 3600  # nothing waits on a row longer than this

SORT_FAILED = "sort_failed"  # the AI failed or its answer could not be read: try again
NOT_ALLOWED = "not_allowed"  # recaps or the plan are off for this campaign
TOO_SOON = "too_soon"  # the campaign's limit on feedback


@dataclass(frozen=True, slots=True)
class Request:
    id: str
    campaign_id: str
    session_at: int
    user_id: int
    body: str
    claimed_at: int = 0  # this worker's claim: only it may finish the row


def answer_of(sorted_: Sorted) -> dict[str, Any]:
    """The points as the page shows them. Ids are only good for this request: "c1", "s1", ….
    When nothing could be made of the words, the reply says so (as the DM screen's does), so the
    page never shows the DM nothing."""
    if sorted_.empty:
        return {"corrections": [], "style": [], "replies": [NOTHING_REPLY]}
    return {
        "corrections": [{"id": f"c{n}", "text": t} for n, t in enumerate(sorted_.corrections, 1)],
        "style": [{"id": f"s{n}", "text": t} for n, t in enumerate(sorted_.style, 1)],
        "replies": list(sorted_.replies),
    }


class RequestStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Request | None:
        """The oldest waiting request (or a stalled one), marked as being worked on."""
        # Milliseconds, so two claims of one row never carry the same value (the claim is the
        # token that lets only the latest worker write the answer).
        now = int(self._now() * 1000)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE recap_requests SET state = 'working', claimed_at = %s WHERE (guild_id, id)"
                " = (SELECT guild_id, id FROM recap_requests WHERE guild_id = %s AND"
                " (state = 'waiting' OR (state = 'working' AND claimed_at < %s))"
                " ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, session_at, user_id, body",
                (now, guild_id, now - RETAKE_AFTER_S * 1000),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Request(
            row["id"],
            row["campaign_id"],
            int(row["session_at"]),
            int(row["user_id"]),
            row["body"],
            now,
        )

    async def finish(
        self,
        guild_id: int,
        request: Request,
        *,
        answer: dict[str, Any] | None,
        error: str = "",
    ) -> bool:
        """The points (done) or the reason it could not be done (failed). Only the worker that
        holds the current claim can write: a slow worker whose row was taken back and answered
        by another changes nothing. False: it was not this worker's row any more."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE recap_requests SET state = %s, error = %s, answer = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (
                    "done" if answer is not None else "failed",
                    error,
                    None if answer is None else Jsonb(answer),
                    guild_id,
                    request.id,
                    request.claimed_at,
                ),
            )
            return bool(cur.rowcount)

    async def sweep(self, guild_id: int) -> int:
        """Delete requests older than `KEEP_FOR_S`, whatever their state."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM recap_requests WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


Gate = Callable[[Request], Awaitable[str | None]]
Sorter = Callable[[str], Awaitable[Sorted | None]]


async def stop_reason(
    request: Request,
    campaign: Any,
    job: Any,
    *,
    has_ai: bool,
    feedback_gate: Callable[[], Awaitable[str | None]],
) -> str | None:
    """Why a claimed request must not be sorted, or None to go on. Read at the moment of use:
    the sender is still a DM of the campaign, the recap is still a draft, there is an AI, and the
    campaign's switch, plan and limit (`feedback_gate`: None, or an error code) allow it. All of
    it before any AI call costs anything."""
    if campaign is None or job is None or job.status != "draft" or not has_ai:
        return NOT_ALLOWED
    if request.user_id not in campaign.dm_user_ids:
        return NOT_ALLOWED
    return await feedback_gate()


async def run_one(
    store: RequestStore, guild_id: int, request: Request, *, gate: Gate, sort: Sorter
) -> str:
    """Do one claimed request. `gate` returns None to go on, or the error code that stops it
    before any AI call; `sort` is `sort_feedback` with the AI bound, held to `SORT_TIMEOUT_S`.
    Returns the outcome word for the log (never the words). Never raises."""
    try:
        stopped = await gate(request)
        if stopped is not None:
            await store.finish(guild_id, request, answer=None, error=stopped)
            return stopped
        sorted_ = await asyncio.wait_for(sort(request.body), SORT_TIMEOUT_S)
        if sorted_ is None:
            await store.finish(guild_id, request, answer=None, error=SORT_FAILED)
            return SORT_FAILED
        await store.finish(guild_id, request, answer=answer_of(sorted_))
        return "done"
    except Exception as exc:  # the row must never stay "working" for a bug
        log.warning("Recap request failed: %s", type(exc).__name__)  # never the words
        try:
            await store.finish(guild_id, request, answer=None, error=SORT_FAILED)
        except Exception:
            log.warning("Recap request could not be marked failed")
        return SORT_FAILED
