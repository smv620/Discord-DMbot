"""Reading what a person has paid for (#435, #437).

The `entitlements` table is changed only by the plan writer (the payment webhook and
Try It, dmbot.web, #435) through Database.plan_writer(); the database refuses any other
write. The bot's plan rules (#437) read it here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from dmbot.db import Conn, Database
from dmbot.plans import PlanId

Status = Literal["active", "grace", "lapsed"]

# A technical guard, not a plan rule (accepted by web, #469; docs/PLAN.md "Plans and
# pricing"): a renewal's payment event can arrive a little after the period ends, so a
# paying person's game isn't stopped in that gap. A missed lapse event still stops the
# plan after this. Try It has no slack: it ends exactly when its 30 days do.
RENEWAL_SLACK_SECONDS = 3 * 24 * 3600


@dataclass(frozen=True)
class Entitlement:
    user_id: int
    plan: PlanId
    status: Status
    hours_cap: int
    extra_hours: int  # bought for this period
    campaign_cap: int
    period_start: int  # Unix seconds
    period_end: int
    grace_ends_at: int | None
    lapsed_at: int | None
    plan_changed_at: int

    @property
    def hours_this_period(self) -> int:
        return self.hours_cap + self.extra_hours

    def usable(self, now: int) -> bool:
        """The plan works now: active (and its period not long over), or in the payment
        grace and not past it. Try It simply ends with its period."""
        if self.status == "active":
            slack = 0 if self.plan == "try-it" else RENEWAL_SLACK_SECONDS
            return now < self.period_end + slack
        return (
            self.status == "grace" and self.grace_ends_at is not None and now < self.grace_ends_at
        )


async def get(db: Database, user_id: int) -> Entitlement | None:
    """This person's plan, or None if they never had one."""
    async with db.user(user_id) as conn:
        return await read(conn, user_id)


async def read(conn: Conn, user_id: int) -> Entitlement | None:
    """This person's plan, inside a transaction the caller already has open (for example
    `/dmbot start`'s server transaction, #437). It lets that transaction see this person's
    website rows too (it sets dmbot.user_id for the rest of the transaction), and nothing
    of anyone else's."""
    await conn.execute("SELECT set_config('dmbot.user_id', %s, true)", (str(int(user_id)),))
    cur = await conn.execute(
        "SELECT user_id, plan, status, hours_cap, extra_hours, campaign_cap, period_start,"
        " period_end, grace_ends_at, lapsed_at, plan_changed_at"
        " FROM entitlements WHERE user_id = %s",
        (user_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return Entitlement(
        user_id=row["user_id"],
        plan=cast(PlanId, row["plan"]),
        status=cast(Status, row["status"]),
        hours_cap=row["hours_cap"],
        extra_hours=row["extra_hours"],
        campaign_cap=row["campaign_cap"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        grace_ends_at=row["grace_ends_at"],
        lapsed_at=row["lapsed_at"],
        plan_changed_at=row["plan_changed_at"],
    )
