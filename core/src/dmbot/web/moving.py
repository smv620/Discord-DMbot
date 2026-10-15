"""Moving to a new Discord account, from the website (#1231; docs/PLAN.md, "Accounts").

Who may ask for a move code is decided in one place, `can_move`, used by `/me` (to show the
button) and by the move-code route, so the two can never disagree. The code itself, its email and
the move are #1230's; the website's side of asking for a code is `EmailedMoveCodes` (#1246).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Literal, Protocol

from dmbot.account_move import CODE_LIFETIME_S, hash_code, new_code
from dmbot.db import Database
from dmbot.entitlements import Entitlement
from dmbot.mail import Mailer
from dmbot.web.accounts import payment_email
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


MoveOutcome = Literal["sent", "not_paid", "too_soon", "slow_down", "no_email", "failed"]


class MoveCodes(Protocol):
    """Makes a move code and emails it to the account's payment email, and says how it went.
    `source` is a hash of the connection's address, for the per-connection limit."""

    async def send_code(self, user_id: int, source: str) -> MoveOutcome: ...


class EmailedMoveCodes:
    """The real `MoveCodes` (#1246): the code is made and counted by one database door, then
    emailed to the **payment email only** (the address the payment company has; a code goes
    nowhere else). The code is in the email and nowhere else: not in a log, not in the email's
    kind (that holds the first 8 characters of its hash, which can't be turned back into it),
    not in the answer."""

    def __init__(
        self, db: Database, mailer: Mailer, clock: Callable[[], float] = time.time
    ) -> None:
        self._db = db
        self._mailer = mailer
        self._clock = clock

    async def send_code(self, user_id: int, source: str) -> MoveOutcome:
        email = await payment_email(self._db, user_id)
        if not email:
            return "no_email"  # checked first, so a missing address uses up none of the asks
        now = int(self._clock())
        code = new_code()
        code_hash = hash_code(code)
        async with self._db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT dmbot_web_move_code(%s, %s, %s, %s, %s) AS outcome",
                (user_id, source, now, code_hash, now + CODE_LIFETIME_S),
            )
            row = await cur.fetchone()
        outcome = str(row["outcome"]) if row else "failed"
        if outcome in ("not_paid", "too_soon", "slow_down"):
            return outcome  # type: ignore[return-value]  # narrowed by the test above
        if outcome != "ok":
            return "failed"
        try:
            result = await self._mailer.send(
                user_id=user_id,
                kind=f"move-code:{code_hash[:8]}",
                to=email,
                subject="Your DMbot move code",
                body=(
                    f"Your DMbot move code: {code[:4]}-{code[4:]}\n\n"
                    "It works once and lasts 30 minutes.\n"
                    'On your NEW Discord account, run /dmbot migrate, tap "Import my account", '
                    "and enter it.\n"
                    "The code moves your plan, its hours and your campaigns. Never give it to "
                    "anyone else.\n"
                    "Didn't ask for this? Ignore this email. Nothing changes."
                ),
            )
        except Exception:
            result = "failed"
        if result not in ("sent", "already_sent"):
            await self._cancel(user_id)
            return "failed"
        return "sent"

    async def _cancel(self, user_id: int) -> None:
        async with self._db.unscoped() as conn:
            await conn.execute("SELECT dmbot_web_cancel_move_code(%s)", (user_id,))
