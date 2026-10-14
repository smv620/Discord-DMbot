"""Moving an account to a new Discord account (#1230; docs/PLAN.md, "Accounts").

For an **active paid subscription** only (not Try It, an extension, a grant or the free
list). Two steps:

1. On the old account, ask for a **move code**. It goes to the account's payment email,
   never on screen: 8 letters and digits, 30 minutes, one use. Only its hash is stored.
2. On the new account, type the code. The screen shows what will move (the plan and each
   campaign's name) and asks to confirm. Then, in one database transaction
   (`dmbot_move_account`, migration 0066), the plan, its hours, the Stripe link, the owned
   campaigns and the DM place and player entries in them go to the new Discord ID. The old ID
   is left as a "moved" marker and can never start Try It. The DMbot ID stays. Recording
   consent does not move: the new account says yes for itself.

Limits: one move in 30 days (checked in the database), the codes are rate-limited here, and a
wrong code counts against the person typing it. This module has no Discord in it.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from dmbot.db import Database

CODE_LETTERS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: easy to read out and type
CODE_LENGTH = 8
CODE_LIFETIME_S = 30 * 60
MOVE_GAP_S = 30 * 86400
GUESSES = 5  # wrong codes a person may type...
GUESS_WINDOW_S = 10 * 60  # ...in this long
ASKS = 3  # codes an account may ask for...
ASK_WINDOW_S = 60 * 60  # ...in this long

NOT_PAID = (
    "Moving needs an active paid plan. Try It, a free trial and other free access can't be moved."
)
TOO_SOON = "This account moved less than 30 days ago. Try again after that."
BAD_CODE = "That code didn't work. Check it, or ask for a new one on the old account."
TOO_MANY = "Too many tries. Wait a few minutes, then try again."
TOO_MANY_ASKS = "A code was asked for a few times already. Wait an hour, then ask again."
SAME = "That's the same account. Use the code on the new Discord account."
NEW_HAS_PLAN = "This account already has a paid plan. A move needs an account without one."
NEW_MOVED_AWAY = "This account moved away before, so it can't be the new one."
LISTENING = "A session is running. Stop it first, then try the move again."
NO_ACCOUNT = "There's no DMbot account to move on that Discord account."

_REFUSALS = {
    "same": SAME,
    "no_account": NO_ACCOUNT,
    "not_paid": NOT_PAID,
    "too_soon": TOO_SOON,
    "new_has_plan": NEW_HAS_PLAN,
    "new_moved_away": NEW_MOVED_AWAY,
    "listening": LISTENING,
}


class MoveError(ValueError):
    """Something the person can fix or needs to know. The message is safe to show as-is."""


def new_code() -> str:
    return "".join(secrets.choice(CODE_LETTERS) for _ in range(CODE_LENGTH))


def clean_code(text: str) -> str:
    """A code as typed: any case, spaces or dashes."""
    return "".join(c for c in text.upper() if c.isalnum())


def hash_code(code: str) -> str:
    return hashlib.sha256(clean_code(code).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Preview:
    plan: str  # the plan's id, as plans.json names it
    campaigns: tuple[str, ...]  # each owned campaign's name


class AccountMoves:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock
        self._wrong: dict[int, deque[float]] = {}  # new account -> wrong guesses
        self._asked: dict[int, deque[float]] = {}  # old account -> codes asked for

    def _now(self) -> int:
        return int(self._clock())

    async def can_move(self, user_id: int) -> str | None:
        """Why this account can't ask for a code, in plain words; None if it can. Judged from
        the person's own plan and the 30-day rule."""
        async with self._db.user(user_id) as conn:
            cur = await conn.execute(
                "SELECT plan, status, provider_subscription_id FROM entitlements"
                " WHERE user_id = %s",
                (user_id,),
            )
            plan = await cur.fetchone()
            cur = await conn.execute(
                "SELECT dmbot_id FROM web_users WHERE user_id = %s", (user_id,)
            )
            account = await cur.fetchone()
        if (
            plan is None
            or plan["plan"] == "try-it"
            or plan["status"] != "active"
            or not plan["provider_subscription_id"]
            or account is None
        ):
            return NOT_PAID
        async with self._db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT 1 AS ok FROM account_moves WHERE dmbot_id = %s AND moved_at > %s",
                (account["dmbot_id"], self._now() - MOVE_GAP_S),
            )
            if await cur.fetchone() is not None:
                return TOO_SOON
        return None

    async def make_code(self, user_id: int) -> str:
        """A new move code for this account (the earlier one stops working). The caller sends
        it to the payment email and shows it nowhere else."""
        why = await self.can_move(user_id)
        if why is not None:
            raise MoveError(why)
        now = self._now()
        asked = self._asked.setdefault(user_id, deque())
        while asked and now - asked[0] > ASK_WINDOW_S:
            asked.popleft()
        if len(asked) >= ASKS:
            raise MoveError(TOO_MANY_ASKS)
        asked.append(now)
        code = new_code()
        async with self._db.unscoped() as conn:
            await conn.execute(
                "INSERT INTO move_codes (user_id, code_hash, created_at, expires_at)"
                " VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (user_id) DO UPDATE SET code_hash = EXCLUDED.code_hash,"
                " created_at = EXCLUDED.created_at, expires_at = EXCLUDED.expires_at,"
                " used_at = NULL",
                (user_id, hash_code(code), now, now + CODE_LIFETIME_S),
            )
        return code

    async def find(self, new_user_id: int, code: str) -> int:
        """The old account a code is for. A wrong or old code counts toward a short limit for
        the person typing it (kept in memory; a code is 8 from 31, about 10**12, and lasts
        half an hour, so the limit only slows a determined guesser)."""
        now = self._now()
        tries = self._wrong.setdefault(new_user_id, deque())
        while tries and now - tries[0] > GUESS_WINDOW_S:
            tries.popleft()
        if len(tries) >= GUESSES:
            raise MoveError(TOO_MANY)
        tries.append(now)  # counted before the database is asked, so parallel guesses count
        wanted = clean_code(code)
        found = None
        if len(wanted) == CODE_LENGTH:
            async with self._db.unscoped() as conn:
                cur = await conn.execute(
                    "SELECT user_id FROM move_codes WHERE code_hash = %s AND used_at IS NULL"
                    " AND expires_at > %s",
                    (hash_code(wanted), now),
                )
                found = await cur.fetchone()
        if found is None:
            raise MoveError(BAD_CODE)
        tries.pop()  # a right code isn't a wrong guess
        return int(found["user_id"])

    async def preview(self, old_user_id: int) -> Preview:
        async with self._db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT kind, label FROM dmbot_move_preview(%s)", (old_user_id,)
            )
            rows = await cur.fetchall()
        plan = next((str(r["label"]) for r in rows if r["kind"] == "plan"), "")
        return Preview(plan, tuple(str(r["label"]) for r in rows if r["kind"] == "campaign"))

    async def move(self, old_user_id: int, new_user_id: int, code: str) -> None:
        """Do the move. The code is checked again here, so a confirmation that sat open past
        its half hour (or after a newer code) moves nothing."""
        if await self.find(new_user_id, code) != old_user_id:
            raise MoveError(BAD_CODE)
        async with self._db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT dmbot_move_account(%s, %s, %s) AS outcome",
                (old_user_id, new_user_id, self._now()),
            )
            row = await cur.fetchone()
        outcome = str(row["outcome"]) if row else "no_account"
        if outcome != "moved":
            raise MoveError(_REFUSALS.get(outcome, NOT_PAID))

    async def moved_to(self, user_id: int) -> int | None:
        """Where this Discord ID's account went, if it moved (the web API and the webhooks
        use this to find a moved person)."""
        async with self._db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT new_user_id FROM account_moves WHERE old_user_id = %s", (user_id,)
            )
            row = await cur.fetchone()
        return int(row["new_user_id"]) if row else None

    async def take_moved_notice(self, user_id: int) -> str | None:
        """The one-time line for an old account that turns up again: its DMbot account moved.
        Asking marks it told."""
        async with self._db.unscoped() as conn:
            cur = await conn.execute(
                "UPDATE account_moves SET told_at = %s WHERE old_user_id = %s AND told_at IS NULL"
                " RETURNING moved_at",
                (self._now(), user_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        from datetime import UTC, datetime

        when = datetime.fromtimestamp(int(row["moved_at"]), UTC).strftime("%B %-d, %Y")
        return f"Your DMbot account moved to another Discord account on {when}."
