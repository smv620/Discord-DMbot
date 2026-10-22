"""Changing the email DMbot uses, from the account page (#2349; docs/PLAN.md, "Accounts").

The address in `web_users.email` is the one sign-in by email and the recap emails use. A person
asks for a new one; a link is mailed to the NEW address and nothing changes until it is confirmed
by the same signed-in account (15 minutes, one use, only its SHA-256 stored). The old address is
told when it changes, so a stolen session cannot quietly move email sign-in. An address the
payment company holds is changed in Stripe, not here. Addresses are never logged.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from typing import Literal

from dmbot.db import Database
from dmbot.web.email_signin import LINK_SECONDS, clean_email
from dmbot.web.sessions import hash_token

Source = Literal["payment", "signin", "none"]
Refusal = Literal["bad_email", "not_allowed", "in_use"]


@dataclass(frozen=True, slots=True)
class Current:
    address: str | None
    source: Source
    pending: str | None  # the address a link was sent to, until it is confirmed or ends


def source_of(payment_email: str | None, email: str | None) -> tuple[str | None, Source]:
    """The address mail goes to and where it came from: the same choice as `recaps/notify.py`
    (the payment company's, else the sign-in one; an empty address is none)."""
    if payment_email:
        return payment_email, "payment"
    if email:
        return email, "signin"
    return None, "none"


async def read(db: Database, user_id: int, *, now: int) -> Current:
    async with db.user(user_id) as conn:
        cur = await conn.execute(
            "SELECT email, payment_email FROM web_users WHERE user_id = %s", (user_id,)
        )
        row = await cur.fetchone()
        waiting = await conn.execute(
            "SELECT new_email FROM email_changes WHERE user_id = %s AND expires_at > %s",
            (user_id, now),
        )
        pending = await waiting.fetchone()
    address, source = source_of(
        row["payment_email"] if row else None, row["email"] if row else None
    )
    return Current(address, source, pending["new_email"] if pending else None)


async def ask(
    db: Database, user_id: int, raw: object, *, now: int
) -> tuple[str, str] | Refusal | None:
    """Start a change: (the new address, the link's token) to mail, or why not. None: it is the
    address already in use, so there is nothing to change. A new ask replaces a waiting one."""
    new = clean_email(raw)
    if new is None:
        return "bad_email"
    async with db.user(user_id) as conn:
        cur = await conn.execute(
            "SELECT email, payment_email FROM web_users WHERE user_id = %s FOR UPDATE", (user_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return "not_allowed"
        if row["payment_email"]:  # changed in Stripe: the payment company's address wins
            return "not_allowed"
        if (row["email"] or "").lower() == new:
            return None
        taken = await conn.execute("SELECT dmbot_email_taken(%s) AS taken", (new,))
        if (await taken.fetchone() or {}).get("taken"):
            return "in_use"
        token = secrets.token_urlsafe(32)
        await conn.execute(
            "INSERT INTO email_changes (user_id, new_email, token_hash, created_at, expires_at)"
            " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET"
            " new_email = EXCLUDED.new_email, token_hash = EXCLUDED.token_hash,"
            " created_at = EXCLUDED.created_at, expires_at = EXCLUDED.expires_at",
            (user_id, new, hash_token(token), now, now + LINK_SECONDS),
        )
    return new, token


def link_id(token: str) -> str:
    """A short id for a link that names no secret, for the email log's kind."""
    return hash_token(token)[:16]


async def cancel(db: Database, user_id: int) -> None:
    async with db.user(user_id) as conn:
        await conn.execute("DELETE FROM email_changes WHERE user_id = %s", (user_id,))


async def confirm(
    db: Database, user_id: int, token: object, *, now: int
) -> tuple[str | None, str] | Literal["in_use"] | None:
    """Use the link: (the old address, the new one), or None for a bad, used, expired or someone
    else's link. One use: the row goes with it. Checked again that no other account took the
    address meanwhile."""
    if not isinstance(token, str) or not token or len(token) > 200:
        return None
    async with db.user(user_id) as conn:
        cur = await conn.execute(
            "SELECT new_email, token_hash FROM email_changes WHERE user_id = %s"
            " AND expires_at > %s FOR UPDATE",
            (user_id, now),
        )
        row = await cur.fetchone()
        if row is None or not hmac.compare_digest(row["token_hash"], hash_token(token)):
            return None
        new = str(row["new_email"])
        taken = await conn.execute("SELECT dmbot_email_taken(%s) AS taken", (new,))
        if (await taken.fetchone() or {}).get("taken"):
            await conn.execute("DELETE FROM email_changes WHERE user_id = %s", (user_id,))
            return "in_use"
        before = await conn.execute(
            "SELECT email FROM web_users WHERE user_id = %s FOR UPDATE", (user_id,)
        )
        old = (await before.fetchone() or {}).get("email")
        await conn.execute("UPDATE web_users SET email = %s WHERE user_id = %s", (new, user_id))
        await conn.execute("DELETE FROM email_changes WHERE user_id = %s", (user_id,))
    return old, new
