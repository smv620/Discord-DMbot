"""The bot's side of giving a Shared story file from the website (#3694 slice D): a queued row
(`web_adventure_gives`, migration 0641) names a file the website put in the staging bucket. The bot
claims it, reads it with the reader Discord's 📖 uses, keeps it with the same call
(`AdventureStore.add`: a DM of the campaign, the switch on and a DM's confirmation on record are
checked again there), and deletes the object. It never starts the AI read: that is the DM's own
yes, with its cost shown (slice E).

The row moves through the shared progress stages (`waiting`, `reading`, `saving`, then `done` or
`failed`) so the page can show where it is and when it last moved (CLAUDE.md, "Every wait shows
that DMbot is still working"). Rows and objects hold no word of the book that outlives the job, and
logs carry counts and outcome words only, never a file's name or words.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from dmbot.db import Database
from dmbot.memory.adventure import (
    ALREADY,
    FULL,
    NO_TEXT,
    NOT_DM,
    NOT_ON,
    TOO_MANY,
    AdventureError,
    AdventureStore,
)
from dmbot.memory.name_documents import DocumentError
from dmbot.pictures.bucket import BucketError

log = logging.getLogger(__name__)

RETAKE_AFTER_S = 600  # the door's own limit for a claim that went quiet
KEEP_FOR_S = 86400  # as long as the website reads an answer back

EXPIRED = "expired"  # the staged file is gone (the bucket's own 1-day rule)
NOT_YOURS = "not_yours"  # no longer a DM of the campaign
REFUSED = "refused"  # the store's own plain words (the switch, a repeat, the limit)
UNREADABLE = "unreadable"  # the reader's plain words
FAILED = "failed"  # anything else, including a bug


# The store's own plain words for a refusal, as the codes the page turns into words.
_REFUSALS = {
    NOT_DM: NOT_YOURS,
    NOT_ON: "not_on",
    ALREADY: "already",
    TOO_MANY: "too_many",
    FULL: "full",
    NO_TEXT: "no_text",
}


class Staging(Protocol):
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


# Reads a file's text with the reader Discord's upload uses (supplied by the bot, as the reader and
# its limits belong to the Discord side): DocumentError for a file it cannot use.
ReadFn = Callable[[str, bytes], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    campaign_id: str
    user_id: int
    object_key: str
    file_name: str
    claimed_at: int


@dataclass(frozen=True, slots=True)
class Outcome:
    error: str = ""
    characters: int = 0
    chunks: int = 0


class GiveStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Item | None:
        now_ms = int(self._now() * 1000)  # milliseconds, so two claims of one row never match
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "WITH pick AS (SELECT guild_id, id FROM web_adventure_gives"
                " WHERE guild_id = %s AND (state = 'waiting'"
                "  OR (state = 'working' AND claimed_at < %s))"
                " ORDER BY seq LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " UPDATE web_adventure_gives a SET state = 'working', stage = 'reading',"
                " claimed_at = %s, updated_at = %s FROM pick"
                " WHERE a.guild_id = pick.guild_id AND a.id = pick.id"
                " RETURNING a.id, a.campaign_id, a.user_id, a.object_key, a.file_name",
                (guild_id, now_ms - RETAKE_AFTER_S * 1000, now_ms, now),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Item(
            row["id"], row["campaign_id"], int(row["user_id"]), row["object_key"],
            row["file_name"], now_ms,
        )  # fmt: skip

    async def saving(self, guild_id: int, item: Item) -> bool:
        """The file is read and is being kept (a stage the page shows). False: not its row now."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_adventure_gives SET stage = 'saving', updated_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (int(self._now()), guild_id, item.id, item.claimed_at),
            )
            return bool(cur.rowcount)

    async def finish(self, guild_id: int, item: Item, outcome: Outcome) -> bool:
        """End the row. Only the worker that holds the current claim can write; the object key is
        blanked in the same statement (the table refuses a finished row that keeps it)."""
        failed = bool(outcome.error)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_adventure_gives SET state = %s, stage = %s, error = %s,"
                " object_key = '', characters = %s, chunks = %s, updated_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (
                    "failed" if failed else "done",
                    "failed" if failed else "done",
                    outcome.error[:40],
                    outcome.characters,
                    outcome.chunks,
                    int(self._now()),
                    guild_id,
                    item.id,
                    item.claimed_at,
                ),
            )
            return bool(cur.rowcount)

    async def sweep(self, guild_id: int) -> list[str]:
        """Delete rows older than `KEEP_FOR_S` and give back the object keys they still named, so
        the caller can delete the objects (the bucket's own rule usually has them already)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM web_adventure_gives WHERE guild_id = %s AND created_at < %s"
                " RETURNING object_key",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return [r["object_key"] for r in await cur.fetchall() if r["object_key"]]


async def run_one(
    adventure: AdventureStore,
    store: GiveStore,
    staging: Staging,
    read: ReadFn,
    guild_id: int,
    item: Item,
) -> Outcome:
    """Do one given file and say how it ended. The object is deleted whatever happens."""
    try:
        outcome = await _give(adventure, store, staging, read, guild_id, item)
    except Exception as exc:  # the type only: nothing here may quote the book
        log.warning("An adventure file could not be kept: %s", type(exc).__name__)
        outcome = Outcome(FAILED)
    with contextlib.suppress(BucketError):  # (left to the bucket's own 1-day rule)
        await staging.delete(item.object_key)
    await store.finish(guild_id, item, outcome)
    log.info(
        "An adventure file ended: %s (%d characters, %d parts)",
        outcome.error or "kept", outcome.characters, outcome.chunks,
    )  # fmt: skip
    return outcome


async def _give(
    adventure: AdventureStore,
    store: GiveStore,
    staging: Staging,
    read: ReadFn,
    guild_id: int,
    item: Item,
) -> Outcome:
    try:
        raw = await staging.get(item.object_key)
    except BucketError:
        return Outcome(EXPIRED)
    try:
        text = await read(item.file_name, raw)
    except DocumentError:
        return Outcome(UNREADABLE)
    if not await store.saving(guild_id, item):
        return Outcome(FAILED)  # not this worker's row any more: its end is ignored by `finish`
    try:
        doc = await adventure.add(guild_id, item.campaign_id, item.user_id, item.file_name, text)
    except AdventureError as exc:
        return Outcome(_REFUSALS.get(str(exc), REFUSED))
    return Outcome("", doc.characters, doc.chunks)
