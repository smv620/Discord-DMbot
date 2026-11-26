"""The bot's side of removing a Shared story file from the website (#3694 slice C): a queued row
(`web_adventure_removals`, migration 0639) is claimed in order and done with the call Discord's 🗑
makes (`AdventureStore.remove`), which checks again that the person is still a DM of the campaign.

A removal is instant, so there is no progress to show: the row ends `done` or `failed` with an error
word the page turns into words. A row holds no word of the book, and logs carry outcome words only.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from dmbot.db import Database
from dmbot.memory.adventure import GONE_DOC, NOT_DM, AdventureError, AdventureStore

log = logging.getLogger(__name__)

RETAKE_AFTER_S = 600  # the door's own limit for a claim that went quiet
KEEP_FOR_S = 86400  # as long as the website reads an answer back

NOT_YOURS = "not_yours"  # no longer a DM of the campaign
GONE = "gone"  # the file was already removed (the page treats it as done)
FAILED = "failed"  # anything else


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    campaign_id: str
    user_id: int
    document_id: str
    claimed_at: int


class RemovalStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Item | None:
        """The oldest waiting removal of this server (or a stalled one), marked as taken."""
        now_ms = int(self._now() * 1000)  # milliseconds, so two claims of one row never match
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "WITH pick AS (SELECT guild_id, id FROM web_adventure_removals"
                " WHERE guild_id = %s AND (state = 'waiting'"
                "  OR (state = 'working' AND claimed_at < %s))"
                " ORDER BY seq LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " UPDATE web_adventure_removals a SET state = 'working', claimed_at = %s,"
                " updated_at = %s FROM pick WHERE a.guild_id = pick.guild_id AND a.id = pick.id"
                " RETURNING a.id, a.campaign_id, a.user_id, a.document_id",
                (guild_id, now_ms - RETAKE_AFTER_S * 1000, now_ms, now),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Item(row["id"], row["campaign_id"], int(row["user_id"]), row["document_id"], now_ms)

    async def finish(self, guild_id: int, item: Item, error: str) -> bool:
        """End the removal. Only the worker that holds the current claim can write."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_adventure_removals SET state = %s, error = %s, updated_at = %s"
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
        """Delete removals older than `KEEP_FOR_S`, whatever their state."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM web_adventure_removals WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


async def run_one(adventure: AdventureStore, store: RemovalStore, guild_id: int, item: Item) -> str:
    """Do one removal and say how it ended: '' for removed (a file already gone counts as done,
    the DM's wish is met), else an error word."""
    try:
        await adventure.remove(guild_id, item.campaign_id, item.user_id, item.document_id)
        error = ""
    except AdventureError as exc:
        text = str(exc)
        error = "" if text == GONE_DOC else NOT_YOURS if text == NOT_DM else FAILED
    except Exception as exc:  # the type only: nothing here may quote the book
        log.warning("An adventure removal failed: %s", type(exc).__name__)
        error = FAILED
    await store.finish(guild_id, item, error)
    log.info("An adventure file removal ended: %s", error or "removed")
    return error
