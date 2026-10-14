"""Pointing the payment company's records at an account's new Discord ID after a move (#1230).

A move is one database transaction; the payment company is told afterwards, never inside it.
Until it is told, an event for the old ID still finds the plan through `dmbot_moved_to`, so
nothing is lost by waiting. Each pass takes a few moves that still need it, updates Stripe
for each, and marks it done only when Stripe said yes. A failure is logged with ids only and
tried again on the next pass.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from dmbot.db import Database
from dmbot.web.payments import PaymentError, PaymentProvider

log = logging.getLogger(__name__)
BACKOFF_FIRST_S = 60
BACKOFF_MAX_S = 3600


@dataclass
class Backoff:
    """Moves that failed wait longer each time (a minute, two, four... up to an hour), kept in
    memory: a restart simply tries them all again, which is harmless (the update is repeatable)."""

    failures: dict[int, int] = field(default_factory=dict)
    not_before: dict[int, float] = field(default_factory=dict)

    def ready(self, old_user_id: int, now: float) -> bool:
        return self.not_before.get(old_user_id, 0.0) <= now

    def failed(self, old_user_id: int, now: float) -> None:
        count = self.failures.get(old_user_id, 0) + 1
        self.failures[old_user_id] = count
        self.not_before[old_user_id] = now + min(BACKOFF_FIRST_S * 2 ** (count - 1), BACKOFF_MAX_S)

    def done(self, old_user_id: int) -> None:
        self.failures.pop(old_user_id, None)
        self.not_before.pop(old_user_id, None)


async def sync_moves(
    db: Database,
    payments: PaymentProvider,
    backoff: Backoff | None = None,
    now: float | None = None,
) -> int:
    """One pass. Returns how many moves were brought up to date."""
    backoff = backoff if backoff is not None else Backoff()
    now = time.time() if now is None else now
    async with db.unscoped() as conn:
        cur = await conn.execute(
            "SELECT old_user_id, new_user_id, customer_id, subscription_id"
            " FROM dmbot_moves_to_sync()"
        )
        waiting = await cur.fetchall()
    done = 0
    for row in waiting:
        old = int(row["old_user_id"])
        if not backoff.ready(old, now):
            continue
        try:
            await payments.retarget(
                customer_id=row["customer_id"],
                subscription_id=row["subscription_id"],
                user_id=int(row["new_user_id"]),
            )
        except PaymentError as exc:
            backoff.failed(old, now)
            log.warning("Couldn't update the payment records of moved account %s (%s)", old, exc)
            continue
        except Exception:
            backoff.failed(old, now)
            log.exception("Updating the payment records of moved account %s failed", old)
            continue
        async with db.unscoped() as conn:
            await conn.execute("SELECT dmbot_move_synced(%s)", (old,))
        backoff.done(old)
        done += 1
    return done
