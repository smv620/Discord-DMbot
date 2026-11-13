"""Small reads about a person's account (#435), shared by the website and the bot (#2547)."""

from __future__ import annotations

import logging
import time

from psycopg import errors as pg_errors

from dmbot.db import Database

log = logging.getLogger(__name__)


async def account_email(db: Database, user_id: int) -> str | None:
    """The account's address: the one the payment company has, else the Discord one."""
    async with db.user(user_id) as conn:
        cur = await conn.execute(
            "SELECT COALESCE(payment_email, email) AS email FROM web_users WHERE user_id = %s",
            (user_id,),
        )
        row = await cur.fetchone()
    return None if row is None else row["email"]


async def payment_email(db: Database, user_id: int) -> str | None:
    """Only the address the payment company has (move codes go here and nowhere else), or
    None while the account has none stored."""
    async with db.user(user_id) as conn:
        cur = await conn.execute(
            "SELECT payment_email FROM web_users WHERE user_id = %s", (user_id,)
        )
        row = await cur.fetchone()
    return None if row is None else row["payment_email"]


async def customer_id(db: Database, user_id: int) -> str | None:
    """The payment company's id for this person, once they have paid."""
    async with db.user(user_id) as conn:
        cur = await conn.execute(
            "SELECT provider_customer_id FROM entitlements WHERE user_id = %s", (user_id,)
        )
        row = await cur.fetchone()
    return None if row is None else row["provider_customer_id"]


async def dmbot_id(db: Database, user_id: int) -> str | None:
    """The account's DMbot ID (base 36, fixed for life), or None if they have no account on
    the website yet."""
    async with db.user(user_id) as conn:
        cur = await conn.execute("SELECT dmbot_id FROM web_users WHERE user_id = %s", (user_id,))
        row = await cur.fetchone()
    return None if row is None else str(row["dmbot_id"])


async def account_of(db: Database, discord_id: int) -> int | None:
    """The DMbot account a Discord ID belongs to, as the account's number, or None (#2533).

    One Discord ID is its own account's number while it is the account's first ID and still in
    use; an added Discord ID gives the account it was added to; a first ID the account took off
    itself, and an ID nobody has used, give None. This is the only place that maps a Discord ID
    to an account: no other code decides whose Discord ID is whose."""
    async with db.unscoped() as conn:
        cur = await conn.execute("SELECT dmbot_account_of(%s) AS a", (int(discord_id),))
        row = await cur.fetchone()
    return None if row is None or row["a"] is None else int(row["a"])


async def user_of_dmbot_id(db: Database, dmbot_id: str) -> int | None:
    """The Discord ID that holds this DMbot ID now (it changes when the account moves)."""
    async with db.unscoped() as conn:
        cur = await conn.execute("SELECT dmbot_user_of(%s) AS u", (dmbot_id,))
        row = await cur.fetchone()
    return None if row is None or row["u"] is None else int(row["u"])


async def record_install(
    db: Database, user_id: int, guild_id: int, *, now: int, session: str
) -> str:
    """DMbot was added to this server through the website by this person (#435).
    Returns "recorded", or "already_linked" when someone else is already the installer
    (DMbot is in the server either way; the first installer stays)."""
    # Someone else's install is left alone. Migration 0014 relied on Postgres applying the upsert's
    # WHERE before the update policy (so it skipped quietly); Postgres 16.16 checks the policy
    # first and raises instead. So the installer is read first, and a policy error is taken as
    # "already linked" only when the row now shows another installer (two people installing at
    # once); for a server the person may not touch the error stands (row-level security refuses).
    try:
        async with db.user(user_id, install_guild=guild_id, session=session) as conn:
            cur = await conn.execute(
                "SELECT installed_by_user_id FROM installs WHERE guild_id = %s", (guild_id,)
            )
            row = await cur.fetchone()
            if row is not None and row["installed_by_user_id"] not in (None, user_id):
                return "already_linked"
            cur = await conn.execute(
                "INSERT INTO installs (guild_id, installed_by_user_id, installed_at, via)"
                " VALUES (%s, %s, %s, 'site')"
                " ON CONFLICT (guild_id) DO UPDATE SET"
                "   installed_by_user_id = EXCLUDED.installed_by_user_id, via = 'site',"
                "   left_at = NULL"
                " WHERE installs.installed_by_user_id IS NULL"
                "   OR installs.installed_by_user_id = EXCLUDED.installed_by_user_id"
                " RETURNING guild_id",
                (guild_id, user_id, now),
            )
            return "recorded" if await cur.fetchone() is not None else "already_linked"
    except pg_errors.InsufficientPrivilege:
        async with db.user(user_id, install_guild=guild_id, session=session) as conn:
            cur = await conn.execute(
                "SELECT installed_by_user_id FROM installs WHERE guild_id = %s", (guild_id,)
            )
            row = await cur.fetchone()
        if row is not None and row["installed_by_user_id"] not in (None, user_id):
            return "already_linked"
        raise


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


NAME_MAX = 100  # a Discord name is at most 32 characters; the rest is a ceiling, not a target


async def remember_display_name(db: Database, user_id: int, name: str) -> bool:
    """Keep a person's Discord name on their account, so the personal pages can greet them
    ("Hello, <name>", #1231) even when they have never signed in to the website since the name
    was kept (#1242 item 2). Only the name, only for an account that already exists (the bot
    never makes one), and only when it differs. True if it changed."""
    name = " ".join(name.split())[:NAME_MAX]
    if not name:
        return False
    async with db.user(user_id) as conn:
        cur = await conn.execute(
            "UPDATE web_users SET display_name = %s WHERE user_id = %s"
            " AND display_name IS DISTINCT FROM %s",
            (name, user_id, name),
        )
        return cur.rowcount == 1


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


async def delete_person(db: Database, user_id: int, *, now: int | None = None) -> None:
    """Delete the account: the person, their plan and sessions go (the database cascades),
    and so does any free access they were given (the grants log keeps the Discord id and
    what was done, for the admin);
    installs keep the server but lose the person. What's kept: the Discord id in
    try_it_used (one free trial per person) and payment events with the Discord id and the
    subscription id (so a replayed payment can't apply twice, and a cancelled subscription's
    last news isn't retried): ids only (privacy page, #433, #571). The person's sent-email
    records go (#1287), and so do their support tickets (#1340); the never-email-me-again
    list (email_unsubscribed: a Discord id, the kind and a time) stays, so a returning
    account is never emailed against its wish.

    The campaigns the person owns are **kept**, ownerless, so the players can still read and
    download them: they go 120 days later unless another DM takes them on (#1059; owner,
    2026-10-11). A database function does that across every server (0047)."""
    async with db.user(user_id) as conn:
        cur = await conn.execute(
            "SELECT dmbot_orphan_owned_campaigns(%s) AS n",
            (int(time.time()) if now is None else now,),
        )
        row = await cur.fetchone()
        log.info("Account deletion: %s campaign(s) kept, ownerless", row["n"] if row else 0)
        # The account's history of earlier Discord accounts goes with it (#1230).
        await conn.execute("SELECT dmbot_forget_account_history()")
        await conn.execute("SELECT dmbot_forget_email_records()")
        # A start-up wizard draft is the person's own answers and documents: it goes in every
        # server (#3955).
        await conn.execute("SELECT dmbot_forget_wizard()")
        # The AI credit totals are the person's Discord ID too; the ledger rows keep their numbers
        # with the owner set to nobody (#4094).
        await conn.execute("SELECT dmbot_forget_credits()")
        # The listening hours are the person's Discord ID too: the month totals go, and each
        # campaign's minutes stay with the owner set to nobody (#4132).
        await conn.execute("SELECT dmbot_forget_hours()")
        # The person's own requests and records go; authorship and transcripts are the campaign's
        # (#4141).
        await conn.execute("SELECT dmbot_forget_person()")
        # What they wrote or decided for a campaign stays, with nobody as the author (#4141).
        await conn.execute("SELECT dmbot_forget_authorship()")
        # More of their own requests and codes (#4148).
        await conn.execute("SELECT dmbot_forget_requests()")
        # Who decided or set a campaign's records goes to 0; the records stay (#4148).
        await conn.execute("SELECT dmbot_forget_decisions()")
        # The DM's list of players they remember, and every list that names the person (#4226).
        await conn.execute("SELECT dmbot_forget_dm_players()")
        # The kept trial and unsubscribe records lose the Discord ID where they have its keyed hash
        # (#4145); a row with no hash yet keeps the ID, as before.
        await conn.execute("SELECT dmbot_forget_person_ids()")
        await conn.execute("DELETE FROM web_users WHERE user_id = %s", (user_id,))
        # Free access goes with the account (#771). It isn't linked to web_users (a grant
        # works for someone who never signed in), so it's deleted by hand.
        await conn.execute("DELETE FROM access_grants WHERE discord_user_id = %s", (user_id,))
        # Support tickets are the person's own words, so they go too (#1340). No WHERE on
        # purpose: a WHERE needs the right to read the rows, which a person doesn't have; the
        # table's own_delete policy already holds this DELETE to the person's own tickets.
        await conn.execute("DELETE FROM support_tickets")
