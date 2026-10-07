"""Reading what a person has paid for (#435, #437).

The `entitlements` table is written only by the payment webhook (dmbot.web, #435); a
test checks no other module writes it. The bot's plan rules (#437) read it here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from dmbot.db import Database
from dmbot.plans import PlanId

Status = Literal["active", "grace", "lapsed"]


@dataclass(frozen=True)
class Entitlement:
    user_id: int
    plan: PlanId
    status: Status
    hours_cap: int
    campaign_cap: int
    period_start: int  # Unix seconds
    period_end: int
    grace_ends_at: int | None

    def usable(self, now: int) -> bool:
        """The plan works now: active, or in the payment grace and not past it."""
        if self.status == "active":
            return True
        return (
            self.status == "grace" and self.grace_ends_at is not None and now < self.grace_ends_at
        )


async def get(db: Database, user_id: int) -> Entitlement | None:
    """This person's plan, or None if they never had one."""
    async with db.user(user_id) as conn:
        cur = await conn.execute(
            "SELECT user_id, plan, status, hours_cap, campaign_cap, period_start, period_end,"
            " grace_ends_at FROM entitlements WHERE user_id = %s",
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
        campaign_cap=row["campaign_cap"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        grace_ends_at=row["grace_ends_at"],
    )
