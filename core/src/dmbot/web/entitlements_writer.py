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
- **Grace only after paying.** A failed payment gives the 7-day grace only to someone with
  an earlier successful payment on record (owner decision, 2026-10-09, #922). A first
  payment that fails starts no plan, and a plan that never started is never lapsed.
"""

from __future__ import annotations

import dataclasses
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


async def _email_updated(db: Database, event: PaymentEvent) -> Outcome:
    """The payer changed their address at the company: the account is found by the customer
    id stored with its plan, and only a newer event replaces the address (events can arrive
    out of order). Nothing else changes, so it needs no ledger row. The address is never
    logged."""
    if not event.customer_id or not event.email:
        return "ignored"
    async with db.unscoped() as conn:
        cur = await conn.execute(
            "SELECT dmbot_set_payment_email(%s, %s, %s) AS done",
            (event.customer_id, event.email, event.occurred_at),
        )
        row = await cur.fetchone()
    return "applied" if row and row["done"] else "ignored"


async def _known_person(conn: Conn, user_id: int) -> bool:
    cur = await conn.execute("SELECT 1 FROM web_users WHERE user_id = %s", (user_id,))
    return await cur.fetchone() is not None


async def _paid_before(conn: Conn, user_id: int) -> bool:
    """Event ids outlive a deleted account (delete_person), so this tells "deleted their
    account" apart from "never signed in here"."""
    cur = await conn.execute("SELECT 1 FROM payment_events WHERE user_id = %s", (user_id,))
    return await cur.fetchone() is not None


# The events that mean money was taken: a plan started or renewed, or extra hours bought.
# (The payment company's adapter sends "started" only once the first payment has gone
# through; see payments.py.)
_PAID_KINDS = ("subscription_started", "subscription_renewed", "extra_hours_bought")

# SQL for "a payment on record": a row with no kind was recorded before kinds were kept and
# counts as paid. Shared with /me and the Try It jobs, so a recorded failure is never
# mistaken for a payment (#922).
PAID_EVENT_SQL = "(kind IS NULL OR kind = ANY(%(paid)s))"
PAID_KINDS = list(_PAID_KINDS)


async def _has_paid(conn: Conn, user_id: int) -> bool:
    """This person has an earlier successful payment on record. Called before the event
    being applied is recorded, so a failure never counts as its own earlier payment.

    Per person, not per subscription or company: a payment under any of their
    subscriptions counts. A row with no kind was recorded before kinds were kept; it counts
    as paid, so the old behaviour (grace, and lapsing when the plan ends) carries on for
    them rather than a plan that never lapses."""
    cur = await conn.execute(
        "SELECT 1 FROM payment_events WHERE user_id = %s AND (kind IS NULL OR kind = ANY(%s))"
        " LIMIT 1",
        (user_id, list(_PAID_KINDS)),
    )
    return await cur.fetchone() is not None


async def _record(conn: Conn, event: PaymentEvent, now: int) -> None:
    """Record the event id, so a repeat delivery is a duplicate. (_seen has already
    checked under the person's lock; DO NOTHING only guards against the impossible.)"""
    await conn.execute(
        "INSERT INTO payment_events (provider, event_id, user_id, subscription_id, kind,"
        " received_at) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (event.provider, event.event_id, event.user_id, event.subscription_id, event.kind, now),
    )


async def _old_subscription(conn: Conn, event: PaymentEvent) -> bool:
    """This person had news about this subscription before, but it isn't the plan they
    have now (the caller checked): one that ended with a deleted account, for example.
    A subscription brought back at the company (cancel undone) is picked up at its next
    renewal, which always applies; failure or end news before that is dropped."""
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


async def _moved_to(conn: Conn, event: PaymentEvent) -> int | None:
    """Where the plan this event names went, if its account moved (#1230). The company's
    records carry the Discord ID the plan was bought with; after a move the plan is the new
    account's, but only when the event names the plan that moved (its subscription or
    customer): a plan the old ID buys later stays the old ID's. Asked inside the person's
    plan lock, so a move that commits just before can't leave the event on a gone row."""
    cur = await conn.execute(
        "SELECT dmbot_moved_to(%s, %s, %s) AS moved",
        (event.user_id, event.subscription_id, event.customer_id),
    )
    row = await cur.fetchone()
    return None if row is None or row["moved"] is None else int(row["moved"])


MOVE_HOPS = 5  # accounts that moved again and again: more than this is not real


async def _recap_audio_event(
    conn: Conn, event: PaymentEvent, row: dict[str, Any] | None, now: int
) -> Outcome:
    """The recap audio add-on (#1389). It never touches the plan: it sets or clears the
    add-on's own columns, and only for a person who has a working paid plan to add it to (the
    way extra hours do). A paid period moves `recap_audio_until` forward, never back; a late
    event (older than the newest applied) is recorded and changes nothing."""
    if row is None or row["status"] == "lapsed" or row["plan"] == "try-it":
        if event.kind != "recap_audio_ended":
            log.error(
                "Recap audio %s for user %s with no working paid plan: needs a refund or credit",
                event.event_id,
                event.user_id,
            )
        await _record(conn, event, now)
        return "ignored"
    if event.occurred_at < row["recap_audio_event_at"]:
        await _record(conn, event, now)
        return "stale"
    known_sub = row["recap_audio_subscription_id"]
    if (
        event.kind != "recap_audio_ended"
        and known_sub is not None
        and event.subscription_id not in (None, known_sub)
    ):
        # A second add-on subscription while one works: both are being charged. The first
        # keeps its place; the webhook cancels this one at the company (app.py).
        await _record(conn, event, now)
        log.error(
            "Recap audio %s starts a second add-on subscription for user %s: cancelling it",
            event.event_id,
            event.user_id,
        )
        return "ignored"
    if event.kind == "recap_audio_ended":
        await _record(conn, event, now)
        known = row["recap_audio_subscription_id"]
        if known is not None and event.subscription_id not in (None, known):
            return "ignored"  # an older add-on subscription ending after a newer one began
        await conn.execute(
            "UPDATE entitlements SET recap_audio_until = NULL, recap_audio_subscription_id = NULL,"
            " recap_audio_event_at = %s, updated_at = %s WHERE user_id = %s",
            (event.occurred_at, now, event.user_id),
        )
        return "applied"
    if (
        event.period_start is None
        or event.period_end is None
        or event.period_end <= event.period_start
    ):
        log.error(
            "Recap audio event %s has no usable period: needs a person to look at it",
            event.event_id,
        )
        return "rejected"
    await _record(conn, event, now)
    await conn.execute(
        "UPDATE entitlements SET recap_audio_until = GREATEST(COALESCE(recap_audio_until, 0), %s),"
        " recap_audio_subscription_id = COALESCE(%s, recap_audio_subscription_id),"
        " recap_audio_event_at = %s, updated_at = %s WHERE user_id = %s",
        (event.period_end, event.subscription_id, event.occurred_at, now, event.user_id),
    )
    return "applied"


async def recap_audio_subscription(db: Database, user_id: int) -> str | None:
    """The company's id of this person's add-on subscription, if they have one that would charge
    again (cancelled with the plan or the account)."""
    async with db.user(user_id) as conn:
        cur = await conn.execute(
            "SELECT recap_audio_subscription_id AS s FROM entitlements WHERE user_id = %s",
            (user_id,),
        )
        row = await cur.fetchone()
    return None if row is None or row["s"] is None else str(row["s"])


async def plan_subscription(db: Database, user_id: int) -> str | None:
    """The company's id of the subscription behind this person's working paid plan, or None
    (Try It, a plan set by hand, or one that has stopped). The billing page opens on choosing a
    plan for it, and Renew undoes its cancellation (#2165)."""
    async with db.user(user_id) as conn:
        cur = await conn.execute(
            "SELECT provider_subscription_id AS s, status FROM entitlements WHERE user_id = %s",
            (user_id,),
        )
        row = await cur.fetchone()
    if row is None or row["s"] is None or row["status"] == "lapsed":
        return None
    return str(row["s"])


async def clear_cancel(db: Database, user_id: int, *, now: int) -> bool:
    """Renew (#2165): the company has been told to undo the cancellation, so the "ends on" date
    goes here too, at once, instead of waiting for its event. True if there was one to clear.

    `last_event_at` is the payment company's clock and is not touched: our clock must not decide
    whether one of its real events is stale. Only a "cancelled" event older than this Renew is
    ignored, by its own column (`renewed_at`)."""
    async with db.plan_writer(user_id) as conn:
        cur = await conn.execute(
            "UPDATE entitlements SET ends_at = NULL, renewed_at = %s, updated_at = %s"
            " WHERE user_id = %s AND ends_at IS NOT NULL AND status = 'active' RETURNING 1 AS ok",
            (now, now, user_id),
        )
        return await cur.fetchone() is not None


async def forget_recap_audio(db: Database, user_id: int, *, now: int) -> None:
    """The add-on subscription was cancelled at the company: clear it here too."""
    async with db.plan_writer(user_id) as conn:
        await conn.execute(
            "UPDATE entitlements SET recap_audio_until = NULL, recap_audio_subscription_id = NULL,"
            " updated_at = %s WHERE user_id = %s",
            (now, user_id),
        )


async def apply_event(db: Database, event: PaymentEvent, *, now: int) -> Outcome:
    """Apply one payment event, following the plan to its new account if it moved."""
    if event.kind == "email_updated":  # found by the customer id: no account to follow
        return await _email_updated(db, event)
    for _ in range(MOVE_HOPS):
        result = await _apply_once(db, event, now=now)
        if isinstance(result, int):
            event = dataclasses.replace(event, user_id=result)
            continue
        return result
    log.warning("Payment event %s followed too many account moves: ignored", event.event_id)
    return "ignored"


async def _apply_once(db: Database, event: PaymentEvent, *, now: int) -> Outcome | int:
    """One try under the person's plan lock; an int is the account the plan moved to."""
    loaded = plans.load()
    async with db.plan_writer(event.user_id) as conn:  # one change per person at a time
        moved = await _moved_to(conn, event)
        if moved is not None:
            return moved
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
        if event.email:
            # The address the company has for the payer: kept for sign-in links, move codes
            # and Try It emails. Written for any new event of a known person; never logged.
            await conn.execute(
                "UPDATE web_users SET payment_email = %s, payment_email_at = %s"
                " WHERE user_id = %s AND (payment_email_at IS NULL OR payment_email_at < %s)",
                (event.email, event.occurred_at, event.user_id, event.occurred_at),
            )
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

        if event.kind in ("recap_audio_started", "recap_audio_renewed", "recap_audio_ended"):
            return await _recap_audio_event(conn, event, row, now)

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
                " status = 'active', hours_cap = EXCLUDED.hours_cap, minutes_taken = 0,"
                # Extra hours belong to one period: a new period starts without them.
                " extra_hours = CASE WHEN %(new_period)s THEN 0"
                "   ELSE entitlements.extra_hours END,"
                " campaign_cap = EXCLUDED.campaign_cap, period_start = EXCLUDED.period_start,"
                " period_end = EXCLUDED.period_end, grace_ends_at = NULL, lapsed_at = NULL,"
                " ends_at = NULL, next_plan = NULL, next_plan_on = NULL,"
                " next_plan_event_at = GREATEST(entitlements.next_plan_event_at,"
                "   EXCLUDED.last_event_at),"
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

        if event.kind == "subscription_cancelled" and event.period_end is None:
            log.error(
                "Payment event %s cancels with no end date: needs a person to look at it",
                event.event_id,
            )
            return "rejected"
        # payment_failed, subscription_cancelled and subscription_ended change the paid plan the
        # person has.
        waiting = row is None or (
            row["provider"] != event.provider and event.occurred_at > row["last_event_at"]
        )
        if waiting and await _old_subscription(conn, event):
            # A subscription they had before, such as one cancelled when they deleted their
            # account (it ends with the paid month): not about the plan they have now.
            await _record(conn, event, now)
            return "ignored"
        if row is None:
            if event.kind == "payment_failed" and not await _has_paid(conn, event.user_id):
                # A first payment that failed: no plan starts. Recorded (as a failure, not a
                # payment) so /me can say so, and a repeat delivery is a duplicate (#922).
                await _record(conn, event, now)
                return "ignored"
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
        # Plan-change schedule events are ordered by their own column below, not by the plan's
        # clock: at a plan change's start the schedule's "released" and the change arrive in the
        # same moment, and the later of them must not make the real change look stale.
        schedule = event.kind in ("plan_scheduled", "plan_unscheduled")
        if not schedule and event.occurred_at < row["last_event_at"]:
            await _record(conn, event, now)
            return "stale"
        renewed = row.get("renewed_at")
        if event.kind == "subscription_cancelled" and renewed and event.occurred_at < renewed:
            await _record(conn, event, now)  # said before the person pressed Renew
            return "stale"
        paid = await _has_paid(conn, event.user_id)  # before this event is recorded
        await _record(conn, event, now)
        if not paid:
            # No payment on record: the plan never started, so there is nothing to give
            # grace to and nothing to lapse. The row stays as it was.
            log.error(
                "Payment event %s (%s) for user %s, who has no earlier payment on record:"
                " no grace, nothing changed: needs a person to look at it",
                event.event_id,
                event.kind,
                event.user_id,
            )
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
        elif event.kind == "subscription_cancelled":
            # Cancelled on the payment page: the plan works to the end of the period already
            # paid for and then ends. Stored so the account page can say so (#824); a renewal
            # or a resume (an "active" event) clears it.
            await conn.execute(
                "UPDATE entitlements SET ends_at = %s, last_event_at = %s, updated_at = %s"
                " WHERE user_id = %s",
                (event.period_end, event.occurred_at, now, event.user_id),
            )
        elif event.kind == "plan_scheduled":
            # The company has set a plan change for the end of the period: stored so the account
            # page can say what starts and when (#2165). It is cleared when the change begins
            # (a renewal or plan change), is undone, or the plan ends.
            await conn.execute(
                "UPDATE entitlements SET next_plan = %s, next_plan_on = %s,"
                " next_plan_event_at = %s, updated_at = %s"
                " WHERE user_id = %s AND next_plan_event_at <= %s",
                (
                    event.plan,
                    event.period_start,
                    event.occurred_at,
                    now,
                    event.user_id,
                    event.occurred_at,
                ),
            )
        elif event.kind == "plan_unscheduled":
            await conn.execute(
                "UPDATE entitlements SET next_plan = NULL, next_plan_on = NULL,"
                " next_plan_event_at = %s, updated_at = %s"
                " WHERE user_id = %s AND next_plan_event_at <= %s",
                (event.occurred_at, now, event.user_id, event.occurred_at),
            )
        else:  # subscription_ended
            await conn.execute(
                "UPDATE entitlements SET status = 'lapsed', grace_ends_at = NULL, ends_at = NULL,"
                " next_plan = NULL, next_plan_on = NULL,"
                " next_plan_event_at = GREATEST(next_plan_event_at, %s), lapsed_at = %s,"
                " last_event_at = %s, updated_at = %s WHERE user_id = %s",
                (event.occurred_at, event.occurred_at, event.occurred_at, now, event.user_id),
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
            " hours_cap = EXCLUDED.hours_cap, extra_hours = 0, minutes_taken = 0,"
            " campaign_cap = EXCLUDED.campaign_cap, period_start = EXCLUDED.period_start,"
            " period_end = EXCLUDED.period_end, grace_ends_at = NULL, lapsed_at = NULL,"
            " ends_at = NULL, next_plan = NULL, next_plan_on = NULL,"
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
