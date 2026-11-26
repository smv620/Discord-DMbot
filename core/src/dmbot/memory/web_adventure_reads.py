"""The bot's side of "Yes, read it" for a Shared story file, given on the website (#3694 slice E): a
queued row (`web_adventure_reads`, migration 0643) is claimed and done with the calls Discord's
ReadYesButton makes: the plan check, `PlanStore.begin` (a DM, the switch on, the confirmation on
record, a readable file not yet read, the per-file and monthly caps) and `start_plan_read`.

The row only says whether the read STARTED (`done`) or why not (`failed`, with a code the page turns
into words). The read itself runs as it does for Discord and shows its own progress in
`adventure_reads`, which the page's list already carries (stage, how far, when it last moved). Its
AI calls are metered as the owner's AI credits (`credits.preparing`, in the bot's `_read_plan`).
Rows hold no word of the book, and logs carry outcome words only.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from dmbot.db import Database
from dmbot.memory.adventure import NOT_DM, NOT_ON, AdventureError
from dmbot.memory.story_plan import (
    ALREADY_READ,
    DOCUMENT_FULL,
    GONE,
    MONTH_FULL,
    NOT_READABLE,
    PlanStore,
)

log = logging.getLogger(__name__)

RETAKE_AFTER_S = 600  # the door's own limit for a claim that went quiet
KEEP_FOR_S = 86400  # as long as the website reads an answer back

NOT_YOURS = "not_yours"  # no longer a DM of the campaign
PLAN = "plan"  # the campaign's plan does not allow reading the adventure
BUSY = "busy"  # this file is already being read
FAILED = "failed"  # anything else, including a bug

# The store's own plain words for a refusal, as the codes the page turns into words.
_REFUSALS = {
    NOT_DM: NOT_YOURS,
    NOT_ON: "not_on",
    GONE: "gone",
    NOT_READABLE: "not_readable",
    ALREADY_READ: "already_read",
    DOCUMENT_FULL: "document_full",
    MONTH_FULL: "month_full",
}


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    campaign_id: str
    user_id: int
    document_id: str
    claimed_at: int


class ReadStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Item | None:
        now_ms = int(self._now() * 1000)  # milliseconds, so two claims of one row never match
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "WITH pick AS (SELECT guild_id, id FROM web_adventure_reads"
                " WHERE guild_id = %s AND (state = 'waiting'"
                "  OR (state = 'working' AND claimed_at < %s))"
                " ORDER BY seq LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " UPDATE web_adventure_reads a SET state = 'working', claimed_at = %s,"
                " updated_at = %s FROM pick WHERE a.guild_id = pick.guild_id AND a.id = pick.id"
                " RETURNING a.id, a.campaign_id, a.user_id, a.document_id",
                (guild_id, now_ms - RETAKE_AFTER_S * 1000, now_ms, now),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Item(row["id"], row["campaign_id"], int(row["user_id"]), row["document_id"], now_ms)

    async def finish(self, guild_id: int, item: Item, error: str) -> bool:
        """End the request. Only the worker that holds the current claim can write."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_adventure_reads SET state = %s, error = %s, updated_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (
                    "failed" if error else "done",
                    error[:40],
                    int(self._now()),
                    guild_id,
                    item.id,
                    item.claimed_at,
                ),
            )
            return bool(cur.rowcount)

    async def sweep(self, guild_id: int) -> int:
        """Delete requests older than `KEEP_FOR_S`, whatever their state."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM web_adventure_reads WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


# The plan's refusal in plain words for this person, or None (the bot's `plan_gate`, with the
# campaign looked up there: a campaign that is gone is a refusal too).
Refusal = Callable[[int, str, int], Awaitable[str | None]]
# Starts the read in the background (the bot's `start_plan_read`), from the chunk it carries on at.
Start = Callable[[int, str, str, int], None]


async def run_one(
    plan: PlanStore,
    store: ReadStore,
    refusal: Refusal,
    start: Start,
    is_reading: Callable[[int, str], bool],
    guild_id: int,
    item: Item,
) -> str:
    """Start one read and say how it went: '' for started, else an error code."""
    try:
        if await refusal(guild_id, item.campaign_id, item.user_id):
            error = PLAN
        elif is_reading(guild_id, item.document_id):
            error = BUSY
        else:
            state = await plan.begin(guild_id, item.campaign_id, item.user_id, item.document_id)
            start(guild_id, item.campaign_id, item.document_id, state.next_chunk)
            error = ""
    except AdventureError as exc:
        error = _REFUSALS.get(str(exc), FAILED)
    except Exception as exc:  # the type only: nothing here may quote the book
        log.warning("An adventure read could not be started: %s", type(exc).__name__)
        error = FAILED
    await store.finish(guild_id, item, error)
    log.info("An adventure read request ended: %s", error or "started")
    return error
