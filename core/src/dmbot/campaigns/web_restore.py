"""The bot's side of restoring a backup from the website (#2746, #2745).

The website's role cannot write the campaign tables, so a copy a DM uploads there is staged in
`web_restores` (with its transcripts files in `web_restore_parts`) and waits for the bot. This
module claims a waiting row and applies it with the same code as Discord's `/dmbot restore`
(`RestoreChoice.restore`): the same plan check, the same free campaign slot, the same
`import_backup`, the same planner refresh and rules-channel refresh afterwards. Nothing the website
checked is trusted: at the moment of use the person must still be in the server, to replace a
campaign still one of its DMs, the plan must still allow copies, and a campaign being played is
never replaced. All under the server's session lock, as in Discord.

The row carries the shared progress shape of the "every wait shows it is still working" rule
(CLAUDE.md, #2564): `stage` (`restore_reading`, `restore_writing`), `done_parts` of `parts_wanted`,
and `updated_at`, which moves on every step so the page can say when it last moved. A refusal is one
plain word (`plan`, `not_allowed`, `not_found`, `playing`, `not_a_backup`, `too_big`, `parts`,
`failed`) that the page turns into words and what to do next. The copy and its files are emptied by
the table's own trigger the moment the row is `done` or `failed`; rows older than 24 hours go
whether or not they ended (`sweep`). A copy holds the campaign's secrets like any backup, so it is
never logged; only the outcome word is.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from dmbot.campaigns import CampaignError
from dmbot.campaigns.models import Campaign
from dmbot.campaigns.parts import LeftOut
from dmbot.campaigns.store import (
    DAMAGED,
    NO_ROOM_TO_RESTORE,
    NOT_A_BACKUP,
    PARTS_NOT_THIS_COPY,
    decode_backup,
)
from dmbot.campaigns.store import TOO_BIG as FILE_TOO_BIG
from dmbot.db import Database
from dmbot.ui import logic

log = logging.getLogger(__name__)

PER_ROUND = 1  # restores applied in one round for one server (each can be large)
RETAKE_AFTER_S = 300  # a claim whose row has not moved for this long belongs to a bot that stopped
KEEP_FOR_S = 86400  # a row and its files go 24 hours after it began

PLAN = "plan"  # the plan has no copies, or no room for another campaign
NOT_ALLOWED = "not_allowed"  # not in the server any more, or not a DM of the campaign to replace
NOT_FOUND = "not_found"  # the campaign to replace is gone
PLAYING = "playing"  # the campaign to replace is being played
NOT_A_BACKUP_WORD = "not_a_backup"
TOO_BIG = "too_big"
PARTS = "parts"  # the transcripts files are not exactly the set this copy came with
FAILED = "failed"  # anything unexpected; trying again is fine

# The store's plain-words refusals, as the word the page has words for.
_BY_MESSAGE = {
    NO_ROOM_TO_RESTORE: PLAN,
    FILE_TOO_BIG: TOO_BIG,
    NOT_A_BACKUP: NOT_A_BACKUP_WORD,
    DAMAGED: NOT_A_BACKUP_WORD,
    PARTS_NOT_THIS_COPY: PARTS,
}


def refusal_word(message: str) -> str:
    """The page's word for a refusal the store worded for Discord."""
    word = _BY_MESSAGE.get(message)
    if word is not None:
        return word
    if message.startswith("This copy has") and "transcripts file" in message:
        return PARTS
    if message.startswith("Only this campaign's DM"):
        return NOT_ALLOWED
    if "transcripts" in message and "damaged" in message.lower():
        return NOT_A_BACKUP_WORD
    return FAILED


@dataclass(frozen=True, slots=True)
class Restore:
    id: str
    user_id: int
    replace_id: str | None
    parts_wanted: int
    without_parts: bool
    claimed_at: int  # this worker's claim: only it may write


@dataclass(frozen=True, slots=True)
class Outcome:
    error: str = ""
    result_id: str | None = None
    left_out: int = 0


class Host(Protocol):
    """What applying a restore needs from the running bot, so tests can stand in for Discord."""

    async def get_campaign(self, guild_id: int, campaign_id: str) -> Campaign | None: ...

    async def restore_gate(
        self, guild_id: int, user_id: int, replacing: Campaign | None = None
    ) -> str | None: ...

    async def is_campaign_playing(self, guild_id: int, campaign_id: str) -> bool: ...

    async def in_server(self, guild_id: int, user_id: int) -> bool: ...

    def session_lock(self, guild_id: int) -> asyncio.Lock: ...

    async def import_backup(
        self,
        guild_id: int,
        data: object,
        importer_id: int,
        *,
        replace_campaign_id: str | None,
        parts: list[bytes],
        left_out: LeftOut,
        progress: Callable[[int, int], Awaitable[None]],
    ) -> Campaign: ...

    async def after_restore(self, guild_id: int, campaign_id: str, replaced: bool) -> None:
        """The planner's statistics (a big restore adds thousands of rows) and, for a replaced
        campaign, its rules channel."""
        ...


class WebRestores:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Restore | None:
        """The oldest waiting restore of this server (or one whose worker stopped), marked as
        being read. Rows are in seconds, as the website writes them."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_restores SET state = 'working', stage = 'restore_reading',"
                " claimed_at = %s, updated_at = %s, done_parts = 0"
                " WHERE (guild_id, id) = (SELECT guild_id, id FROM web_restores"
                " WHERE guild_id = %s AND (state = 'waiting' OR (state = 'working'"
                " AND updated_at < %s)) ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, user_id, replace_id, parts_wanted, without_parts",
                (now, now, guild_id, now - RETAKE_AFTER_S),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Restore(
            row["id"],
            int(row["user_id"]),
            row["replace_id"],
            int(row["parts_wanted"]),
            bool(row["without_parts"]),
            now,
        )

    async def read(self, guild_id: int, item: Restore) -> tuple[bytes | None, list[bytes]]:
        """The staged copy and its transcripts files, in order. (Separate from the claim so a
        refusal that needs no bytes never loads them.)"""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT main FROM web_restores WHERE guild_id = %s AND id = %s"
                " AND state = 'working' AND claimed_at = %s",
                (guild_id, item.id, item.claimed_at),
            )
            row = await cur.fetchone()
            if row is None or row["main"] is None:
                return None, []
            cur = await conn.execute(
                "SELECT data FROM web_restore_parts WHERE guild_id = %s AND restore_id = %s"
                " AND data IS NOT NULL ORDER BY n",
                (guild_id, item.id),
            )
            parts = [bytes(r["data"]) for r in await cur.fetchall()]
        return bytes(row["main"]), parts

    async def move(
        self,
        guild_id: int,
        item: Restore,
        *,
        stage: str | None = None,
        done_parts: int | None = None,
    ) -> None:
        """Show progress: the stage and/or the parts done, and that the worker is still going."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE web_restores SET updated_at = %s,"
                " stage = COALESCE(%s, stage), done_parts = COALESCE(%s, done_parts)"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (int(self._now()), stage, done_parts, guild_id, item.id, item.claimed_at),
            )

    async def finish(self, guild_id: int, item: Restore, outcome: Outcome) -> bool:
        """Done, or failed with why. The table's trigger empties the copy and its files. Only the
        worker holding the current claim writes. False: not its row any more."""
        state = "failed" if outcome.error else "done"
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_restores SET state = %s, stage = %s, error = %s, result_id = %s,"
                " left_out = %s, done_parts = CASE WHEN %s THEN done_parts ELSE parts_wanted END,"
                " updated_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (
                    state,
                    state,
                    outcome.error,
                    outcome.result_id,
                    outcome.left_out,
                    bool(outcome.error) or item.without_parts,
                    int(self._now()),
                    guild_id,
                    item.id,
                    item.claimed_at,
                ),
            )
            return bool(cur.rowcount)

    async def sweep(self, guild_id: int) -> int:
        """Delete rows 24 hours after they began, ended or not; the files go with them."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM web_restores WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


async def apply(host: Host, store: WebRestores, guild_id: int, item: Restore) -> Outcome:
    """Apply one claimed restore the way Discord's would. Never raises for a refusal: the outcome
    carries the word."""
    # Who and where, before any bytes are read: nothing the website checked is trusted.
    if not await host.in_server(guild_id, item.user_id):
        return Outcome(NOT_ALLOWED)
    replacing: Campaign | None = None
    if item.replace_id is not None:
        replacing = await host.get_campaign(guild_id, item.replace_id)
        if replacing is None:
            return Outcome(NOT_FOUND)
        if item.user_id not in replacing.dm_user_ids:  # (handed over since they asked)
            return Outcome(NOT_ALLOWED)
    if await host.restore_gate(guild_id, item.user_id, replacing) is not None:
        return Outcome(PLAN)
    main, parts = await store.read(guild_id, item)
    if main is None:
        return Outcome(FAILED)
    try:
        data = await asyncio.to_thread(decode_backup, main)
    except CampaignError as exc:
        return Outcome(refusal_word(str(exc)))
    del main
    if logic.backup_campaign_name(data) is None:
        return Outcome(NOT_A_BACKUP_WORD)
    wanted = logic.backup_parts(data)
    if item.without_parts:
        parts = []  # the person chose to restore without them
    if (parts and len(parts) != wanted) or (wanted and not parts and not item.without_parts):
        return Outcome(PARTS)
    erased = LeftOut()
    async with host.session_lock(guild_id):  # a campaign cannot be replaced while it starts up
        if item.replace_id is not None and await host.is_campaign_playing(
            guild_id, item.replace_id
        ):
            return Outcome(PLAYING)
        await store.move(guild_id, item, stage="restore_writing", done_parts=0)

        async def progress(done: int, total: int) -> None:
            await store.move(guild_id, item, done_parts=done)

        try:
            campaign = await host.import_backup(
                guild_id,
                data,
                item.user_id,
                replace_campaign_id=item.replace_id,
                parts=parts,
                left_out=erased,
                progress=progress,
            )
        except CampaignError as exc:
            return Outcome(refusal_word(str(exc)))
    await host.after_restore(guild_id, campaign.id, item.replace_id is not None)
    return Outcome(result_id=campaign.id, left_out=erased.lines)


async def run_one(host: Host, store: WebRestores, guild_id: int, item: Restore) -> bool:
    """Apply one claimed restore and mark it. A bug never leaves the row "working": it ends as
    `failed` for the page to offer another try. False when the row could not be marked (the loop
    stops; the row is taken again later). Only the outcome word is logged, never the copy."""
    try:
        done = await apply(host, store, guild_id, item)
    except Exception as exc:
        log.warning("A website restore failed: %s", type(exc).__name__)
        done = Outcome(FAILED)
    try:
        if not await store.finish(guild_id, item, done):
            log.info("A website restore was taken over by another worker")
    except Exception as exc:
        log.warning("A website restore could not be marked: %s", type(exc).__name__)
        return False
    log.info("Website restore: %s", done.error or "done")
    return True
