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
import math
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from fractions import Fraction
from typing import TYPE_CHECKING

from psycopg import errors as pg_errors

from dmbot import usage
from dmbot.plans import PlanId

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

# ---- the estimate (#4005, step 2) ----------------------------------------------------------
# What a job will cost, told to the DM before it starts. Pure: no database, no AI.
#
# `CREDITS_PER_UNIT` is a deliberately HIGH first guess, one table in one place, so an estimate
# comes out above the real charge and never below it. It is tuned from the ledger's own numbers (the
# estimate and the charge are logged side by side, counts only), like the AI answer limits were
# (#2596). A unit is what the DM gave: a thousand words of lore or of an adventure, a line of an
# imported session (its count is `transcript lines`), a session for a script.
CREDITS_PER_UNIT: dict[str, int] = {
    "lore": 40,  # per 1,000 words
    "adventure": 60,  # per 1,000 words (a careful read, so dearer than names)
    "import": 4,  # per line of the imported session
    "script": 120,  # per script (one imported session)
}
# The words the DM sees for what is counted, by job kind.
UNIT_WORDS: dict[str, str] = {
    "lore": "for the lore",
    "adventure": "for the adventure",
    "import": "for the session",
    "script": "for the script",
}
# Below this the DM is not asked (a few credits is not worth a question).
ESTIMATE_BAR = 20
# The monthly allowance, in credits, by plan id (`plans.PlanId`). HIGH ON PURPOSE until the owner
# sets the numbers (#4005, "Owner, what this needs from you"): nothing is refused yet, and a
# number that is too small would be a refusal.
CREDITS_PER_MONTH: dict[PlanId, int] = {
    "try-it": 10_000_000,
    "table": 10_000_000,
    "two-tables": 10_000_000,
    "guild": 10_000_000,
    "pro": 10_000_000,
}


# False until the owner has set the allowances above: a made-up number must not be shown to a DM, so
# the "You have N left" sentence is left out while it is False (the first card is `estimate_words`).
ALLOWANCES_SET = False


@dataclass(frozen=True, slots=True)
class Estimate:
    credits: int  # what the job is expected to cost, never below the real charge
    left: int  # what the month has left before it (never below 0)
    ask: bool  # worth a question (over the bar)
    enough: bool  # the month has at least that much left


def units_of(job_kind: str, *, words: int = 0, lines: int = 0, items: int = 1) -> float:
    """The units a job is counted in, from what the DM gave: thousands of words for lore and an
    adventure, lines for an imported session, one for each script and for any other kind."""
    if job_kind in ("lore", "adventure"):
        return max(0, words) / 1000
    if job_kind == "import":
        return float(max(0, lines))
    return float(max(0, items))


def estimate(job_kind: str, units: float, *, allowance: int, used: int) -> Estimate:
    """What a job costs, and where the month stands, in credits. Worked out exactly (never lower
    than the price times the units) and rounded up, and a job kind with no price is priced like the
    dearest, so an estimate is never low. A count that is not a number, or is negative, is 0."""
    per_unit = CREDITS_PER_UNIT.get(job_kind, max(CREDITS_PER_UNIT.values()))
    # (from the number as written, 0.2 and not its binary neighbour, so 0.2 units of 40 is 8)
    count = Fraction(str(units)) if math.isfinite(units) and units > 0 else Fraction(0)
    credits = math.ceil(count * per_unit)
    left = max(0, allowance - max(0, used))
    return Estimate(credits, left, credits >= ESTIMATE_BAR, credits <= left)


def lore_estimate(chars: int, *, allowance: int = 0, used: int = 0) -> Estimate:
    """What reading a lore text of `chars` characters will cost. It uses the model-priced figure the
    lore card already worked out (`memory.background.estimate_cents`: the text goes in three times,
    at the list prices, with a margin), which is never low, rather than a per-word guess. One cent
    is 10 credits (a credit is a thousandth of a dollar)."""
    # (imported here: memory.background imports ai, which imports this module)
    from dmbot.memory.background import estimate_cents

    credits = estimate_cents(max(0, chars)) * (MICRO_PER_CREDIT // 100)
    left = max(0, allowance - max(0, used))
    return Estimate(credits, left, credits >= ESTIMATE_BAR, credits <= left)


def estimate_words(job_kind: str, est: Estimate) -> str:
    """The sentence on the card, plain words: "About 120 AI credits for the script. You have 9,800
    left this month." The second sentence waits until the owner has set the allowances. Counts
    only. The caller skips the card when `est.ask` is False."""
    what = UNIT_WORDS.get(job_kind, "")
    unit = "credit" if est.credits == 1 else "credits"
    words = f"About {est.credits:,} AI {unit}{(' ' + what) if what else ''}."
    if ALLOWANCES_SET:
        words += f" You have {est.left:,} left this month."
    return words


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


@dataclass(frozen=True, slots=True)
class Charge:
    """One call's cost, ready to write. Its id is made once, so writing it again (after a timeout
    that may or may not have landed) can never count it twice."""

    spend_id: str
    guild_id: int
    campaign_id: str
    job_kind: str
    feature: str
    credits: int
    now_ms: int
    scope: Scope  # (the job: it keeps who owns the campaign, found once, inside the bounded write)


# Charges that could not be written, tried again before the next charge (a dropped count must not
# silently undercount, #4094). Bounded: past the limit the oldest is dropped and counted in `lost`.
MAX_WAITING = 200
_waiting: deque[Charge] = deque()
lost = 0  # charges dropped for good since the bot started (a count, shown in the log)


def waiting() -> int:
    """How many charges are waiting to be written again."""
    return len(_waiting)


async def record(reply: Reply, feature: str | None) -> int:
    """Charge one answered call to the job it belongs to. Returns the credits charged (0 outside a
    job, or when the write is waiting for a retry). It never raises: a failed write is one
    counts-only warning, kept and tried again, and the work goes on."""
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
        charge = Charge(
            uuid.uuid4().hex,
            scope.guild_id,
            scope.campaign_id,
            scope.job_kind,
            (feature or "-")[:40],
            credits,
            int(_clock() * 1000),
            scope,
        )
        # Kept before anything is awaited, so a cancelled job cannot lose it; the flush writes
        # everything waiting in order, this charge last, and is bounded as a whole.
        _keep(charge)
        await _flush(_db)
        if any(c is charge for c in _waiting):
            return 0  # still waiting for a retry
    except Exception as exc:  # the meter must never fail the real work
        log.warning("AI credits: a charge could not be made (%s)", type(exc).__name__)
        return 0
    log.info("AI credits: job=%s feature=%s credits=%d", scope.job_kind, feature or "-", credits)
    return credits


_PERMANENT = (pg_errors.IntegrityError, pg_errors.DataError)  # a retry can never succeed


async def _attempt(db: Database, charge: Charge) -> str:
    """Write one charge. Shielded, so a cancelled job does not lose it half way, and bounded, so a
    hung database costs one warning instead of stalling the answer. "ok", "retry" (the database or
    the network: try again later) or "drop" (it can never be written: the campaign is gone or was
    moved, a value is out of range). A write that timed out may still land; the id makes a second
    write of it a no-op, and the flush stops at the first failure, so these do not pile up."""
    try:
        await asyncio.wait_for(asyncio.shield(_write(db, charge)), WRITE_TIMEOUT_S)
    except _PERMANENT as exc:
        log.error("AI credits: a charge can never be written (%s)", type(exc).__name__)
        return "drop"
    except Exception as exc:
        log.warning("AI credits: a charge could not be written (%s)", type(exc).__name__)
        return "retry"
    return "ok"


def _keep(charge: Charge) -> None:
    global lost
    if len(_waiting) >= MAX_WAITING:
        dropped = _waiting.popleft()
        lost += 1
        log.error(
            "AI credits: a charge of %d credits was dropped, %d lost since the start",
            dropped.credits,
            lost,
        )
    _waiting.append(charge)


async def _flush(db: Database) -> None:
    """Write the waiting charges, oldest first, within one deadline for the whole flush. A charge is
    taken off the list before it is written, so two calls flushing at once never write or drop the
    same one. One that fails for a lasting reason is dropped and counted, so it cannot block the
    ones behind it; one that fails for the moment goes back to the front and stops the flush (the
    database is down: the next charge tries again)."""
    global lost
    deadline = time.monotonic() + WRITE_TIMEOUT_S
    while _waiting and time.monotonic() < deadline:
        charge = _waiting.popleft()
        try:
            outcome = await _attempt(db, charge)
        except BaseException:  # (cancelled: it goes back, and the write is a no-op if it landed)
            _waiting.appendleft(charge)
            raise
        if outcome == "retry":
            _waiting.appendleft(charge)
            return
        if outcome == "drop":
            lost += 1
            log.error(
                "AI credits: a charge of %d credits was dropped, %d lost since the start",
                charge.credits,
                lost,
            )


async def _owner(scope: Scope) -> int | None:
    if not scope.owner_known:
        scope.owner = (
            await _owner_of(scope.guild_id, scope.campaign_id) if _owner_of is not None else None
        )
        scope.owner_known = True
    return scope.owner


async def _write(db: Database, charge: Charge) -> None:
    """The ledger row and, for a paid account, its month total, in one short transaction of their
    own, so a crash loses at most one call's count. Writing the same charge again does nothing."""
    owner = await _owner(charge.scope)
    if owner is None:
        # Nobody owned the campaign yet: kept for the record, never added to anyone's month.
        month = usage.calendar_month(charge.now_ms // 1000).start
        async with db.guild(charge.guild_id) as conn:
            await conn.execute(
                "INSERT INTO ai_credit_spend (id, guild_id, campaign_id, owner_user_id, job_kind,"
                " feature, credits, month_start, at_ms) VALUES (%s, %s, %s, 0, %s, %s, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, id) DO NOTHING",
                (
                    charge.spend_id,
                    charge.guild_id,
                    charge.campaign_id,
                    charge.job_kind,
                    charge.feature,
                    charge.credits,
                    month,
                    charge.now_ms,
                ),
            )
        return
    await usage.add_credits(
        db,
        guild_id=charge.guild_id,
        campaign_id=charge.campaign_id,
        owner_user_id=owner,
        spend_id=charge.spend_id,
        job_kind=charge.job_kind,
        feature=charge.feature,
        credits=charge.credits,
        now_ms=charge.now_ms,
    )
