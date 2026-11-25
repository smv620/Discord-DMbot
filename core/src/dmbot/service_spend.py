"""What DMbot's paid calls cost, as business totals (#3672, the Costs page's part 2).

Each call to a paid service is added to an in-memory total as it answers (`record`, which never
waits, never raises and never holds a word of what was said), and one flush a minute adds the
totals to `service_spend` (0625): a row for each month, service, model, feature, plan and source.
The row carries no server, campaign or person. The server and campaign a call belongs to are
kept only in memory until the flush, to find the campaign owner's plan at that time (cached ten
minutes, so a call never waits for a plan read); a call with no campaign is plan `unknown`.

Counts only, everywhere: this module's table, log and errors hold no name, prompt or reply. A
failed write keeps its counts for the next flush and logs once an hour, by type only; a crash can
lose up to a minute of counts, which is fine for estimates and keeps recording off the call.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from dmbot import entitlements, logs

if TYPE_CHECKING:
    from dmbot.ai import Reply
    from dmbot.db import Database

log = logging.getLogger(__name__)

FLUSH_EVERY_S = 60
WRITE_TIMEOUT_S = 5.0  # the flush is bounded as a whole: a slow database never holds a call
PLAN_CACHE_S = 600
LOG_EVERY_S = 3600
UNKNOWN, FREE, LISTED = "unknown", "free", "listed"
PLAY, SAMPLE = "play", "sample"
SERVICES = ("anthropic", "gemini", "deepgram", "elevenlabs", "voyage")  # = the table's check
# Fixed words for the features that are not an AI `Feature` (the table keeps a word, never text).
TRANSCRIBE, RECAP_AUDIO, SEARCH = "transcribe", "recap_audio", "search"


class Notifier(Protocol):
    """Tells the admins once (`ai_watch.AIWatch.notify_once`)."""

    def notify_once(self, key: str, text: str) -> None: ...


OwnerOf = Callable[[int, str], Awaitable[int | None]]
CampaignOf = Callable[[int], str | None]  # the campaign a server is playing now, if any


@dataclass(frozen=True, slots=True)
class Totals:
    calls: int = 0
    units_in: int = 0
    units_out: int = 0
    micro_usd: int = 0
    refused: int = 0
    unpriced: int = 0

    def __add__(self, other: Totals) -> Totals:
        return Totals(
            self.calls + other.calls,
            self.units_in + other.units_in,
            self.units_out + other.units_out,
            self.micro_usd + other.micro_usd,
            self.refused + other.refused,
            self.unpriced + other.unpriced,
        )


# month, service, model, feature, source, server, campaign (the last two stay in memory)
_Pending = tuple[int, str, str, str, str, int | None, str | None]
_pending: dict[_Pending, Totals] = {}
_db: Database | None = None
_owner_of: OwnerOf | None = None
_campaign_of: CampaignOf | None = None
_notifier: Notifier | None = None
_budget_micro = 0  # the month's AI budget in millionths of a dollar; 0 is off (#5270)
_clock: Callable[[], float] = time.time
_source = PLAY
_plans: dict[tuple[int, str], tuple[str, float]] = {}
_flusher: asyncio.Task[None] | None = None
_flushing = asyncio.Lock()
_last_logged = 0.0


def configure(
    db: Database | None,
    owner_of: OwnerOf | None,
    clock: Callable[[], float] = time.time,
    *,
    source: str = PLAY,
    campaign_of: CampaignOf | None = None,
) -> None:
    """Called once at startup. `owner_of(guild_id, campaign_id)` is the campaign's owner (the
    same lookup the AI credits use), or None. With no database nothing is recorded. `source`
    `sample` for a sample run's process, so its spend is its own. `campaign_of(guild_id)` is the
    campaign a server is playing now, for a live call that knows its server and no campaign."""
    global _db, _owner_of, _clock, _source, _campaign_of
    _db, _owner_of, _clock, _source = db, owner_of, clock, source if source == SAMPLE else PLAY
    _campaign_of = campaign_of


def watch(notifier: Notifier | None, budget_usd: float = 0.0) -> None:
    """Who is told about the month's AI cost (#5270): at 80% and 100% of `budget_usd` (0 is off;
    nothing is ever stopped), and on the month's first refused call. Called once at startup."""
    global _notifier, _budget_micro
    _notifier, _budget_micro = notifier, round(max(0.0, budget_usd) * 1_000_000)


def _dollars(micro: int) -> str:
    return f"${micro / 1_000_000:,.2f}"


def month_start(now: float) -> int:
    """The first second of the calendar month (UTC) that `now` falls in."""
    moment = datetime.fromtimestamp(now, UTC)
    return int(datetime(moment.year, moment.month, 1, tzinfo=UTC).timestamp())


def record(
    service: str,
    model: str,
    feature: str,
    *,
    units_in: int = 0,
    units_out: int = 0,
    micro_usd: int = 0,
    refused: int = 0,
    unpriced: bool = False,
    guild_id: int | None = None,
) -> None:
    """Add one call to the totals. Never waits, never raises: the call's own result stands.
    `guild_id`: the server, for a live call that has it (Deepgram's clip) and no log context."""
    if _db is None:
        return
    try:
        if service not in SERVICES:
            return
        context_guild, campaign_id = logs.current_context()
        guild_id = guild_id if guild_id is not None else context_guild
        if campaign_id is None and guild_id is not None and _campaign_of is not None:
            campaign_id = _campaign_of(guild_id)
        key: _Pending = (
            month_start(_clock()),
            service,
            model[:60],
            feature[:40],
            _source,
            guild_id,
            campaign_id,
        )
        add = Totals(
            0 if refused else 1,
            max(0, units_in),
            max(0, units_out),
            max(0, micro_usd),
            refused,
            1 if unpriced and not refused else 0,
        )
        _pending[key] = _pending.get(key, Totals()) + add
        _ensure_flusher()
    except Exception as exc:  # recording must never fail the real work
        _warn("a call could not be recorded", exc)


def record_ai(reply: Reply, feature: str | None) -> None:
    """One AI call that answered (a cut or refused answer too: the service billed those tokens)."""
    try:
        # (imported here: `ai` imports this module)
        from dmbot.ai import PRICE_PER_MILLION
        from dmbot.memory.story_plan import micro_usd

        cost = micro_usd(
            reply.model,
            reply.input_tokens,
            reply.output_tokens,
            reply.cache_read_tokens,
            reply.cache_write_tokens,
        )
        record(
            "gemini" if reply.model.startswith("gemini") else "anthropic",
            reply.model or "unknown",
            feature or "-",
            # (a cache write is charged a quarter more than an input token and a read a tenth)
            units_in=reply.input_tokens
            + reply.cache_write_tokens * 5 // 4
            + reply.cache_read_tokens // 10,
            units_out=reply.output_tokens,
            micro_usd=cost,
            unpriced=reply.model not in PRICE_PER_MILLION,
        )
    except Exception as exc:
        _warn("an AI call could not be recorded", exc)


def record_refused(feature: str | None, model: str) -> None:
    """One AI call refused for limits or billing: no cost, but counted, so a night like the one
    the usage limit ran out shows which feature was refused."""
    record("anthropic", model or "unknown", feature or "-", refused=1)
    if _notifier is not None:
        # The first refusal of the month tells the admins at once, with the feature's fixed word
        # (never text) and, from the next flush, what the month's AI has cost (#5270).
        word = "".join(c for c in (feature or "-") if c.isalnum() or c in "_-")[:40]
        _notifier.notify_once(
            f"refused-{month_start(_clock())}",
            f"⚠️ A DMbot AI call was refused for limits or billing for the first time this month "
            f"(the job: {word}). Raise the limit at "
            "https://console.anthropic.com/settings/limits, or add credit at "
            "https://console.anthropic.com/settings/billing.",
        )


def _warn(what: str, exc: BaseException) -> None:
    """One counts-only warning an hour, type only."""
    global _last_logged
    now = time.monotonic()
    if now - _last_logged >= LOG_EVERY_S:
        _last_logged = now
        log.warning("Service spend: %s (%s)", what, type(exc).__name__)


def _ensure_flusher() -> None:
    global _flusher
    if _flusher is not None and not _flusher.done():
        return
    with contextlib.suppress(RuntimeError):  # (no running loop: the next call starts it)
        _flusher = asyncio.get_running_loop().create_task(_every_minute())


async def _every_minute() -> None:
    while True:
        await asyncio.sleep(FLUSH_EVERY_S)
        await flush()


async def plan_of(guild_id: int | None, campaign_id: str | None) -> str:
    """The campaign owner's plan now: a plan id, `free`, `listed` (a free-list person or server) or
    `unknown` (no campaign, or no owner). Cached for `PLAN_CACHE_S`."""
    if guild_id is None or campaign_id is None or _owner_of is None or _db is None:
        return UNKNOWN
    cached = _plans.get((guild_id, campaign_id))
    now = _clock()
    if cached is not None and now - cached[1] < PLAN_CACHE_S:
        return cached[0]
    plan = UNKNOWN
    try:
        owner = await _owner_of(guild_id, campaign_id)
        if owner is not None:
            if owner in entitlements.free_users() or guild_id in entitlements.free_guilds():
                plan = LISTED
            else:
                ent = await entitlements.get(_db, owner)
                plan = (
                    ent.plan
                    if ent is not None and entitlements.has_priced_plan(ent, int(now))
                    else FREE
                )
    except Exception as exc:  # the plan is a label: a failed read leaves `unknown`
        _warn("a plan could not be read", exc)
        return UNKNOWN
    _plans[(guild_id, campaign_id)] = (plan, now)
    return plan


async def flush() -> int:
    """Add everything waiting to the table; returns how many rows were written. Bounded as a whole;
    on a failure the counts are kept for the next flush. Never raises."""
    db = _db
    if db is None or not _pending:
        return 0
    async with _flushing:
        taken = dict(_pending)
        for key in taken:
            _pending.pop(key, None)
        try:
            written = await asyncio.wait_for(_write(db, taken), WRITE_TIMEOUT_S)
            await _check_budget(db)
            return written
        except BaseException as exc:
            for key, totals in taken.items():  # (kept: counts added since stay too)
                _pending[key] = _pending.get(key, Totals()) + totals
            if isinstance(exc, asyncio.CancelledError):
                raise
            _warn("a flush failed and is kept for the next", exc)
            return 0


async def month_ai_micro(db: Database, month: int) -> int:
    """What the month's real AI play has cost so far, in millionths of a dollar (a sample run's
    spend is its own and not counted)."""
    async with db.spend_writer() as conn:
        cur = await conn.execute(
            "SELECT COALESCE(sum(micro_usd), 0) AS n FROM service_spend WHERE month_start = %s"
            " AND service IN ('anthropic', 'gemini') AND source = 'play'",
            (month,),
        )
        row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def _check_budget(db: Database) -> None:
    """After a flush: tell the admins when the month's AI cost reaches 80% and 100% of the budget,
    each once a month. A failed read is a label problem only: the flush stands."""
    if _budget_micro <= 0 or _notifier is None:
        return
    try:
        month = month_start(_clock())
        spent = await month_ai_micro(db, month)
        for percent in (100, 80):
            if spent * 100 >= _budget_micro * percent:
                _notifier.notify_once(
                    f"budget-{percent}-{month}",
                    f"⚠️ DMbot's AI has cost {_dollars(spent)} so far this month: {percent}% of "
                    f"the {_dollars(_budget_micro)} budget you set (DMBOT_AI_MONTHLY_BUDGET_USD). "
                    "Nothing is stopped; this is a warning.",
                )
                break
    except Exception as exc:
        _warn("the budget could not be checked", exc)


async def _write(db: Database, taken: dict[_Pending, Totals]) -> int:
    rows: dict[tuple[int, str, str, str, str, str], Totals] = {}
    for (month, service, model, feature, source, guild_id, campaign_id), totals in taken.items():
        plan = await plan_of(guild_id, campaign_id)
        row = (month, service, model, feature, plan, source)
        rows[row] = rows.get(row, Totals()) + totals
    async with db.spend_writer() as conn:
        for (month, service, model, feature, plan, source), t in sorted(rows.items()):
            await conn.execute(
                "INSERT INTO service_spend (month_start, service, model, feature, plan, source,"
                " calls, units_in, units_out, micro_usd, refused, unpriced)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (month_start, service, model, feature, plan, source) DO UPDATE SET"
                " calls = service_spend.calls + EXCLUDED.calls,"
                " units_in = service_spend.units_in + EXCLUDED.units_in,"
                " units_out = service_spend.units_out + EXCLUDED.units_out,"
                " micro_usd = service_spend.micro_usd + EXCLUDED.micro_usd,"
                " refused = service_spend.refused + EXCLUDED.refused,"
                " unpriced = service_spend.unpriced + EXCLUDED.unpriced",
                (month, service, model, feature, plan, source, t.calls, t.units_in, t.units_out,
                 t.micro_usd, t.refused, t.unpriced),
            )  # fmt: skip
    return len(rows)


async def read_month(db: Database, month: int) -> list[dict[str, object]]:
    """Every total of one month for the admin page: the key words and the sums, nothing else."""
    async with db.spend_admin() as conn:
        cur = await conn.execute(
            "SELECT service, model, feature, plan, source, calls, units_in, units_out, micro_usd,"
            " refused, unpriced FROM service_spend WHERE month_start = %s"
            " ORDER BY micro_usd DESC, service, model, feature, plan, source",
            (month,),
        )
        return [dict(r) for r in await cur.fetchall()]


def reset_for_tests() -> None:
    """Forget everything kept in memory (tests only)."""
    global _flusher, _notifier, _budget_micro
    _notifier, _budget_micro = None, 0
    _pending.clear()
    _plans.clear()
    if _flusher is not None:
        _flusher.cancel()
        _flusher = None


__all__ = ["Totals", "configure", "flush", "month_start", "plan_of", "read_month", "record"]
