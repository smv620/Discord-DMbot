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

Two safety rules from review: a copy that is already in is never reported as failed because the
tidying after it failed (a second try would make a second campaign), and a row a stopped bot left
`working` is ended as failed, never taken up again (it may be in already, and a copy that took the
bot down would loop). Memory: the copy and its files are read whole, as Discord's restore holds
them: at most 25 MB for the copy and 24 files of 25 MB each, about 625 MB at the ceiling.
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
from dmbot.campaigns.parts import DAMAGED as PART_DAMAGED
from dmbot.campaigns.parts import TOO_BIG_PART, LeftOut
from dmbot.campaigns.store import (
    DAMAGED,
    DAMAGED_CONFIRMATION,
    DAMAGED_DM,
    DAMAGED_OPTIONAL_RULE,
    NO_ROOM_TO_RESTORE,
    NOT_A_BACKUP,
    NOT_HERE,
    NOT_THIS_DM,
    PartsError,
    decode_backup,
)
from dmbot.campaigns.store import TOO_BIG as FILE_TOO_BIG
from dmbot.db import Conn, Database
from dmbot.ui import logic

log = logging.getLogger(__name__)

PER_ROUND = 1  # restores applied in one round for one server (each can be large)
STALLED_AFTER_S = 300  # a working row not moving for this long belongs to a bot that stopped
FINISH_TRIES = 3  # writing the outcome: a database blip must not leave a finished copy "working"
FINISH_PAUSE_S = 1.0
KEEP_FOR_S = 86400  # a row and its files go 24 hours after it began

PLAN = "plan"  # the plan has no copies, or no room for another campaign
NOT_ALLOWED = "not_allowed"  # not in the server any more, or not a DM of the campaign to replace
NOT_FOUND = "not_found"  # the campaign to replace is gone
PLAYING = "playing"  # the campaign to replace is being played
NOT_A_BACKUP_WORD = "not_a_backup"
TOO_BIG = "too_big"
PARTS = "parts"  # the transcripts files are not exactly the set this copy came with
FAILED = "failed"  # anything unexpected; trying again is fine

# The store's refusals (its constants, not their wording), as the word the page has words for.
_BY_MESSAGE = {
    NO_ROOM_TO_RESTORE: PLAN,
    NOT_HERE: NOT_FOUND,
    NOT_THIS_DM: NOT_ALLOWED,
    FILE_TOO_BIG: TOO_BIG,
    TOO_BIG_PART: TOO_BIG,
    NOT_A_BACKUP: NOT_A_BACKUP_WORD,
    DAMAGED: NOT_A_BACKUP_WORD,
    DAMAGED_DM: NOT_A_BACKUP_WORD,
    DAMAGED_CONFIRMATION: NOT_A_BACKUP_WORD,
    DAMAGED_OPTIONAL_RULE: NOT_A_BACKUP_WORD,
    PART_DAMAGED: NOT_A_BACKUP_WORD,
}


def refusal_word(error: CampaignError) -> str:
    """The page's word for a refusal the store worded for Discord."""
    if isinstance(error, PartsError):
        return PARTS
    return _BY_MESSAGE.get(str(error), FAILED)


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

    async def restore_copy(
        self,
        guild_id: int,
        data: object,
        importer_id: int,
        *,
        replace_campaign_id: str | None,
        parts: list[bytes],
        left_out: LeftOut,
        progress: Callable[[int, int], Awaitable[None]],
        in_transaction: Callable[[Conn, str], Awaitable[None]],
    ) -> Campaign: ...

    async def after_restore(self, guild_id: int, campaign_id: str, replaced: bool) -> None:
        """The planner's statistics (a big restore adds thousands of rows) and, for a replaced
        campaign, its rules channel."""
        ...


class WebRestores:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock
        # When the worker last showed a sign of life: each round and each progress call. The
        # keep-alive for the queue trusts it, so a hung worker lets the queue show as stalled.
        self._beat: float | None = None

    def beat(self) -> None:
        """The worker is alive: a round began or a restore moved forward."""
        self._beat = self._now()

    async def fail_stalled(self, guild_id: int) -> int:
        """End the rows a stopped bot left `working`, as failed (the page offers another try). Not
        taken up again: the copy may already be in, and applying it twice would make two
        campaigns; a copy that took the bot down would loop. Only called by the one worker that
        runs restores, between its own restores, so it never ends a row it is working on."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_restores SET state = 'failed', stage = 'failed', error = 'failed',"
                " updated_at = %s WHERE guild_id = %s AND state = 'working' AND updated_at < %s",
                (now, guild_id, now - STALLED_AFTER_S),
            )
            return int(cur.rowcount)

    async def claim_next(self, guild_id: int) -> Restore | None:
        """The oldest waiting restore of this server, marked as being read. Rows are in seconds,
        as the website writes them."""
        self.beat()
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_restores SET state = 'working', stage = 'restore_reading',"
                " claimed_at = %s, updated_at = %s, done_parts = 0"
                " WHERE (guild_id, id) = (SELECT guild_id, id FROM web_restores"
                " WHERE guild_id = %s AND state = 'waiting' ORDER BY created_at LIMIT 1"
                " FOR UPDATE SKIP LOCKED)"
                " RETURNING id, user_id, replace_id, parts_wanted, without_parts",
                (now, now, guild_id),
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
        self.beat()
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE web_restores SET updated_at = %s,"
                " stage = COALESCE(%s, stage), done_parts = COALESCE(%s, done_parts)"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (int(self._now()), stage, done_parts, guild_id, item.id, item.claimed_at),
            )

    async def mark(self, conn: Conn, guild_id: int, item: Restore, outcome: Outcome) -> bool:
        """Done, or failed with why, on this connection. The table's trigger empties the copy and
        its files. Only the worker holding the current claim writes."""
        state = "failed" if outcome.error else "done"
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

    async def finish(self, guild_id: int, item: Restore, outcome: Outcome) -> bool:
        """`mark` on its own connection. False: not its row any more, or already ended (a copy
        that is in marks its own row done in the same transaction as the copy)."""
        async with self._db.guild(guild_id) as conn:
            return await self.mark(conn, guild_id, item, outcome)

    async def guilds(self) -> list[int]:
        """Every server that has rows, the ones the bot has left included: their rows hold whole
        campaigns and must still go. Server ids only."""
        async with self._db.unscoped() as conn:
            cur = await conn.execute("SELECT guild_id FROM web_restore_guilds ORDER BY guild_id")
            return [int(r["guild_id"]) for r in await cur.fetchall()]

    async def forget_if_empty(self, guild_id: int) -> None:
        """Take a server off the list once none of its rows is left. The routing row is locked
        first, then the check and the delete run as a second statement that sees every row
        committed by then: a restore being staged takes the same row (its trigger upserts it), so
        it either waits for this to finish and puts the row back, or committed first and is seen."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM web_restore_guilds WHERE guild_id = %s FOR UPDATE", (guild_id,)
            )
            if await cur.fetchone() is None:
                return
            await conn.execute(
                "DELETE FROM web_restore_guilds g WHERE g.guild_id = %s AND NOT EXISTS"
                " (SELECT 1 FROM web_restores r WHERE r.guild_id = g.guild_id)",
                (guild_id,),
            )

    async def touch_waiting(self, guild_id: int) -> int:
        """Show the rows still waiting their turn as alive (one restore runs at a time, so the
        page's two-minute stall for `restore_waiting` would otherwise fire on a queue)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_restores SET updated_at = %s WHERE guild_id = %s AND state = 'waiting'",
                (int(self._now()), guild_id),
            )
            return int(cur.rowcount)

    async def keep_waiting_alive(self) -> None:
        """`touch_waiting` for every server with rows. Run on its own, apart from the restores,
        so a queue behind a long restore keeps moving; a server it cannot touch is skipped. Only
        while the worker's own heartbeat is fresh: a hung worker must let the queue show as
        stalled, not hide it."""
        if self._beat is None or self._now() - self._beat >= STALLED_AFTER_S:
            return
        for guild_id in await self.guilds():
            try:
                if await self.has_left(guild_id):
                    continue  # (upkeep ends these rows; they are not waiting for a turn)
                await self.touch_waiting(guild_id)
            except Exception as exc:  # the type only
                log.warning("Website restore queue touch: %s", type(exc).__name__)

    async def has_left(self, guild_id: int) -> bool:
        """Whether DMbot has left this server, from the database and never from the bot's cache
        (which is empty until Discord has sent the server list): the install record says it left,
        the same rule the website uses to accept a restore (#2750). A server with no record that
        has no campaign either is not DMbot's, so nothing could ever apply its rows."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT EXISTS (SELECT 1 FROM installs WHERE guild_id = %s AND left_at IS NULL)"
                " AS installed,"
                " EXISTS (SELECT 1 FROM installs WHERE guild_id = %s) AS recorded,"
                " EXISTS (SELECT 1 FROM campaigns WHERE guild_id = %s) AS played",
                (guild_id, guild_id, guild_id),
            )
            here = await cur.fetchone() or {}
        return not (here.get("installed") or (here.get("played") and not here.get("recorded")))

    async def fail_not_here(self, guild_id: int) -> int:
        """End, as not allowed, the rows of a server the bot is not in: nothing can apply them,
        and the person is `busy` everywhere while they stand."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_restores SET state = 'failed', stage = 'failed', error = %s,"
                " updated_at = %s WHERE guild_id = %s"
                " AND state IN ('receiving', 'waiting', 'working')",
                (NOT_ALLOWED, now, guild_id),
            )
            return int(cur.rowcount)

    async def sweep(self, guild_id: int) -> int:
        """Delete ended rows 24 hours after they began, and the files with them. A row still
        `working` is `fail_stalled`'s, never deleted under the worker."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM web_restores WHERE guild_id = %s AND created_at < %s"
                " AND state <> 'working'",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


async def tidy(store: WebRestores) -> None:
    """The upkeep of the rows, for every server that has any: end what a stopped bot left, end the
    rows of a server DMbot has left (the install record says so; the bot's server cache is never
    asked), and delete what is a day old. Each step has its own `try`, so one failing never skips
    the rest. Keeping a queue alive is `keep_waiting_alive`, a task of its own."""
    for guild_id in await store.guilds():
        try:
            gone = await store.has_left(guild_id)
        except Exception as exc:  # the type only: a database error can quote a row
            log.warning("Website restore upkeep (has_left): %s", type(exc).__name__)
            continue  # not known: nothing is ended on a guess
        steps = (
            (store.fail_not_here, store.sweep, store.forget_if_empty)
            if gone
            else (store.fail_stalled, store.sweep, store.forget_if_empty)
        )
        for step in steps:
            try:
                await step(guild_id)
            except Exception as exc:
                log.warning("Website restore upkeep (%s): %s", step.__name__, type(exc).__name__)


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
        return Outcome(refusal_word(exc))
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

        async def record(conn: Conn, campaign_id: str) -> None:
            """The row is marked done on the restore's own connection, so the copy and its
            outcome commit together: a crash can never leave a copy that is in as a row that
            reads failed (a second try would make a second campaign)."""
            await store.mark(
                conn, guild_id, item, Outcome(result_id=campaign_id, left_out=erased.lines)
            )

        try:
            campaign = await host.restore_copy(
                guild_id,
                data,
                item.user_id,
                replace_campaign_id=item.replace_id,
                parts=parts,
                left_out=erased,
                progress=progress,
                in_transaction=record,
            )
        except CampaignError as exc:
            return Outcome(refusal_word(exc))
    # The copy is in. What follows (the planner's statistics, the rules channel) is tidying: if it
    # fails the restore still happened, and saying "failed" would invite a second copy.
    try:
        await host.after_restore(guild_id, campaign.id, item.replace_id is not None)
    except Exception as exc:
        log.warning("After a website restore: %s", type(exc).__name__)
    return Outcome(result_id=campaign.id, left_out=erased.lines)


async def run_one(host: Host, store: WebRestores, guild_id: int, item: Restore) -> bool:
    """Apply one claimed restore and mark it. A bug never leaves the row "working": it ends as
    `failed` for the page to offer another try. A copy that is in has marked its own row done in
    the same transaction, so the mark here finds it already ended. False when the row could not
    be marked (the loop stops; a row left `working` is failed by upkeep, never retaken). Only the
    outcome word is logged, never the copy."""
    try:
        done = await apply(host, store, guild_id, item)
    except Exception as exc:
        log.warning("A website restore failed: %s", type(exc).__name__)
        done = Outcome(FAILED)
    marked: bool | None = None
    for attempt in range(FINISH_TRIES):
        try:
            marked = await store.finish(guild_id, item, done)
            break
        except Exception as exc:
            log.warning("A website restore could not be marked: %s", type(exc).__name__)
            if attempt + 1 < FINISH_TRIES:
                await asyncio.sleep(FINISH_PAUSE_S)
    if marked is None:
        return False
    if not marked and done.error:
        log.info("A website restore's row had already ended")
    log.info("Website restore: %s", done.error or "done")
    return True
