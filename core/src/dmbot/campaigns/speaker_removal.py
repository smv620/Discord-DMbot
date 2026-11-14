"""The bot's side of a DM's "Remove this speaker's lines" on the website (#3305).

The website's role cannot touch the transcript tables, and a removal has to hold each running
session's save lock, so a request is a row (`speaker_removals`) that this module claims one at a
time and carries out with `TranscriptStore.remove_speaker`: one transaction, this campaign only.
The row names the campaign and the one speaker whose lines may go, and holds a stage and two
counts: never anyone's words. Same shape as `web_line_delete` (the person's own deletion).

A failure removes nothing (one transaction) and ends the row `failed` so the page can say so. A
speaker with no lines left (removed meanwhile) ends `done` with 0.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from dmbot.db import Database

log = logging.getLogger(__name__)

PER_ROUND = 3  # requests carried out in one round for one server
RETAKE_AFTER_S = 600  # a claim this old belongs to a bot that stopped
KEEP_FOR_S = 86_400  # a request goes a day after it was made

FAILED = "failed"  # anything unexpected; nothing was removed, asking again is fine
TAKEN = "taken"  # another worker holds the row now, so this one writes nothing
NOT_MARKED = "not_marked"  # removed, but the row could not be marked; it is retaken


@dataclass(frozen=True, slots=True)
class Request:
    id: str
    campaign_id: str
    speaker: int  # the only speaker whose lines may go, from this campaign only
    claimed_at: int  # this worker's claim: only it may write
    lines: int = 0  # counts a first try noted before it removed (0, 0 on a first try)
    sessions: int = 0


class Host(Protocol):
    """What the removal needs from the running bot, so tests can stand in for it."""

    async def remove_speaker(
        self, guild_id: int, campaign_id: str, speaker: int
    ) -> tuple[int, int]: ...

    async def count_speaker(
        self, guild_id: int, campaign_id: str, speaker: int
    ) -> tuple[int, int]: ...


class SpeakerRemovals:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Request | None:
        """The oldest waiting request of this server (or one stalled for ten minutes), marked as
        being worked on. Nothing is removed for a request that is not marked first."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE speaker_removals SET state = 'working', stage = 'removing',"
                " claimed_at = %s, updated_at = %s"
                " WHERE (guild_id, id) = (SELECT guild_id, id FROM speaker_removals"
                " WHERE guild_id = %s AND (state = 'waiting' OR (state = 'working'"
                " AND claimed_at < %s)) AND created_at >= %s ORDER BY created_at LIMIT 1"
                " FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, speaker, lines, sessions",
                (now, now, guild_id, now - RETAKE_AFTER_S, now - KEEP_FOR_S),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Request(
            row["id"], row["campaign_id"], int(row["speaker"]), now, int(row["lines"]),
            int(row["sessions"]),
        )  # fmt: skip

    async def note_counts(self, guild_id: int, item: Request, lines: int, sessions: int) -> None:
        """What is about to go, kept on the row before the removal, so a retake after a removal
        that could not be marked still reports the numbers that were removed."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE speaker_removals SET lines = %s, sessions = %s, updated_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (lines, sessions, int(self._now()), guild_id, item.id, item.claimed_at),
            )

    async def done(self, guild_id: int, item: Request, lines: int, sessions: int) -> bool:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE speaker_removals SET state = 'done', stage = 'done', lines = %s,"
                " sessions = %s, updated_at = %s WHERE guild_id = %s AND id = %s"
                " AND state = 'working' AND claimed_at = %s",
                (lines, sessions, int(self._now()), guild_id, item.id, item.claimed_at),
            )
            return bool(cur.rowcount)

    async def failed(self, guild_id: int, item: Request, error: str) -> bool:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE speaker_removals SET state = 'failed', stage = 'failed', error = %s,"
                " lines = 0, sessions = 0, updated_at = %s WHERE guild_id = %s AND id = %s"
                " AND state = 'working' AND claimed_at = %s",
                (error, int(self._now()), guild_id, item.id, item.claimed_at),
            )
            return bool(cur.rowcount)

    async def sweep(self, guild_id: int) -> int:
        """Delete requests older than `KEEP_FOR_S`, whatever their state."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM speaker_removals WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


async def carry_out(host: Host, guild_id: int, item: Request, store: SpeakerRemovals) -> str:
    """Carry out one claimed request and mark it. Returns the outcome word ("" when it worked).
    Never raises. Only counts are logged, never the speaker or the campaign.

    `failed` is written only when the removal itself failed (nothing was removed: it is one
    transaction). If it worked and the row cannot be marked, the row stays `working` and is
    retaken after ten minutes, which is harmless: the removal repeats as a no-op."""
    if item.lines == 0 and item.sessions == 0:  # a first try: note what is about to go
        try:
            counted = await host.count_speaker(guild_id, item.campaign_id, item.speaker)
            await store.note_counts(guild_id, item, *counted)
        except Exception as exc:  # best effort: the numbers are only for the page
            log.warning("A speaker removal could not note its counts: %s", type(exc).__name__)
    try:
        lines, sessions = await host.remove_speaker(guild_id, item.campaign_id, item.speaker)
    except Exception as exc:  # the type only: an error can quote a row
        log.warning("A speaker removal failed: %s", type(exc).__name__)
        try:
            await store.failed(guild_id, item, FAILED)
        except Exception:
            log.warning("A speaker removal could not be marked failed")
        return FAILED
    try:
        if (lines, sessions) == (0, 0):  # a retake finds nothing left: the first try removed them
            lines, sessions = item.lines, item.sessions
        return "" if await store.done(guild_id, item, lines, sessions) else TAKEN
    except Exception as exc:
        log.warning("A speaker removal could not be marked done: %s", type(exc).__name__)
        return NOT_MARKED
