"""Moving to a new Discord account, from the website (#1231; docs/PLAN.md, "Accounts").

Who may ask for a move code is decided in one place, `can_move`, used by `/me` (to show the
button) and by the move-code route, so the two can never disagree. The code itself, its email and
the move are #1230's: until that is wired the site only has this door (`MoveCodes`).
"""

from __future__ import annotations

from typing import Protocol

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


class MoveCodes(Protocol):
    """Makes a move code and emails it to the account's payment email. The real one is #1230's."""

    async def send_code(self, user_id: int) -> None: ...
