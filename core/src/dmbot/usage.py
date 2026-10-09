"""Recording DMbot's listening minutes (#437 part 2; docs/PLAN.md, "Plans and pricing").

The rules (which month, how many minutes) are in `dmbot.hours`; this module writes them
down. Every minute goes to two tables in one transaction, through `Database.meter()`:
`session_usage` (that campaign's session, in its server's scope) and `owner_hours` (the
campaign owner's total for the plan month, numbers only, readable by that owner and
across servers because it holds no server or campaign). Minutes count toward whoever owns
the campaign when they are recorded, so a hand-over mid-session moves the later minutes.

The meter records for everyone. Whether a refusal follows is a separate switch
(`DMBOT_ENFORCE_PLANS`), so recording can start before anyone is locked out.
"""

from __future__ import annotations

from datetime import UTC, datetime

from dmbot import entitlements, hours
from dmbot.db import Conn, Database


def calendar_month(now: int) -> hours.Month:
    """The UTC calendar month, for people with no plan month of their own (the free list,
    "no limits" access): their minutes are still recorded, just not limited."""
    when = datetime.fromtimestamp(now, UTC)
    start = when.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = start.replace(year=start.year + (start.month == 12), month=start.month % 12 + 1)
    return hours.Month(int(start.timestamp()), int(nxt.timestamp()))


async def month_of(conn: Conn, owner_user_id: int, now: int) -> hours.Month:
    """The month this owner's hours belong to now. Needs an open transaction."""
    plan, grant, access = await entitlements.inputs(conn, owner_user_id, now)
    return hours.month_for(access, plan, grant.granted_at if grant else None, now) or (
        calendar_month(now)
    )


async def add_minutes(
    db: Database,
    *,
    guild_id: int,
    campaign_id: str,
    owner_user_id: int,
    session_started_at: int,
    minutes: int,
    now: int,
) -> hours.Month:
    """Add listening minutes for this owner, in both tables at once. Returns the month
    they were recorded in."""
    async with db.meter(guild_id, owner_user_id) as conn:
        month = await month_of(conn, owner_user_id, now)
        if minutes > 0:  # (nothing to add still names the month)
            await conn.execute(
                "INSERT INTO session_usage (guild_id, campaign_id, session_started_at,"
                " owner_user_id, minutes, updated_at) VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (guild_id, campaign_id, session_started_at, owner_user_id)"
                " DO UPDATE SET minutes = session_usage.minutes + EXCLUDED.minutes,"
                " updated_at = EXCLUDED.updated_at",
                (guild_id, campaign_id, session_started_at, owner_user_id, minutes, now),
            )
            await conn.execute(
                "INSERT INTO owner_hours (owner_user_id, month_start, minutes)"
                " VALUES (%s, %s, %s) ON CONFLICT (owner_user_id, month_start)"
                " DO UPDATE SET minutes = owner_hours.minutes + EXCLUDED.minutes",
                (owner_user_id, month.start, minutes),
            )
    return month


async def minutes_this_month(conn: Conn, owner_user_id: int, month: hours.Month) -> int:
    """The owner's recorded minutes in this month, from any server. Reads only the
    owner's own row; needs an open transaction with that owner set (Database.meter or
    Database.user)."""
    cur = await conn.execute(
        "SELECT minutes FROM owner_hours WHERE owner_user_id = %s AND month_start = %s",
        (owner_user_id, month.start),
    )
    row = await cur.fetchone()
    return int(row["minutes"]) if row else 0


async def session_minutes(db: Database, guild_id: int, campaign_id: str, started_at: int) -> int:
    """Minutes already recorded for one session, for every owner it had: a session picked
    up again after a restart carries on from here and never counts a minute twice."""
    async with db.guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT COALESCE(SUM(minutes), 0) AS n FROM session_usage"
            " WHERE campaign_id = %s AND session_started_at = %s",
            (campaign_id, started_at),
        )
        row = await cur.fetchone()
    return int(row["n"]) if row else 0


class Meter:
    """The bot's handle on the hours meter: the database to write to. The bot gets one
    only when it runs for real; without one (tests, tools) nothing is recorded."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(
        self,
        *,
        guild_id: int,
        campaign_id: str,
        owner_user_id: int,
        session_started_at: int,
        minutes: int,
        now: int,
    ) -> hours.Month:
        return await add_minutes(
            self.db,
            guild_id=guild_id,
            campaign_id=campaign_id,
            owner_user_id=owner_user_id,
            session_started_at=session_started_at,
            minutes=minutes,
            now=now,
        )

    async def recorded(self, guild_id: int, campaign_id: str, started_at: int) -> int:
        return await session_minutes(self.db, guild_id, campaign_id, started_at)
