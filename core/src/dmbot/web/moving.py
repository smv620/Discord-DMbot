"""Moving to a new Discord account, from the website (#1231; docs/PLAN.md, "Accounts").

Move codes are retired (#2533 item 5): moving is adding the new Discord account on the Account
page and removing the old one. What is left here is `can_move` (used by `/me`) and the hourly
sweep of old asks; the code's tables and doors stay for accounts already moved.
"""

from __future__ import annotations

from dmbot.db import Database
from dmbot.entitlements import Entitlement
from dmbot.web.payments import paid_plan_ids


def can_move(plan: Entitlement | None, now: int) -> bool:
    """An active, working **paid** plan. Not Try It or its automatic extension (both are the
    `try-it` plan), not a grant and not the free list (neither is a plan row at all)."""
    return (
        plan is not None
        and plan.plan in paid_plan_ids()
        and plan.status == "active"
        and plan.usable(now)
    )


async def forget_old_asks(db: Database, *, now: int) -> int:
    """The hourly sweep: move-code asks older than the hour they count for."""
    async with db.unscoped() as conn:
        cur = await conn.execute("SELECT dmbot_web_forget_move_asks(%s) AS n", (now,))
        row = await cur.fetchone()
    return int(row["n"]) if row else 0
