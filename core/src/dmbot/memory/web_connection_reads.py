"""The bot's side of a connection said in words from the website (#2940 slice C): a queued row
(`web_connection_reads`, migration 0632) is claimed in order and done with the calls Discord's
✍️ Correct it makes (`memory/connection_said.py`).

A row is one of three jobs. **read**: the DM's words are read once into proposals, which stay on the
row for the page to show; nothing is written. **use**: the DM pressed Use this on some of them; the
entries are checked again, and so is the connection being corrected (its hash must still be the
one the DM saw), then `connection_said.apply` writes it all in one change a single Undo takes back.
**undo**: that Undo. The door checked what it could in the database; the plan, the AI, who is still
a DM and everything that can have changed since is checked again here.

The DM's words sit in the row only while the read waits or runs: every end blanks them (and the
proposals) in the same UPDATE as the state, and the sweep deletes whatever is a day old, so a row
the bot never finished cannot keep them. Logs carry outcome words only, never a word of the DM's.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from dmbot.campaigns import Campaign
from dmbot.db import Database
from dmbot.memory import connection_said as said
from dmbot.memory import link_edits
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED
from dmbot.memory.store import MemoryStore

log = logging.getLogger(__name__)

RETAKE_AFTER_S = 600  # a claim this old belongs to a bot that stopped: the door's own limit
KEEP_FOR_S = 86400  # as long as the website reads an answer back

READ, USE, UNDO = "read", "use", "undo"

# What a failed job says, as the code the page turns into words.
NOT_YOURS = "not_yours"  # no longer a DM of the campaign
NOT_AVAILABLE = "not_available"  # the bot has no AI or no memory just now
NOT_ON_PLAN = "not_on_plan"
UNREADABLE = "unreadable"
NOTHING = "nothing"  # the words hold no connection between the two names
TOO_LONG = "too_long"
CHANGED = "changed"  # the connection being corrected changed since the DM saw it
GONE = "gone"  # a name is gone since
REFUSED = "refused"  # the store refused a write
FAILED = "failed"  # anything else, including a bug


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    campaign_id: str
    user_id: int
    first_id: str
    second_id: str
    replacing_id: str | None
    basis: str
    words: str
    job: str
    proposals: tuple[dict[str, Any], ...] = ()
    characters: tuple[str, ...] = ()
    picks: tuple[int, ...] = ()
    use_who: str = ""
    use_chars: tuple[str, ...] = ()
    batches: tuple[int, ...] = ()
    claimed_at: int = 0  # this worker's claim: only it may finish the row


@dataclass(frozen=True, slots=True)
class Outcome:
    error: str = ""
    proposals: list[dict[str, Any]] = field(default_factory=list)  # a read's
    who_kind: str = "everyone"
    characters: list[str] = field(default_factory=list)
    left_out: list[str] = field(default_factory=list)
    batches: list[int] = field(default_factory=list)  # a use's
    partly: bool = False  # an undo that could not take everything back


class Host(Protocol):
    """What the work needs from the running bot, so tests can stand in for Discord and the AI."""

    memory: MemoryStore | None
    connection_said_ai: Any

    async def get_campaign(self, guild_id: int, campaign_id: str) -> Campaign | None: ...

    async def plan_allows(
        self,
        action: Literal["ai"],
        guild_id: int,
        campaign: Campaign,
        *,
        fail_open: bool = True,
    ) -> bool: ...

    async def names_lookup(self, guild_id: int, campaign_id: str) -> CampaignLookup | None: ...


class ActionStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Item | None:
        """The oldest waiting job (or a stalled one) of this server, marked as being worked on,
        never past an earlier open one of the same campaign: the order made is the order done."""
        now_ms = int(self._now() * 1000)  # milliseconds, so two claims of one row never match
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "WITH pick AS (SELECT guild_id, id FROM web_connection_reads"
                " WHERE guild_id = %s AND (state = 'waiting'"
                "  OR (state = 'working' AND claimed_at < %s))"
                " AND NOT EXISTS (SELECT 1 FROM web_connection_reads p WHERE p.guild_id = %s"
                "  AND p.campaign_id = web_connection_reads.campaign_id"
                "  AND p.seq < web_connection_reads.seq"
                "  AND (p.state = 'waiting' OR (p.state = 'working' AND p.claimed_at >= %s)))"
                " ORDER BY seq LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " UPDATE web_connection_reads a SET state = 'working', claimed_at = %s,"
                " updated_at = %s FROM pick"
                " WHERE a.guild_id = pick.guild_id AND a.id = pick.id"
                " RETURNING a.id, a.campaign_id, a.user_id, a.first_id, a.second_id,"
                " a.replacing_id, a.basis, a.words, a.job, a.proposals, a.characters, a.picks,"
                " a.use_who, a.use_chars, a.batches",
                (
                    guild_id,
                    now_ms - RETAKE_AFTER_S * 1000,
                    guild_id,
                    now_ms - RETAKE_AFTER_S * 1000,
                    now_ms,
                    now,
                ),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Item(
            row["id"],
            row["campaign_id"],
            int(row["user_id"]),
            row["first_id"],
            row["second_id"],
            row["replacing_id"],
            row["basis"],
            row["words"],
            row["job"],
            tuple(row["proposals"]),
            tuple(row["characters"]),
            tuple(int(p) for p in row["picks"]),
            row["use_who"],
            tuple(row["use_chars"]),
            tuple(int(b) for b in row["batches"]),
            now_ms,
        )

    async def finish(self, guild_id: int, item: Item, outcome: Outcome) -> bool:
        """End the job and say how. Only the worker that holds the current claim can write; the
        words and proposals are blanked in the same statement (the table refuses a finished row
        that keeps them). False: not its row any more."""
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            if outcome.error and item.job == UNDO:
                # An undo that could not be done leaves the change as it was and tries again later:
                # the use stays done, with the reason, so Undo can be pressed once more.
                cur = await conn.execute(
                    "UPDATE web_connection_reads SET job = 'use', state = 'done', error = %s,"
                    " updated_at = %s"
                    " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                    (outcome.error[:40], now, guild_id, item.id, item.claimed_at),
                )
            elif outcome.error:
                cur = await conn.execute(
                    "UPDATE web_connection_reads SET state = 'failed', error = %s, words = '',"
                    " proposals = '[]'::jsonb, left_out = '{}', updated_at = %s"
                    " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                    (outcome.error[:40], now, guild_id, item.id, item.claimed_at),
                )
            elif item.job == READ:
                cur = await conn.execute(
                    "UPDATE web_connection_reads SET state = 'ready', words = '', error = '',"
                    " proposals = %s::jsonb, who_kind = %s, characters = %s, left_out = %s,"
                    " updated_at = %s"
                    " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                    (
                        _json(outcome.proposals),
                        outcome.who_kind,
                        outcome.characters,
                        outcome.left_out,
                        now,
                        guild_id,
                        item.id,
                        item.claimed_at,
                    ),
                )
            elif item.job == USE:
                cur = await conn.execute(
                    "UPDATE web_connection_reads SET state = 'done', batches = %s, error = '',"
                    " proposals = '[]'::jsonb, left_out = '{}', updated_at = %s"
                    " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                    (outcome.batches, now, guild_id, item.id, item.claimed_at),
                )
            else:
                cur = await conn.execute(
                    "UPDATE web_connection_reads SET state = 'undone', partly = %s, batches = '{}',"
                    " error = '', updated_at = %s"
                    " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                    (outcome.partly, now, guild_id, item.id, item.claimed_at),
                )
            return bool(cur.rowcount)

    async def basis_now(self, guild_id: int, item: Item) -> str | None:
        """The hash of the connection being corrected as it reads now, made the way the door made
        it (in SQL, so the two always agree). None: it is gone, or no longer confirmed and
        current."""
        if item.replacing_id is None:
            return ""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT md5(concat_ws('|', r.predicate, r.detail, r.subject_id, r.object_id,"
                " r.secret::TEXT, r.unrevealed::TEXT, r.directional::TEXT)) AS basis"
                " FROM memory_relations r WHERE r.guild_id = %s AND r.campaign_id = %s"
                " AND r.id = %s AND r.status = 'confirmed' AND r.to_session_at IS NULL",
                (guild_id, item.campaign_id, item.replacing_id),
            )
            row = await cur.fetchone()
        return None if row is None else str(row["basis"])

    async def sweep(self, guild_id: int) -> int:
        """Delete jobs older than `KEEP_FOR_S`, whatever their state: a row the bot never finished
        must not keep a DM's words past a day."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM web_connection_reads WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


def _json(rows: Sequence[dict[str, Any]]) -> str:
    import json

    return json.dumps(list(rows))


def proposal_row(p: said.Proposal) -> dict[str, Any]:
    """One proposal as it is kept on the row: fixed fields only (no quote of the DM's)."""
    return {
        "first": p.first,
        "second": p.second,
        "kind": p.kind,
        "words": p.words,
        "secret": p.secret,
    }


def proposal_of(row: dict[str, Any]) -> said.Proposal:
    return said.Proposal(
        str(row["first"]),
        str(row["second"]),
        str(row["kind"]),
        str(row["words"]),
        "",
        bool(row.get("secret", False)),
    )


async def _read(host: Host, store: ActionStore, guild_id: int, item: Item) -> Outcome:
    campaign = await host.get_campaign(guild_id, item.campaign_id)
    if campaign is None or item.user_id not in campaign.dm_user_ids:
        return Outcome(NOT_YOURS)  # (they may have handed over since)
    ai = host.connection_said_ai
    if ai is None or host.memory is None:
        return Outcome(NOT_AVAILABLE)
    if not await host.plan_allows("ai", guild_id, campaign, fail_open=False):
        return Outcome(NOT_ON_PLAN)
    names = await host.names_lookup(guild_id, item.campaign_id)
    if names is None:
        return Outcome(NOT_AVAILABLE)
    first, second = said.end_from(names, item.first_id), said.end_from(names, item.second_id)
    if first is None or second is None:
        return Outcome(GONE)
    if await store.basis_now(guild_id, item) != item.basis:
        return Outcome(CHANGED)
    got = await said.read(ai.complete, first, second, item.words)
    if got.problem and not got.proposals:
        if got.problem == said.UNREADABLE:
            return Outcome(UNREADABLE)
        return Outcome(TOO_LONG if got.problem == said.TOO_LONG else NOTHING)
    ids, left = said.characters_named(names, got.who.names)
    return Outcome(
        proposals=[proposal_row(p) for p in got.proposals],
        who_kind=got.who.kind if got.who.kind != said.SOME or ids else said.ONLY_DM,
        characters=ids,
        left_out=left[:10],
    )


async def _use(host: Host, store: ActionStore, guild_id: int, item: Item) -> Outcome:
    campaign = await host.get_campaign(guild_id, item.campaign_id)
    memory = host.memory
    if campaign is None or item.user_id not in campaign.dm_user_ids:
        return Outcome(NOT_YOURS)
    if memory is None:
        return Outcome(NOT_AVAILABLE)
    replacing = None
    if item.replacing_id is not None:
        if await store.basis_now(guild_id, item) != item.basis:
            return Outcome(CHANGED)  # not the connection the DM saw
        replacing = await memory.relation(guild_id, item.campaign_id, item.replacing_id)
        if replacing is None or replacing.status != CONFIRMED:
            return Outcome(CHANGED)
    chosen = [
        proposal_of(item.proposals[n - 1]) for n in item.picks if 1 <= n <= len(item.proposals)
    ]
    who = {
        "everyone": link_edits.EVERYONE,
        "dm": link_edits.ONLY_THE_DM,
        "some": link_edits.SOME_CHARACTERS,
        "keep": None,
    }.get(item.use_who, link_edits.EVERYONE)
    done = await said.apply(
        memory,
        guild_id,
        item.campaign_id,
        chosen,
        who=who,
        characters=item.use_chars if item.use_who == "some" else (),
        replacing=replacing,
    )
    if done.problem == said.CHANGED:
        return Outcome(CHANGED)
    if done.problem == said.GONE:
        return Outcome(GONE)
    if done.problem:
        return Outcome(REFUSED)
    return Outcome(batches=list(done.batches))


async def _undo(host: Host, guild_id: int, item: Item) -> Outcome:
    campaign = await host.get_campaign(guild_id, item.campaign_id)
    memory = host.memory
    if campaign is None or item.user_id not in campaign.dm_user_ids:
        return Outcome(NOT_YOURS)
    if memory is None:
        return Outcome(NOT_AVAILABLE)
    whole = await said.undo(memory, guild_id, item.campaign_id, said.Applied(item.batches))
    return Outcome(partly=not whole)


async def apply(host: Host, store: ActionStore, guild_id: int, item: Item) -> Outcome:
    """Do one claimed job. Never raises for a refusal."""
    if item.job == READ:
        return await _read(host, store, guild_id, item)
    if item.job == USE:
        return await _use(host, store, guild_id, item)
    return await _undo(host, guild_id, item)


async def run_one(host: Host, store: ActionStore, guild_id: int, item: Item) -> bool:
    """Do one claimed job and mark it. A bug never leaves the row "working": it ends as
    `failed`. False when the row could not be marked (the loop stops; the row is taken again
    later). Only the job and outcome words are logged."""
    try:
        outcome = await apply(host, store, guild_id, item)
    except Exception as exc:
        log.warning("A website connection %s failed: %s", item.job, type(exc).__name__)
        outcome = Outcome(FAILED)
    for attempt in (1, 2):  # one more try: a retake would do the job twice
        try:
            if not await store.finish(guild_id, item, outcome):
                log.info("A website connection job was taken over by another worker")
            break
        except Exception as exc:
            log.warning("A website connection job could not be marked: %s", type(exc).__name__)
            if attempt == 2:
                return False
    log.info("Website connection %s: %s", item.job, outcome.error or "done")
    return True
