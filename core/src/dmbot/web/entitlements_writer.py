"""The only code that changes a person's plan (#435): payment events and Try It.

Every change runs inside Database.plan_writer(), which the database requires for any
write to `entitlements`, `payment_events` and `try_it_used`.

Payment events:
- **Once only.** The event id is recorded in the same transaction; a repeat changes
  nothing.
- **Never backwards.** An event older than the newest one applied is recorded but changes
  nothing, because the payment company may deliver events out of order.
- **Only known plans.** The plan id is checked against plans.json, and the caps are copied
  from it, never taken from the event.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from dmbot import plans
from dmbot.db import Conn, Database
from dmbot.web.payments import PaymentEvent

log = logging.getLogger(__name__)

Outcome = Literal["applied", "duplicate", "stale", "ignored"]
TRY_IT_PROVIDER = "dmbot"


@dataclass(frozen=True)
class TryItResult:
    started: bool
    reason: Literal["started", "used", "has_plan"]


async def _current(conn: Conn, user_id: int) -> dict[str, Any] | None:
    cur = await conn.execute("SELECT * FROM entitlements WHERE user_id = %s FOR UPDATE", (user_id,))
    return await cur.fetchone()


async def _known_person(conn: Conn, user_id: int) -> bool:
    cur = await conn.execute("SELECT 1 FROM web_users WHERE user_id = %s", (user_id,))
    return await cur.fetchone() is not None


async def apply_event(db: Database, event: PaymentEvent, *, now: int) -> Outcome:
    loaded = plans.load()
    async with db.plan_writer(event.user_id) as conn:
        if not await _known_person(conn, event.user_id):
            # Not someone who signed in here (or they deleted their account): nothing to
            # attach the plan to. The company still has the payment; ids are logged.
            log.warning(
                "Payment event %s for unknown user %s ignored", event.event_id, event.user_id
            )
            return "ignored"
        cur = await conn.execute(
            "INSERT INTO payment_events (provider, event_id, user_id, received_at)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING event_id",
            (event.provider, event.event_id, event.user_id, now),
        )
        if await cur.fetchone() is None:
            return "duplicate"
        row = await _current(conn, event.user_id)
        if row is not None and event.occurred_at < row["last_event_at"]:
            return "stale"

        if event.kind in ("subscription_started", "subscription_renewed"):
            plan = loaded.get(event.plan or "")
            if (
                plan is None
                or not plan.price_cents
                or event.period_start is None
                or event.period_end is None
                or event.period_end <= event.period_start
            ):
                log.warning("Payment event %s has no usable plan or period", event.event_id)
                return "ignored"
            changed = row is None or row["plan"] != plan.id
            new_period = row is None or row["period_start"] != event.period_start
            await conn.execute(
                "INSERT INTO entitlements (user_id, plan, status, hours_cap, extra_hours,"
                " campaign_cap, period_start, period_end, grace_ends_at, lapsed_at,"
                " plan_changed_at, provider, provider_customer_id, provider_subscription_id,"
                " last_event_at, updated_at)"
                " VALUES (%(user)s, %(plan)s, 'active', %(hours)s, 0, %(campaigns)s,"
                " %(start)s, %(end)s, NULL, NULL, %(at)s, %(provider)s, %(customer)s,"
                " %(subscription)s, %(at)s, %(now)s)"
                " ON CONFLICT (user_id) DO UPDATE SET plan = EXCLUDED.plan,"
                " status = 'active', hours_cap = EXCLUDED.hours_cap,"
                " extra_hours = CASE WHEN %(new_period)s THEN 0"
                "   ELSE entitlements.extra_hours END,"
                " campaign_cap = EXCLUDED.campaign_cap, period_start = EXCLUDED.period_start,"
                " period_end = EXCLUDED.period_end, grace_ends_at = NULL, lapsed_at = NULL,"
                " plan_changed_at = CASE WHEN %(changed)s THEN EXCLUDED.plan_changed_at"
                "   ELSE entitlements.plan_changed_at END,"
                " provider = EXCLUDED.provider,"
                " provider_customer_id = COALESCE(EXCLUDED.provider_customer_id,"
                "   entitlements.provider_customer_id),"
                " provider_subscription_id = COALESCE(EXCLUDED.provider_subscription_id,"
                "   entitlements.provider_subscription_id),"
                " last_event_at = EXCLUDED.last_event_at, updated_at = EXCLUDED.updated_at",
                {
                    "user": event.user_id,
                    "plan": plan.id,
                    "hours": plan.hours_per_month,
                    "campaigns": plan.campaigns,
                    "start": event.period_start,
                    "end": event.period_end,
                    "at": event.occurred_at,
                    "provider": event.provider,
                    "customer": event.customer_id,
                    "subscription": event.subscription_id,
                    "now": now,
                    "new_period": new_period,
                    "changed": changed,
                },
            )
            return "applied"

        if row is None:
            log.warning("Payment event %s for a person with no plan ignored", event.event_id)
            return "ignored"

        if event.kind == "payment_failed":
            if row["status"] == "lapsed":
                return "ignored"
            grace_ends = row["grace_ends_at"] or (
                event.occurred_at + loaded.payment_grace_days * 86400
            )
            await conn.execute(
                "UPDATE entitlements SET status = 'grace', grace_ends_at = %s,"
                " last_event_at = %s, updated_at = %s WHERE user_id = %s",
                (grace_ends, event.occurred_at, now, event.user_id),
            )
        elif event.kind == "subscription_ended":
            await conn.execute(
                "UPDATE entitlements SET status = 'lapsed', grace_ends_at = NULL,"
                " lapsed_at = %s, last_event_at = %s, updated_at = %s WHERE user_id = %s",
                (event.occurred_at, event.occurred_at, now, event.user_id),
            )
        elif event.kind == "extra_hours_bought":
            await conn.execute(
                "UPDATE entitlements SET extra_hours = extra_hours + %s,"
                " last_event_at = %s, updated_at = %s WHERE user_id = %s",
                (loaded.extra_hours, event.occurred_at, now, event.user_id),
            )
        return "applied"


async def start_try_it(db: Database, user_id: int, *, now: int) -> TryItResult:
    """Start the free Try It plan: once per Discord user, and not over a working plan."""
    loaded = plans.load()
    try_it = loaded.by_id["try-it"]
    days = try_it.trial_days or 30
    async with db.plan_writer(user_id) as conn:
        row = await _current(conn, user_id)
        if row is not None and row["status"] != "lapsed":
            return TryItResult(False, "has_plan")
        cur = await conn.execute(
            "INSERT INTO try_it_used (user_id, used_at) VALUES (%s, %s)"
            " ON CONFLICT DO NOTHING RETURNING user_id",
            (user_id, now),
        )
        if await cur.fetchone() is None:
            return TryItResult(False, "used")
        await conn.execute(
            "INSERT INTO entitlements (user_id, plan, status, hours_cap, extra_hours,"
            " campaign_cap, period_start, period_end, plan_changed_at, provider,"
            " last_event_at, updated_at)"
            " VALUES (%(user)s, 'try-it', 'active', %(hours)s, 0, %(campaigns)s, %(now)s,"
            " %(end)s, %(now)s, %(provider)s, %(now)s, %(now)s)"
            " ON CONFLICT (user_id) DO UPDATE SET plan = 'try-it', status = 'active',"
            " hours_cap = EXCLUDED.hours_cap, extra_hours = 0,"
            " campaign_cap = EXCLUDED.campaign_cap, period_start = EXCLUDED.period_start,"
            " period_end = EXCLUDED.period_end, grace_ends_at = NULL, lapsed_at = NULL,"
            " plan_changed_at = EXCLUDED.plan_changed_at, provider = EXCLUDED.provider,"
            " last_event_at = EXCLUDED.last_event_at, updated_at = EXCLUDED.updated_at",
            {
                "user": user_id,
                "hours": try_it.hours_per_month,
                "campaigns": try_it.campaigns,
                "now": now,
                "end": now + days * 86400,
                "provider": TRY_IT_PROVIDER,
            },
        )
    return TryItResult(True, "started")


async def has_used_try_it(db: Database, user_id: int) -> bool:
    async with db.user(user_id) as conn:
        cur = await conn.execute("SELECT 1 FROM try_it_used WHERE user_id = %s", (user_id,))
        return await cur.fetchone() is not None
