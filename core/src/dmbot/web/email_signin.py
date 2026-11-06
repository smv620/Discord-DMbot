"""Website sign-in by an emailed link (#1231; docs/PLAN.md, "Accounts").

A person types their email; if it belongs to one account, a one-use link good for 15 minutes is
emailed. The caller always answers the same words whether or not it did, so the page never tells
anyone whether an address has an account. Only the link's SHA-256 is stored, like a session
cookie, so a database copy can't sign anyone in.

The link points at the website, with the token after a `#`, so it is never sent to a server or
written in a log; the page then posts it here. A mail program that opens links to check them
(many do) therefore can't use it up.
"""

from __future__ import annotations

import secrets

from dmbot.db import Database

# The cleaner moved to dmbot.email_address (#2547). The `as` form keeps the name exported here
# for the website's callers, and mypy strict needs it spelled that way.
from dmbot.email_address import clean_email as clean_email
from dmbot.web.sessions import hash_token

LINK_SECONDS = 15 * 60


async def make_link(db: Database, email: str, *, now: int) -> tuple[int, str] | None:
    """The account this address belongs to, and a new sign-in token for it: None when no one
    (or two accounts) has the address. The caller emails the token and tells no one the answer.
    The link's id (for the email log) is `link_id(token)`."""
    async with db.anonymous() as conn:
        cur = await conn.execute("SELECT dmbot_user_by_email(%s) AS user_id", (email,))
        row = await cur.fetchone()
    if row is None or row["user_id"] is None:
        return None
    user_id = int(row["user_id"])
    token = secrets.token_urlsafe(32)
    async with db.user(user_id) as conn:
        # Two asks at once must leave one live link, not two: the account's row is locked while
        # the old links go and the new one is made, so the second ask waits for the first.
        await conn.execute("SELECT 1 FROM web_users WHERE user_id = %s FOR UPDATE", (user_id,))
        # One live link at a time: asking again spends the earlier ones (and clears old and
        # used ones), so a link in an old email, or one a stranger triggered, stops working.
        await conn.execute("DELETE FROM email_sign_in_links WHERE user_id = %s", (user_id,))
        await conn.execute(
            "INSERT INTO email_sign_in_links (token_hash, user_id, created_at, expires_at)"
            " VALUES (%s, %s, %s, %s)",
            (hash_token(token), user_id, now, now + LINK_SECONDS),
        )
    return user_id, token


def link_id(token: str) -> str:
    """A short id for a link that names no secret (the start of its stored hash), for the
    email log's kind: `sign-in:<id>`, so each link is its own email."""
    return hash_token(token)[:16]


async def redeem(db: Database, token: str, *, now: int) -> int | None:
    """Use a link: the account it signs in, or None if it is wrong, expired or already used.
    One statement, so two people (or two tabs) racing with the same link get one winner."""
    if not token or len(token) > 200:
        return None
    async with db.session(hash_token(token)) as conn:
        cur = await conn.execute(
            "UPDATE email_sign_in_links SET used_at = %s"
            " WHERE token_hash = %s AND used_at IS NULL AND expires_at > %s RETURNING user_id",
            (now, hash_token(token), now),
        )
        row = await cur.fetchone()
    return None if row is None else int(row["user_id"])
