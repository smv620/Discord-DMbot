"""Recap audio add-on subscriptions that must be cancelled (#1399, from #1389).

A second add-on, one bought with no working plan, or one on a plan that now includes audio only
keeps charging. The webhook cancels it at once; when the company refuses, the subscription id is
kept here (in the database, by a door that holds nothing else) and the hourly sweep tries again,
each at most once an hour, until the company says yes. Logs carry counts only.
"""

from __future__ import annotations

import logging

from dmbot.db import Database
from dmbot.web.payments import PaymentProvider

log = logging.getLogger(__name__)

NEEDS_A_PERSON_AFTER = 24  # hourly tries: a day of refusals is no passing outage


async def note(db: Database, subscription_id: str) -> None:
    """Keep a subscription the company wouldn't cancel, for the sweep. Never raises: the
    webhook that found it must still be answered."""
    try:
        async with db.unscoped() as conn:
            await conn.execute("SELECT dmbot_note_stray_addon(%s)", (subscription_id,))
    except Exception as exc:
        log.error(
            "Couldn't keep a stray add-on for a retry: %s: needs a person", type(exc).__name__
        )


async def sweep(db: Database, payments: PaymentProvider) -> int:
    """One pass: cancel the kept subscriptions that are due. Returns how many were cancelled."""
    async with db.unscoped() as conn:
        cur = await conn.execute("SELECT subscription_id, attempts FROM dmbot_strays_to_cancel()")
        due = [(str(r["subscription_id"]), int(r["attempts"])) for r in await cur.fetchall()]
    done = failed = stuck = 0
    for sub, attempts in due:
        try:
            await payments.cancel(subscription_id=sub)
        except Exception:  # the company, the network: tried again in an hour
            failed += 1
            if attempts + 1 >= NEEDS_A_PERSON_AFTER:
                stuck += 1
            async with db.unscoped() as conn:
                await conn.execute("SELECT dmbot_stray_addon_tried(%s)", (sub,))
            continue
        async with db.unscoped() as conn:
            await conn.execute("SELECT dmbot_stray_addon_done(%s)", (sub,))
        done += 1
    if stuck:  # still charging: only a person can fix a refusal that lasts (no ids in the log)
        log.error("%d recap audio subscription(s) can't be cancelled: needs a person", stuck)
    if due:
        log.info("Stray recap audio subscriptions: %d cancelled, %d to try again", done, failed)
    return done
