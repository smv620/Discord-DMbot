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


async def record_install(
    db: Database, user_id: int, guild_id: int, *, now: int, session: str
) -> str:
    """DMbot was added to this server through the website by this person (#435).
    Returns "recorded", or "already_linked" when someone else is already the installer
    (DMbot is in the server either way; the first installer stays)."""
    async with db.user(user_id, install_guild=guild_id, session=session) as conn:
        # The write contract (schema.py, migration 0014): someone else's install is left
        # alone, quietly (no row comes back).
        cur = await conn.execute(
            "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via)"
            " VALUES (%s, %s, %s, 'site')"
            " ON CONFLICT (guild_id) DO UPDATE SET"
            "   installed_by_user_id = EXCLUDED.installed_by_user_id, via = 'site', left_at = NULL"
            " WHERE installs.installed_by_user_id IS NULL"
            "   OR installs.installed_by_user_id = EXCLUDED.installed_by_user_id"
            " RETURNING guild_id",
            (guild_id, user_id, now),
        )
        return "recorded" if await cur.fetchone() is not None else "already_linked"


async def link_install(db: Database, user_id: int, guild_id: int, *, session: str) -> str:
    """Fill in who added DMbot to a server it joined through a plain link. Returns
    "linked", "already_linked" (someone else did), or "not_installed" (DMbot isn't
    there, or has left)."""
    async with db.user(user_id, install_guild=guild_id, session=session) as conn:
        cur = await conn.execute(
            "SELECT installed_by_user_id FROM installs WHERE guild_id = %s AND left_at IS NULL",
            (guild_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return "not_installed"  # the bot records its joins; it isn't in this server
        # One statement decides, so two people linking at once can't both win.
        cur = await conn.execute(
            "UPDATE installs SET installed_by_user_id = %s"
            " WHERE guild_id = %s AND left_at IS NULL"
            "   AND (installed_by_user_id IS NULL OR installed_by_user_id = %s)",
            (user_id, guild_id, user_id),
        )
    return "linked" if cur.rowcount == 1 else "already_linked"


async def active_subscription(db: Database, user_id: int) -> tuple[str, str] | None:
    """(payment company, subscription id) of a paid plan that would charge again."""
    async with db.user(user_id) as conn:
        cur = await conn.execute(
            "SELECT provider, provider_subscription_id FROM entitlements"
            " WHERE user_id = %s AND status <> 'lapsed' AND provider_subscription_id IS NOT NULL",
            (user_id,),
        )
        row = await cur.fetchone()
    return None if row is None else (row["provider"], row["provider_subscription_id"])


async def delete_person(db: Database, user_id: int) -> None:
    """Delete the account: the person, their plan and sessions go (the database cascades);
    installs keep the server but lose the person. What's kept: the Discord id in
    try_it_used (one free trial per person) and payment events with the Discord id and the
    subscription id (so a replayed payment can't apply twice, and a cancelled subscription's
    last news isn't retried): ids only (privacy page, #433, #571)."""
    async with db.user(user_id) as conn:
        await conn.execute("DELETE FROM web_users WHERE user_id = %s", (user_id,))
