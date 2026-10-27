"""The bot's side of the website's backup download (#2554, part 1b).

The website's role cannot read the tables a backup holds, so a request for a copy is a row
(`web_backups`) that this module claims one at a time and fills. The copy is built exactly as
Discord's Download builds it (`CampaignStore.export_set`, `encode_backup`: the main file and the
transcripts files, #2772), from the database alone, so
a campaign in a server DMbot cannot reach works. The file waits in the row for the person who
asked (at most 15 minutes, or until they fetch it).

Checked again when the bot builds it, because the queue-time check can be stale: the sender must
still be the campaign's owner or one of its game masters, and the campaign's owner's plan must
include copies (Try It has none; the bot owns the plan rules, `plan_allows`). A campaign too big for
a restore to take (`BackupTooBig`) fails with `too_big` and nothing is kept. The file is never
logged; only the outcome word is.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from dmbot.campaigns.models import Campaign
from dmbot.campaigns.parts import TOO_MANY_PARTS
from dmbot.campaigns.store import BackupTooBig, encode_backup
from dmbot.db import Database
from dmbot.plan_rules import Action
from dmbot.ui.logic import backup_filename

log = logging.getLogger(__name__)

PER_ROUND = 2  # copies built in one round for one server (each can be large)
RETAKE_AFTER_S = 300  # a claim this old belongs to a bot that stopped
KEEP_FOR_S = 900  # a request and its file go 15 minutes after it was made

NOT_FOUND = "not_found"  # the campaign is gone
NOT_ALLOWED = "not_allowed"  # the sender is not the owner or a game master any more
PLAN = "plan"  # the owner's plan has no copies (Try It)
TOO_BIG = "too_big"  # bigger than a restore takes
TOO_MANY_FILES = "too_many_files"  # more transcripts files than a restore has boxes for
FAILED = "failed"  # anything unexpected; asking again is fine


@dataclass(frozen=True, slots=True)
class Request:
    id: str
    campaign_id: str
    user_id: int
    claimed_at: int  # this worker's claim: only it may write


class Host(Protocol):
    """What building a copy needs from the running bot, so tests can stand in for it."""

    async def get_campaign(self, guild_id: int, campaign_id: str) -> Campaign | None: ...

    async def plan_allows(
        self, action: Action, guild_id: int, campaign: Campaign, *, fail_open: bool = True
    ) -> bool: ...

    async def export_campaign(self, guild_id: int, campaign_id: str) -> tuple[Any, list[bytes]]: ...


class WebBackups:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Request | None:
        """The oldest waiting request of this server (or a stalled one), marked as being worked
        on. Nothing is built for a request that is not marked first."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_backups SET state = 'working', stage = 'reading', claimed_at = %s,"
                " updated_at = %s WHERE (guild_id, id) = (SELECT guild_id, id FROM web_backups"
                " WHERE guild_id = %s AND (state = 'waiting' OR (state = 'working'"
                " AND claimed_at < %s)) AND created_at >= %s ORDER BY created_at LIMIT 1"
                " FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, user_id",
                (now, now, guild_id, now - RETAKE_AFTER_S, now - KEEP_FOR_S),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Request(row["id"], row["campaign_id"], int(row["user_id"]), now)

    async def stage(self, guild_id: int, item: Request, stage: str) -> None:
        """Moved on to the next step (for the page's progress)."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE web_backups SET stage = %s, updated_at = %s WHERE guild_id = %s"
                " AND id = %s AND state = 'working' AND claimed_at = %s",
                (stage, int(self._now()), guild_id, item.id, item.claimed_at),
            )

    async def done(
        self, guild_id: int, item: Request, raw: bytes, name: str, parts: Sequence[bytes] = ()
    ) -> bool:
        """The copy is ready: the main file and its transcripts files, together in one
        transaction, so the page never sees a copy that has only some of them."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_backups SET state = 'done', stage = 'done', file = %s, size = %s,"
                " file_name = %s, parts = %s, updated_at = %s WHERE guild_id = %s AND id = %s"
                " AND state = 'working' AND claimed_at = %s",
                (
                    raw, len(raw), name[:200], len(parts), int(self._now()), guild_id, item.id,
                    item.claimed_at,
                ),
            )  # fmt: skip
            if not cur.rowcount:
                return False
            for n, part in enumerate(parts, 1):
                await conn.execute(
                    "INSERT INTO web_backup_parts (guild_id, backup_id, n, data, size)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (guild_id, item.id, n, part, len(part)),
                )
            return True

    async def failed(self, guild_id: int, item: Request, error: str) -> bool:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_backups SET state = 'failed', stage = 'failed', error = %s,"
                " updated_at = %s WHERE guild_id = %s AND id = %s AND state = 'working'"
                " AND claimed_at = %s",
                (error, int(self._now()), guild_id, item.id, item.claimed_at),
            )
            return bool(cur.rowcount)

    async def sweep(self, guild_id: int) -> int:
        """Delete requests, and the files in them, older than `KEEP_FOR_S`."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM web_backups WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


async def build(host: Host, guild_id: int, item: Request, store: WebBackups) -> str:
    """Build one copy and store it, or fail it with a word. Returns the outcome word ("" when it
    worked). Never raises."""
    try:
        campaign = await host.get_campaign(guild_id, item.campaign_id)
        if campaign is None:
            await store.failed(guild_id, item, NOT_FOUND)
            return NOT_FOUND
        if item.user_id not in campaign.dm_user_ids and item.user_id != campaign.owner_user_id:
            await store.failed(guild_id, item, NOT_ALLOWED)
            return NOT_ALLOWED
        # A copy holds the campaign's secrets, so a plan that cannot be checked is a no.
        if not await host.plan_allows("copy", guild_id, campaign, fail_open=False):
            await store.failed(guild_id, item, PLAN)
            return PLAN
        try:
            data, parts = await host.export_campaign(guild_id, campaign.id)
            await store.stage(guild_id, item, "packing")
            raw = await asyncio.to_thread(encode_backup, data)
        except BackupTooBig as exc:  # a main file, a session, or a set a restore cannot take
            # Discord says which when it is the number of files; so does the page.
            why = TOO_MANY_FILES if str(exc) == TOO_MANY_PARTS else TOO_BIG
            await store.failed(guild_id, item, why)
            return why
        if not await store.done(
            guild_id, item, raw, backup_filename(campaign.name, int(time.time())), parts
        ):
            return FAILED  # not this worker's row any more
        return ""
    except Exception as exc:  # the type only: an error can quote a row
        log.warning("A website backup failed: %s", type(exc).__name__)
        try:
            await store.failed(guild_id, item, FAILED)
        except Exception:
            log.warning("A website backup could not be marked failed")
        return FAILED
