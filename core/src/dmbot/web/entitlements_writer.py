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

# applied: the plan changed. duplicate: seen before. stale: older than what's applied
# (recorded, changes nothing). ignored: nothing of DMbot's to change (recorded).
# retry: nothing to apply it to yet, e.g. an "ended" arriving before its "started"; NOT
# recorded, so the company's next delivery is applied. rejected: unusable (unknown plan,
# no period); NOT recorded, logged as an error for someone to look at.
Outcome = Literal["applied", "duplicate", "stale", "ignored", "retry", "rejected"]
TRY_IT_PROVIDER = "dmbot"


@dataclass(frozen=True)
class TryItResult:
    started: bool
    reason: Literal["started", "used", "has_plan"]


async def _current(conn: Conn, user_id: int) -> dict[str, Any] | None:
    cur = await conn.execute("SELECT * FROM entitlements WHERE user_id = %s", (user_id,))
    return await cur.fetchone()


async def _known_person(conn: Conn, user_id: int) -> bool:
    cur = await conn.execute("SELECT 1 FROM web_users WHERE user_id = %s", (user_id,))
    return await cur.fetchone() is not None


async def _paid_before(conn: Conn, user_id: int) -> bool:
    """Event ids outlive a deleted account (delete_person), so this tells "deleted their
    account" apart from "never signed in here"."""
    cur = await conn.execute("SELECT 1 FROM payment_events WHERE user_id = %s", (user_id,))
    return await cur.fetchone() is not None


async def _record(conn: Conn, event: PaymentEvent, now: int) -> None:
    """Record the event id, so a repeat delivery is a duplicate. (_seen has already
    checked under the person's lock; DO NOTHING only guards against the impossible.)"""
    await conn.execute(
        "INSERT INTO payment_events (provider, event_id, user_id, subscription_id, received_at)"
        " VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (event.provider, event.event_id, event.user_id, event.subscription_id, now),
    )


async def _old_subscription(conn: Conn, event: PaymentEvent) -> bool:
    """This person had news about this subscription before, but it isn't the plan they
    have now (the caller checked): one that ended with a deleted account, for example."""
    if event.subscription_id is None:
        return False
    cur = await conn.execute(
        "SELECT 1 FROM payment_events"
        " WHERE user_id = %s AND provider = %s AND subscription_id = %s LIMIT 1",
        (event.user_id, event.provider, event.subscription_id),
    )
    return await cur.fetchone() is not None


async def _seen(conn: Conn, event: PaymentEvent) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM payment_events WHERE provider = %s AND event_id = %s",
        (event.provider, event.event_id),
    )
    return await cur.fetchone() is not None


def _same_subscription(row: dict[str, Any], event: PaymentEvent) -> bool:
    """An event about the plan this person has now: never Try It (ours, not the company's),
    and when both sides know the subscription, the same one (an old subscription ending
    after a plan change must not stop the new one)."""
    if row["provider"] != event.provider:
        return False
    known = row["provider_subscription_id"]
    return known is None or event.subscription_id is None or known == event.subscription_id


async def apply_event(db: Database, event: PaymentEvent, *, now: int) -> Outcome:
    loaded = plans.load()
    async with db.plan_writer(event.user_id) as conn:  # one change per person at a time
        if not await _known_person(conn, event.user_id):
            # Nothing to attach the plan to. The company still has the payment; ids only.
            if await _paid_before(conn, event.user_id):
                # Expected after a deletion: the cancelled subscription's last events.
                log.warning(
                    "Payment event %s for user %s, whose account was deleted: ignored",
                    event.event_id,
                    event.user_id,
                )
            else:
                log.error(
                    "Payment event %s for unknown user %s: needs a person to look at it",
                    event.event_id,
                    event.user_id,
                )
            return "ignored"
        if await _seen(conn, event):
            return "duplicate"
        row = await _current(conn, event.user_id)

        if event.kind == "extra_hours_bought":
            # A one-off purchase, not a change of state: credit it whenever it arrives, as
            # long as there's a working plan to add it to.
            if row is None or row["status"] == "lapsed" or row["plan"] == "try-it":
                # Extra hours are a paid add-on: not on Try It, not without a plan.
                log.error(
                    "Extra hours %s for user %s with no working paid plan: needs a refund"
                    " or credit",
                    event.event_id,
                    event.user_id,
                )
                await _record(conn, event, now)
                return "ignored"
            await _record(conn, event, now)
            log.info(
                "Extra hours %s added to user %s's %s plan",
                event.event_id,
                event.user_id,
                row["plan"],
            )
            await conn.execute(
                "UPDATE entitlements SET extra_hours = extra_hours + %s, updated_at = %s"
                " WHERE user_id = %s",
                (loaded.extra_hours, now, event.user_id),
            )
            return "applied"

        if event.kind in ("subscription_started", "subscription_renewed"):
            plan = loaded.get(event.plan or "")
            if (
                plan is None
                or not plan.price_cents
                or event.period_start is None
                or event.period_end is None
                or event.period_end <= event.period_start
            ):
                log.error(
                    "Payment event %s has no usable plan or period: needs a person to look at it",
                    event.event_id,
                )
                return "rejected"
            # Older than what's applied: seen, changes nothing. Whatever the row's provider:
            # Try It keeps the stopped paid plan's last_event_at, so a late renewal of
            # that dead subscription can't overwrite Try It (a fresh one has 0).
            if row is not None and event.occurred_at < row["last_event_at"]:
                await _record(conn, event, now)
                return "stale"
            second = (
                row is not None
                and row["status"] != "lapsed"
                and row["provider"] == event.provider
                and not _same_subscription(row, event)
            )
            if second and event.kind == "subscription_started":
                # Could be a plan change made as a new subscription, so it's applied; but
                # if the old one keeps renewing, two are being charged.
                log.warning(
                    "Payment event %s starts another subscription for user %s while one works",
                    event.event_id,
                    event.user_id,
                )
            if second and event.kind == "subscription_renewed":
                # Two subscriptions at once (two checkout tabs, say): this one is being
                # charged too. Never pick one silently; someone refunds one of them.
                # (Over a stopped plan, a renewal is applied: it's the one still paid for.)
                await _record(conn, event, now)
                log.error(
                    "Payment event %s renews a second subscription for user %s: needs a"
                    " person to look at it",
                    event.event_id,
                    event.user_id,
                )
                return "ignored"
            await _record(conn, event, now)
            changed = row is None or row["plan"] != plan.id
            new_period = row is None or row["period_start"] != event.period_start
            await conn.execute(
                "INSERT INTO entitlements (user_id, plan, status, hours_cap, extra_hours,"
                " campaign_cap, period_start, period_end, grace_ends_at, lapsed_at,"
                " plan_changed_at, provider, provider_customer_id, provider_subscription_id,"
                " last_event_at, updated_at)"
                " VALUES (%(user)s, %(plan)s, 'active', %(hours)s, 0, %(campaigns)s,"
                " %(start)s, %(end)s, NULL, NULL, %(now)s, %(provider)s, %(customer)s,"
                " %(subscription)s, %(at)s, %(now)s)"
                " ON CONFLICT (user_id) DO UPDATE SET plan = EXCLUDED.plan,"
                " status = 'active', hours_cap = EXCLUDED.hours_cap,"
                # Extra hours belong to one period: a new period starts without them.
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

        # payment_failed and subscription_ended change the paid plan the person has.
        waiting = row is None or (
            row["provider"] != event.provider and event.occurred_at > row["last_event_at"]
        )
        if waiting and await _old_subscription(conn, event):
            # A subscription they had before, such as one cancelled when they deleted their
            # account (it ends with the paid month): not about the plan they have now.
            await _record(conn, event, now)
            return "ignored"
        if row is None:
            # Nothing to change yet: the company may have sent this before the "started"
            # event. Not recorded, so its next delivery is applied.
            return "retry"
        if waiting:
            # On Try It (ours): news newer than the company's last word must be about a
            # new subscription whose "started" hasn't arrived yet, so wait for it. Try It
            # keeps the stopped paid plan's last_event_at, so that plan's late news isn't.
            return "retry"
        if not _same_subscription(row, event):
            # About a plan the person no longer has: one that was replaced, or one that
            # had stopped before they started Try It.
            await _record(conn, event, now)
            return "ignored"  # about a subscription that was replaced
        if event.occurred_at < row["last_event_at"]:
            await _record(conn, event, now)
            return "stale"
        await _record(conn, event, now)
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
        else:  # subscription_ended
            await conn.execute(
                "UPDATE entitlements SET status = 'lapsed', grace_ends_at = NULL,"
                " lapsed_at = %s, last_event_at = %s, updated_at = %s WHERE user_id = %s",
                (event.occurred_at, event.occurred_at, now, event.user_id),
            )
        return "applied"


async def start_try_it(db: Database, user_id: int, *, now: int) -> TryItResult:
    """Start the free Try It plan: once per Discord user, and not over a working plan."""
    loaded = plans.load()
    try_it = loaded.by_id["try-it"]
    days = try_it.trial_days or 30
    async with db.plan_writer(user_id) as conn:  # waits for any payment event in flight
        row = await _current(conn, user_id)
        if row is not None and row["status"] != "lapsed":
            return TryItResult(False, "has_plan")
        cur = await conn.execute(
            "INSERT INTO try_it_used (user_id) VALUES (%s)"
            " ON CONFLICT DO NOTHING RETURNING user_id",
            (user_id,),
        )
        if await cur.fetchone() is None:
            return TryItResult(False, "used")
        await conn.execute(
            "INSERT INTO entitlements (user_id, plan, status, hours_cap, extra_hours,"
            " campaign_cap, period_start, period_end, plan_changed_at, provider,"
            " last_event_at, updated_at)"
            " VALUES (%(user)s, 'try-it', 'active', %(hours)s, 0, %(campaigns)s, %(now)s,"
            " %(end)s, %(now)s, %(provider)s, 0, %(now)s)"
            " ON CONFLICT (user_id) DO UPDATE SET plan = 'try-it', status = 'active',"
            " hours_cap = EXCLUDED.hours_cap, extra_hours = 0,"
            " campaign_cap = EXCLUDED.campaign_cap, period_start = EXCLUDED.period_start,"
            " period_end = EXCLUDED.period_end, grace_ends_at = NULL, lapsed_at = NULL,"
            " plan_changed_at = EXCLUDED.plan_changed_at, provider = EXCLUDED.provider,"
            # The stopped plan's company ids go: no billing page for Try It, and its late
            # events are then about a plan the person no longer has.
            " provider_customer_id = NULL, provider_subscription_id = NULL,"
            # last_event_at is the payment company's clock: Try It leaves it alone.
            " updated_at = EXCLUDED.updated_at"
            # Only over a plan that has stopped (the lock makes this certain, this says it).
            " WHERE entitlements.status = 'lapsed'",
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
