"""AI credits (#4005, docs/AI_CREDITS_DESIGN.md): what the AI calls made because a DM added or
processed material cost, counted in credits and kept per account for the plan month.

This part only measures. Nothing here refuses a job or shows a number to anyone yet; the estimate,
the "Go" button and the limit come in later changes.

How a call is charged: a job that prepares material (a lore upload, an import ...) opens
`preparing(...)` around its work. `ai.AnthropicClient.complete` then calls `record` after every
call that answers, and `record` finds the job in a ContextVar, so the many call sites inside a read
are not edited one by one. A call made outside any `preparing` block is free, which is the rule for
live play (the hours pay for it) by construction. The price is what the service reported for the
call, never the limit it was given: a cut answer is charged for the tokens it used.

Counts only, everywhere: the ledger, the log and the errors never hold a word of a prompt, a
reply, a name or a file name (CLAUDE.md, privacy).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from dmbot import entitlements, usage

if TYPE_CHECKING:
    from dmbot.ai import Reply
    from dmbot.db import Database

log = logging.getLogger(__name__)

# 1 credit = 1,000 millionths of a dollar (10 credits = $0.01). The one place the unit is set.
MICRO_PER_CREDIT = 1000
# A charge that takes longer than this is dropped with one warning: a slow database must never hold
# up an answer DMbot already paid for.
WRITE_TIMEOUT_S = 5.0

# Every job that prepares material and so must open `preparing`, as (module, function): a test
# reads each function's source and fails if it does not, so a new job that forgets the scope is
# not silently free (#4005, design section 7).
PREPARING_JOBS: tuple[tuple[str, str], ...] = (
    ("dmbot.ui.name_background", "read_background"),  # lore: Discord's button and the website
    ("dmbot.transcript.import_job", "ImportJob.run"),  # an imported session's cleaning
    ("dmbot.bot", "DMBot._extract_import"),  # an imported session's story read
    ("dmbot.bot", "DMBot._scan_import"),  # an imported session's name scan
)

OwnerOf = Callable[[int, str], Awaitable[int | None]]


def of_micro(micro_usd: int) -> int:
    """Credits for a cost in millionths of a dollar, rounded up so a counted call is never free."""
    return -(-max(0, micro_usd) // MICRO_PER_CREDIT)


@dataclass(slots=True)
class Scope:
    """The job a call belongs to. `owner` is looked up on the first charge and kept."""

    guild_id: int
    campaign_id: str
    job_kind: str
    owner: int | None = field(default=None)
    owner_known: bool = field(default=False)


_current: contextvars.ContextVar[Scope | None] = contextvars.ContextVar(
    "dmbot_credits_scope", default=None
)
_db: Database | None = None
_owner_of: OwnerOf | None = None
_clock: Callable[[], float] = time.time


def configure(
    db: Database | None, owner_of: OwnerOf | None, clock: Callable[[], float] = time.time
) -> None:
    """Called once at startup. `owner_of(guild_id, campaign_id)` is the campaign's owner (account
    number), or None. With no database, nothing is recorded."""
    global _db, _owner_of, _clock
    _db, _owner_of, _clock = db, owner_of, clock


@contextlib.contextmanager
def preparing(guild_id: int, campaign_id: str, job_kind: str) -> Iterator[Scope]:
    """Every AI call made inside this block is charged to this campaign's owner, as `job_kind`
    ("lore", "import", ...). A ContextVar is per task, so one job never leaks into another."""
    scope = Scope(guild_id, campaign_id, job_kind[:40])
    token = _current.set(scope)
    try:
        yield scope
    finally:
        _current.reset(token)


def current() -> Scope | None:
    """The job being charged right now, if any."""
    return _current.get()


async def record(reply: Reply, feature: str | None) -> int:
    """Charge one answered call to the job it belongs to. Returns the credits charged (0 outside a
    job). It never raises: a failed write is one counts-only warning, and the work goes on."""
    scope = _current.get()
    if scope is None or _db is None:
        return 0
    try:
        # (imported here: story_plan imports ai, which imports this module)
        from dmbot.memory.story_plan import micro_usd

        credits = of_micro(
            micro_usd(
                reply.model,
                reply.input_tokens,
                reply.output_tokens,
                reply.cache_read_tokens,
                reply.cache_write_tokens,
            )
        )
        if credits <= 0:
            return 0
        # Shielded, so a cancelled job does not lose the count half way, and bounded, so a hung
        # database costs one warning instead of stalling the answer.
        await asyncio.wait_for(
            asyncio.shield(_write(_db, scope, (feature or "-")[:40], credits)), WRITE_TIMEOUT_S
        )
    except Exception as exc:  # the meter must never fail the real work
        log.warning("AI credits: a charge could not be written (%s)", type(exc).__name__)
        return 0
    log.info("AI credits: job=%s feature=%s credits=%d", scope.job_kind, feature or "-", credits)
    return credits


async def _owner(scope: Scope) -> int | None:
    if not scope.owner_known:
        scope.owner = (
            await _owner_of(scope.guild_id, scope.campaign_id) if _owner_of is not None else None
        )
        scope.owner_known = True
    return scope.owner


async def _write(db: Database, scope: Scope, feature: str, credits: int) -> None:
    """The ledger row and, for a paid account, its month total, in one short transaction of their
    own, so a crash loses at most one call's count."""
    owner = await _owner(scope)
    now_ms = int(_clock() * 1000)
    row = (uuid.uuid4().hex, scope.guild_id, scope.campaign_id)
    sql = (
        "INSERT INTO ai_credit_spend (id, guild_id, campaign_id, owner_user_id, job_kind,"
        " feature, credits, month_start, at_ms) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    if owner is None:
        # Nobody owned the campaign yet: kept for the record, never added to anyone's month.
        month = usage.calendar_month(now_ms // 1000).start
        async with db.guild(scope.guild_id) as conn:
            await conn.execute(sql, (*row, 0, scope.job_kind, feature, credits, month, now_ms))
        return
    async with db.meter(scope.guild_id, owner) as conn:
        standing = await usage.standing_of(conn, owner, now_ms // 1000, read_used=False)
        month = standing.month.start
        await conn.execute(sql, (*row, owner, scope.job_kind, feature, credits, month, now_ms))
        if owner in entitlements.free_users() or await entitlements.on_free_guild(conn):
            # Free because of the person or the server: metered, but a paid owner's other
            # servers still draw on their own allowance (the rule #3792 gave hours).
            return
        # The row lock this upsert takes serialises two campaigns of one owner charging at once.
        await conn.execute(
            "INSERT INTO owner_credits (owner_user_id, month_start, credits) VALUES (%s, %s, %s)"
            " ON CONFLICT (owner_user_id, month_start)"
            " DO UPDATE SET credits = owner_credits.credits + EXCLUDED.credits",
            (owner, month, credits),
        )
