"""The bot's side of Match to files on the website (#3694 slice G1): a queued row
(`web_adventure_matches`, migration 0647) is claimed and done with the calls Discord's 🔗 makes
(`memory.book_links`: `suggestions`, `decide`, and `memory.undo` of what `decide` wrote). The
functions that need the bot are passed in, which keeps this module free of Discord. Nothing here
changes `memory/book_links.py`: it only calls what is there.

Jobs: `suggest` (the pairs worth asking about), `decide` (the DM's Same or Not the same for up to
ten pairs: one `decide` call for each, each its own change with its own Undo, and the job stops at
the first refusal, telling how many were written, which pick stopped and a code for why) and
`undo` (take back a `decide` job's changes, newest first, stopping at the first that is too late).

Every job is checked again here (still a DM, the Shared story switch on with a DM's confirmation).
The answer holds ids and names from the DM's book and table: kept a day at most, read back only by
a DM of the campaign, never logged. Logs carry outcome words only.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database
from dmbot.memory.book_links import CLASH, GONE, TAKEN_BOOK, TAKEN_TABLE, TWICE
from dmbot.memory.models import MemoryRuleError, TooLateToUndo

log = logging.getLogger(__name__)

RETAKE_AFTER_S = 600  # the door's own limit for a claim that went quiet
KEEP_FOR_S = 86400  # as long as the website reads an answer back

SUGGEST, DECIDE, UNDO = "suggest", "decide", "undo"

NOT_YOURS = "not_yours"  # no longer a DM of the campaign
NOT_ON = "not_on"  # the switch is off, or no DM confirmed the right to use the material
NOT_AVAILABLE = "not_available"  # the bot has no memory just now
FAILED = "failed"  # anything else, including a bug

# What a refused pick says, as the website's own codes: `book_links`' sentences are Discord's words
# and stay there (the page has its own, and a change to a Discord sentence cannot change them).
_REFUSALS = {
    GONE: "gone",
    CLASH: "clash",
    TWICE: "twice",
    TAKEN_BOOK: "taken_book",
    TAKEN_TABLE: "taken_table",
}

TOO_LATE = "too_late"  # an undo that is past its time
CHANGED_SINCE = "changed_since"  # an undo of something changed again after


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    campaign_id: str
    user_id: int
    job: str
    picks: tuple[tuple[str, str, str], ...]
    batches: tuple[int, ...]
    claimed_at: int


class MatchStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Item | None:
        now_ms = int(self._now() * 1000)  # milliseconds, so two claims of one row never match
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "WITH pick AS (SELECT guild_id, id FROM web_adventure_matches"
                " WHERE guild_id = %s AND (state = 'waiting'"
                "  OR (state = 'working' AND claimed_at < %s))"
                " ORDER BY seq LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " UPDATE web_adventure_matches a SET state = 'working', claimed_at = %s,"
                " updated_at = %s FROM pick WHERE a.guild_id = pick.guild_id AND a.id = pick.id"
                " RETURNING a.id, a.campaign_id, a.user_id, a.job, a.picks, a.batches",
                (guild_id, now_ms - RETAKE_AFTER_S * 1000, now_ms, now),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Item(
            row["id"], row["campaign_id"], int(row["user_id"]), row["job"],
            tuple((str(p[0]), str(p[1]), str(p[2])) for p in row["picks"]),
            tuple(int(b) for b in row["batches"]), now_ms,
        )  # fmt: skip

    async def finish(
        self,
        guild_id: int,
        item: Item,
        error: str,
        answer: dict[str, Any] | None = None,
        batches: Sequence[int] = (),
    ) -> bool:
        """End the job. Only the worker that holds the current claim can write. The answer is kept
        only on a job that succeeded; `batches` are what a `decide` wrote, for the Undo."""
        from psycopg.types.json import Jsonb  # (a database type: kept out of the module's top)

        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_adventure_matches SET state = %s, error = %s, answer = %s,"
                " batches = %s, updated_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (
                    "failed" if error else "done",
                    error[:40],
                    Jsonb({} if error or answer is None else answer),
                    [] if error else list(batches),
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
                "DELETE FROM web_adventure_matches WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


@dataclass(frozen=True, slots=True)
class Host:
    """What the bot supplies. `check`: the error code for a person who may not use Match to files
    with this campaign now (`not_yours`, `not_on`, `not_available`), or None. `suggest`: the pairs
    as plain dicts. `decide`: one `book_links.decide` of a single pair, giving its change's batch
    (None when nothing was written), raising `MemoryRuleError` with the store's plain words.
    `undo`: `memory.undo` of one batch, raising `TooLateToUndo` or `MemoryRuleError`."""

    check: Callable[[int, str, int], Awaitable[str | None]]
    suggest: Callable[[int, str], Awaitable[list[dict[str, str]]]]
    decide: Callable[[int, str, tuple[str, str, str], int], Awaitable[int | None]]
    undo: Callable[[int, str, int], Awaitable[object]]


async def run_one(host: Host, store: MatchStore, guild_id: int, item: Item) -> str:
    """Do one job and say how it ended: '' for done, else an error code."""
    answer: dict[str, Any] | None = None
    batches: list[int] = []
    try:
        error = await host.check(guild_id, item.campaign_id, item.user_id) or ""
        if not error:
            if item.job == SUGGEST:
                answer = {"pairs": await host.suggest(guild_id, item.campaign_id)}
            elif item.job == DECIDE:
                answer, batches = await _decide(host, guild_id, item)
            else:
                answer = await _undo(host, guild_id, item)
    except Exception as exc:  # the type only: nothing here may quote the book
        log.warning("A Match to files job failed: %s", type(exc).__name__)
        error, answer, batches = FAILED, None, []
    await store.finish(guild_id, item, error, answer, batches)
    log.info("A Match to files job ended: %s", error or "done")
    return error


async def _decide(host: Host, guild_id: int, item: Item) -> tuple[dict[str, Any], list[int]]:
    """One `decide` for each pick, each its own change; stop at the first refusal."""
    written, batches, refusal, at = 0, [], "", None
    for index, pick in enumerate(item.picks):
        try:
            batch = await host.decide(guild_id, item.campaign_id, pick, item.user_id)
        except MemoryRuleError as exc:
            refusal, at = _REFUSALS.get(str(exc), FAILED), index  # a code, never Discord's words
            break
        written += 1
        if batch is not None:
            batches.append(batch)
    return {"written": written, "refusal": refusal, "at": at}, batches


async def _undo(host: Host, guild_id: int, item: Item) -> dict[str, Any]:
    """Take the batches back, newest first, and stop at the first that cannot be."""
    undone, note = 0, ""
    for batch in reversed(item.batches):
        try:
            await host.undo(guild_id, item.campaign_id, batch)
        except TooLateToUndo:
            note = TOO_LATE
            break
        except MemoryRuleError:
            note = CHANGED_SINCE
            break
        undone += 1
    return {"undone": undone, "note": note}
