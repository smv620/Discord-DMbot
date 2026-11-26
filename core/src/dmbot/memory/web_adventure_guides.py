"""The bot's side of Guide me on the website (#3694 slice F): a queued row (`web_adventure_guides`,
migration 0645) is claimed and answered with the one function Discord's 🧭 uses, so the two can
never disagree. The functions that need the bot (the plan check, the story memory, the clashes) are
passed in by the bot, which keeps this module free of Discord and testable without it.

A job is `read` (work out the answer), `pick` (the DM says the table is in another chapter:
`memory.guide.pick`, then the new answer) or `clear` (back to DMbot's guess: `memory.guide.clear`,
then the new answer). Every job is checked again here: the person is still a DM, the Shared story
switch is on "Guide me" with a DM's confirmation, and the plan allows it.

The answer holds names and short summaries from the DM's own book: it is kept on the row for a day
at most, read back only by a DM of that campaign, and never logged. Logs carry outcome words only.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database
from dmbot.memory.models import MemoryRuleError

log = logging.getLogger(__name__)

RETAKE_AFTER_S = 600  # the door's own limit for a claim that went quiet
KEEP_FOR_S = 86400  # as long as the website reads an answer back

READ, PICK, CLEAR = "read", "pick", "clear"

NOT_YOURS = "not_yours"  # no longer a DM of the campaign
NOT_GUIDE = "not_guide"  # the switch is not on "Guide me", or no DM confirmed the right
PLAN = "plan"  # the campaign's plan does not allow reading the adventure
NO_CHAPTERS = "no_chapters"  # the file has no chapters read yet
GONE = "gone"  # the file or the chapter is not in the book any more
NOT_AVAILABLE = "not_available"  # the bot has no memory just now
FAILED = "failed"  # anything else, including a bug


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    campaign_id: str
    user_id: int
    job: str
    document_id: str
    chapter_id: str
    claimed_at: int


class GuideStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Item | None:
        now_ms = int(self._now() * 1000)  # milliseconds, so two claims of one row never match
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "WITH pick AS (SELECT guild_id, id FROM web_adventure_guides"
                " WHERE guild_id = %s AND (state = 'waiting'"
                "  OR (state = 'working' AND claimed_at < %s))"
                " ORDER BY seq LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " UPDATE web_adventure_guides a SET state = 'working', claimed_at = %s,"
                " updated_at = %s FROM pick WHERE a.guild_id = pick.guild_id AND a.id = pick.id"
                " RETURNING a.id, a.campaign_id, a.user_id, a.job, a.document_id, a.chapter_id",
                (guild_id, now_ms - RETAKE_AFTER_S * 1000, now_ms, now),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Item(
            row["id"], row["campaign_id"], int(row["user_id"]), row["job"], row["document_id"],
            row["chapter_id"], now_ms,
        )  # fmt: skip

    async def finish(
        self, guild_id: int, item: Item, error: str, answer: dict[str, Any] | None = None
    ) -> bool:
        """End the job. Only the worker that holds the current claim can write. The answer is
        kept only on a job that succeeded (the table refuses it on any other row)."""
        from psycopg.types.json import Jsonb  # (a database type: kept out of the module's top)

        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_adventure_guides SET state = %s, error = %s, answer = %s,"
                " updated_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (
                    "failed" if error else "done",
                    error[:40],
                    Jsonb({} if error or answer is None else answer),
                    int(self._now()),
                    guild_id,
                    item.id,
                    item.claimed_at,
                ),
            )
            return bool(cur.rowcount)

    async def sweep(self, guild_id: int) -> int:
        """Delete jobs older than `KEEP_FOR_S`, whatever their state."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM web_adventure_guides WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


@dataclass(frozen=True, slots=True)
class Host:
    """What the bot supplies. `check` is the error code for a person who may not use Guide me with
    this campaign now (`not_yours`, `not_guide`, `plan`), or None. `compose` is the answer for one
    file as a plain dict (None: the file has no chapters). `pick` and `clear` write the DM's pick,
    as Discord's menu does."""

    check: Callable[[int, str, int], Awaitable[str | None]]
    compose: Callable[[int, str, str], Awaitable[dict[str, Any] | None]]
    pick: Callable[[int, str, str, str, int], Awaitable[object]]
    clear: Callable[[int, str, str], Awaitable[object]]


async def run_one(host: Host, store: GuideStore, guild_id: int, item: Item) -> str:
    """Do one job and say how it ended: '' for done, else an error code."""
    answer: dict[str, Any] | None = None
    try:
        error = await host.check(guild_id, item.campaign_id, item.user_id) or ""
        if not error:
            if item.job == PICK:
                await host.pick(
                    guild_id, item.campaign_id, item.document_id, item.chapter_id, item.user_id
                )
            elif item.job == CLEAR:
                await host.clear(guild_id, item.campaign_id, item.document_id)
            answer = await host.compose(guild_id, item.campaign_id, item.document_id)
            if answer is None:
                error = NO_CHAPTERS
    except MemoryRuleError:
        error = GONE  # the chapter is not in the book any more
    except Exception as exc:  # the type only: nothing here may quote the book
        log.warning("A Guide me job failed: %s", type(exc).__name__)
        error = FAILED
    await store.finish(guild_id, item, error, answer)
    log.info("A Guide me job ended: %s", error or "done")
    return error
