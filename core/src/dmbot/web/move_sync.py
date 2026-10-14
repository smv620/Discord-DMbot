"""Pointing the payment company's records at an account's new Discord ID after a move (#1230).

A move is one database transaction; the payment company is told afterwards, never inside it.
Until it is told, an event for the old ID still finds the plan through `dmbot_moved_to`, so
nothing is lost by waiting. Each pass takes a few moves that still need it, updates Stripe
for each, and marks it done only when Stripe said yes. A failure is logged with ids only and
tried again on the next pass.
"""

from __future__ import annotations

import logging

from dmbot.db import Database
from dmbot.web.payments import PaymentError, PaymentProvider

log = logging.getLogger(__name__)


async def sync_moves(db: Database, payments: PaymentProvider) -> int:
    """One pass. Returns how many moves were brought up to date."""
    async with db.unscoped() as conn:
        cur = await conn.execute(
            "SELECT old_user_id, new_user_id, customer_id, subscription_id"
            " FROM dmbot_moves_to_sync()"
        )
        waiting = await cur.fetchall()
    done = 0
    for row in waiting:
        try:
            await payments.retarget(
                customer_id=row["customer_id"],
                subscription_id=row["subscription_id"],
                user_id=int(row["new_user_id"]),
            )
        except PaymentError as exc:
            log.warning("Couldn't update the payment records for a moved account (%s)", exc)
            continue
        except Exception:
            log.exception("Updating the payment records for a moved account failed")
            continue
        async with db.unscoped() as conn:
            await conn.execute("SELECT dmbot_move_synced(%s)", (int(row["old_user_id"]),))
        done += 1
    return done
