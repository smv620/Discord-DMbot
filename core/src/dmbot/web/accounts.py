"""Small reads about a person's account for the web API (#435)."""

from __future__ import annotations

from dmbot.db import Database


async def account_email(db: Database, user_id: int) -> str | None:
    async with db.user(user_id) as conn:
        cur = await conn.execute("SELECT email FROM web_users WHERE user_id = %s", (user_id,))
        row = await cur.fetchone()
    return None if row is None else row["email"]


async def customer_id(db: Database, user_id: int) -> str | None:
    """The payment company's id for this person, once they have paid."""
    async with db.user(user_id) as conn:
        cur = await conn.execute(
            "SELECT provider_customer_id FROM entitlements WHERE user_id = %s", (user_id,)
        )
        row = await cur.fetchone()
    return None if row is None else row["provider_customer_id"]


async def first_paid_month_after_trial(db: Database, user_id: int) -> bool:
    """Used Try It and never paid before: the first month of Table is $1.99 (#432)."""
    async with db.user(user_id) as conn:
        cur = await conn.execute(
            "SELECT EXISTS (SELECT 1 FROM try_it_used WHERE user_id = %(u)s) AS tried,"
            " EXISTS (SELECT 1 FROM payment_events WHERE user_id = %(u)s) AS paid",
            {"u": user_id},
        )
        row = await cur.fetchone()
    return bool(row and row["tried"] and not row["paid"])
