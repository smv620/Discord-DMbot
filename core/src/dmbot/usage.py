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

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class Standing:
    """An owner's hours at one moment: what they may do, the month the hours belong to,
    and the minutes recorded in it (before and after what was just added)."""

    access: entitlements.Access
    month: hours.Month
    used_before: int
    used_after: int
    grace_session: int | None = None  # the session given this month's grace, if any


async def standing_of(conn: Conn, owner_user_id: int, now: int) -> Standing:
    """The owner's access, month and recorded minutes. Needs an open transaction with the
    owner set (Database.meter)."""
    plan, grant, access = await entitlements.inputs(conn, owner_user_id, now)
    month = hours.month_for(access, plan, grant.granted_at if grant else None, now) or (
        calendar_month(now)
    )
    used, grace = await _hours_row(conn, owner_user_id, month)
    return Standing(access, month, used, used, grace)


async def month_of(conn: Conn, owner_user_id: int, now: int) -> hours.Month:
    """The month this owner's hours belong to now. Needs an open transaction."""
    return (await standing_of(conn, owner_user_id, now)).month


async def check_start(db: Database, guild_id: int, owner_user_id: int, now: int) -> StartCheck:
    """May this owner's campaign start listening? Reads only; the bot refuses with
    `hours.refusal` when the verdict isn't "ok"."""
    async with db.meter(guild_id, owner_user_id) as conn:
        s = await standing_of(conn, owner_user_id, now)
    return StartCheck(hours.start_verdict(s.access, s.used_before), s.month)


@dataclass(frozen=True, slots=True)
class StartCheck:
    verdict: hours.Verdict
    month: hours.Month


async def add_minutes(
    db: Database,
    *,
    guild_id: int,
    campaign_id: str,
    owner_user_id: int,
    session_started_at: int,
    minutes: int,
    now: int,
) -> Standing:
    """Add listening minutes for this owner, in both tables at once. Returns where their
    hours stand now (the month they were recorded in, and the minutes before and after)."""
    async with db.meter(guild_id, owner_user_id) as conn:
        s = await standing_of(conn, owner_user_id, now)
        if minutes <= 0:
            return s
        await conn.execute(
            "INSERT INTO session_usage (guild_id, campaign_id, session_started_at,"
            " owner_user_id, minutes, updated_at) VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (guild_id, campaign_id, session_started_at, owner_user_id)"
            " DO UPDATE SET minutes = session_usage.minutes + EXCLUDED.minutes,"
            " updated_at = EXCLUDED.updated_at",
            (guild_id, campaign_id, session_started_at, owner_user_id, minutes, now),
        )
        # The row lock this upsert takes serialises two campaigns of one owner ticking at
        # once; the total it returns, not the earlier read, says where the hours stand now.
        cur = await conn.execute(
            "INSERT INTO owner_hours (owner_user_id, month_start, minutes)"
            " VALUES (%s, %s, %s) ON CONFLICT (owner_user_id, month_start)"
            " DO UPDATE SET minutes = owner_hours.minutes + EXCLUDED.minutes"
            " RETURNING minutes, grace_session",
            (owner_user_id, s.month.start, minutes),
        )
        row = await cur.fetchone()
    after = int(row["minutes"]) if row else s.used_before + minutes
    grace = None if row is None or row["grace_session"] is None else int(row["grace_session"])
    return Standing(s.access, s.month, after - minutes, after, grace)


async def _hours_row(conn: Conn, owner_user_id: int, month: hours.Month) -> tuple[int, int | None]:
    cur = await conn.execute(
        "SELECT minutes, grace_session FROM owner_hours"
        " WHERE owner_user_id = %s AND month_start = %s",
        (owner_user_id, month.start),
    )
    row = await cur.fetchone()
    if row is None:
        return 0, None
    grace = row["grace_session"]
    return int(row["minutes"]), None if grace is None else int(grace)


async def add_unowned_minutes(
    db: Database,
    *,
    guild_id: int,
    campaign_id: str,
    session_started_at: int,
    minutes: int,
    now: int,
) -> None:
    """Listening minutes while the campaign had no owner: kept in the campaign's own
    session record (owner 0) and nowhere else. Nobody's hours are spent, and whoever takes
    the campaign on later is billed only from then."""
    async with db.guild(guild_id) as conn:
        await conn.execute(
            "INSERT INTO session_usage (guild_id, campaign_id, session_started_at,"
            " owner_user_id, minutes, updated_at) VALUES (%s, %s, %s, 0, %s, %s)"
            " ON CONFLICT (guild_id, campaign_id, session_started_at, owner_user_id)"
            " DO UPDATE SET minutes = session_usage.minutes + EXCLUDED.minutes,"
            " updated_at = EXCLUDED.updated_at",
            (guild_id, campaign_id, session_started_at, minutes, now),
        )


async def minutes_this_month(conn: Conn, owner_user_id: int, month: hours.Month) -> int:
    """The owner's recorded minutes in this month, from any server. Reads only the
    owner's own row; needs an open transaction with that owner set (Database.meter or
    Database.user)."""
    return (await _hours_row(conn, owner_user_id, month))[0]


async def start_grace(
    db: Database,
    guild_id: int,
    owner_user_id: int,
    month: hours.Month,
    session_started_at: int,
) -> bool:
    """Give this session the month's grace, if no session has had it yet. True if it got
    it; False if another session did first (two campaigns reaching the cap together)."""
    async with db.meter(guild_id, owner_user_id) as conn:
        cur = await conn.execute(
            "UPDATE owner_hours SET grace_session = %s"
            " WHERE owner_user_id = %s AND month_start = %s AND grace_session IS NULL"
            " RETURNING grace_session",
            (session_started_at, owner_user_id, month.start),
        )
        return await cur.fetchone() is not None


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
    ) -> Standing:
        return await add_minutes(
            self.db,
            guild_id=guild_id,
            campaign_id=campaign_id,
            owner_user_id=owner_user_id,
            session_started_at=session_started_at,
            minutes=minutes,
            now=now,
        )

    async def add_unowned(
        self,
        *,
        guild_id: int,
        campaign_id: str,
        session_started_at: int,
        minutes: int,
        now: int,
    ) -> None:
        await add_unowned_minutes(
            self.db,
            guild_id=guild_id,
            campaign_id=campaign_id,
            session_started_at=session_started_at,
            minutes=minutes,
            now=now,
        )

    async def recorded(self, guild_id: int, campaign_id: str, started_at: int) -> int:
        return await session_minutes(self.db, guild_id, campaign_id, started_at)

    async def start_grace(
        self, guild_id: int, owner_user_id: int, month: hours.Month, session_started_at: int
    ) -> bool:
        return await start_grace(self.db, guild_id, owner_user_id, month, session_started_at)

    async def check(self, guild_id: int, owner_user_id: int, now: int) -> StartCheck:
        return await check_start(self.db, guild_id, owner_user_id, now)
