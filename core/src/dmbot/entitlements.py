"""Reading what a person has paid for (#435, #437), and what they may do (#771).

The `entitlements` table is changed only by the plan writer (the payment webhook and
Try It, dmbot.web, #435) through Database.plan_writer(); the database refuses any other
write. The bot's plan rules (#437) read it here.

Free access (#771; docs/PLAN.md, "Free access and the admin page"): the owner's own
accounts are on the free list (`DMBOT_FREE_USERS`, never in the repository), and the admin
can give others a grant (`access_grants`, written only through Database.grant_writer()).
`effective` is the one answer to "does this person's plan work, and with what caps":
free list, then an active grant, then the paid plan; with a grant and a paid plan both
working, the larger caps win. `entitlements` itself is never changed by this.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Any, Literal, cast

from psycopg import pq

from dmbot import plans
from dmbot.db import Conn, Database
from dmbot.plans import PlanId

log = logging.getLogger(__name__)

Status = Literal["active", "grace", "lapsed"]

# A technical guard, not a plan rule (accepted by web, #469; docs/PLAN.md "Plans and
# pricing"): a renewal's payment event can arrive a little after the period ends, so a
# paying person's game isn't stopped in that gap. A missed lapse event still stops the
# plan after this. Try It has no slack: it ends exactly when its 30 days do.
RENEWAL_SLACK_SECONDS = 3 * 24 * 3600


@dataclass(frozen=True)
class Entitlement:
    user_id: int
    plan: PlanId
    status: Status
    hours_cap: int
    extra_hours: int  # bought for this period
    campaign_cap: int
    period_start: int  # Unix seconds
    period_end: int
    grace_ends_at: int | None
    lapsed_at: int | None
    plan_changed_at: int

    @property
    def hours_this_period(self) -> int:
        return self.hours_cap + self.extra_hours

    def usable(self, now: int) -> bool:
        """The plan works now: active (and its period not long over), or in the payment
        grace and not past it. Try It simply ends with its period."""
        if self.status == "active":
            slack = 0 if self.plan == "try-it" else RENEWAL_SLACK_SECONDS
            return now < self.period_end + slack
        return (
            self.status == "grace" and self.grace_ends_at is not None and now < self.grace_ends_at
        )


AccessKind = Literal["free", "grant", "paid", "none"]
GrantLevel = Literal["guild", "unlimited"]
GRANT_LEVELS: tuple[GrantLevel, ...] = ("guild", "unlimited")
FREE_ACCESS = "Free access"  # what the bot and the account page call it

_free_users: frozenset[int] = frozenset()


def parse_free_users(raw: str) -> frozenset[int]:
    """`DMBOT_FREE_USERS`: Discord ids, comma-separated. Raises ValueError (no id in the
    message: the setting is never shown)."""
    ids = [part.strip() for part in raw.split(",") if part.strip()]
    if not all(p.isascii() and p.isdigit() and 0 < int(p) < 2**63 for p in ids):
        raise ValueError(
            "DMBOT_FREE_USERS must be Discord account numbers (digits only, up to 19), "
            "separated by commas. Fix it in .env and start again."
        )
    return frozenset(int(p) for p in ids)


def configure_free_users(ids: Iterable[int]) -> None:
    """Set the free list once, at start (the bot and the web API). Logged as a count
    only: the ids themselves never go in a log."""
    global _free_users
    _free_users = frozenset(ids)
    log.info("Free access list: %d %s", len(_free_users), "id" if len(_free_users) == 1 else "ids")


def free_users() -> frozenset[int]:
    return _free_users


@dataclass(frozen=True)
class Grant:
    user_id: int
    level: GrantLevel
    ends_at: int | None
    revoked_at: int | None

    def active(self, now: int) -> bool:
        return self.revoked_at is None and (self.ends_at is None or now < self.ends_at)


@dataclass(frozen=True)
class Access:
    """What a person may do now. A cap of None means no cap."""

    kind: AccessKind
    plan_name: str | None  # shown to people: "Free access", or the paid plan's name
    hours_cap: int | None
    campaign_cap: int | None
    backups: bool
    until: int | None = None  # a grant's end, if it has one
    # Covered by free access while a paid plan still works too: its name, so the account
    # page can offer to stop paying (#771).
    still_paying: str | None = None

    @property
    def works(self) -> bool:
        return self.kind != "none"


NO_ACCESS = Access("none", None, 0, 0, False)


def _paying(plan: Entitlement | None, now: int) -> str | None:
    """The name of a plan that works and costs money (not Try It, which is free), for
    "you're still paying for…" under free access."""
    if plan is None or not plan.usable(now):
        return None
    known = plans.load().get(plan.plan)
    if known is None or not known.price_cents:
        return None
    return known.name


def _larger(a: int | None, b: int | None) -> int | None:
    return None if a is None or b is None else max(a, b)


def access_for(user_id: int, plan: Entitlement | None, grant: Grant | None, now: int) -> Access:
    """The rule, pure: free list, then an active grant, then the paid plan; a grant and a
    paid plan together give the larger of each cap."""
    if user_id in _free_users:
        return Access("free", FREE_ACCESS, None, None, True, still_paying=_paying(plan, now))
    paid = None
    if plan is not None and plan.usable(now):
        known = plans.load().get(plan.plan)
        paid = Access(
            "paid",
            known.name if known else plan.plan,
            plan.hours_this_period,
            plan.campaign_cap,
            bool(known and known.backups),
        )
    if grant is not None and grant.active(now):
        if grant.level == "unlimited":
            hours, campaigns = None, None
        else:  # "guild": Guild's caps, from plans.json
            guild = plans.load().by_id["guild"]
            hours, campaigns = guild.hours_per_month, guild.campaigns
        if paid is not None:
            hours, campaigns = _larger(hours, paid.hours_cap), _larger(campaigns, paid.campaign_cap)
        return Access(
            "grant",
            FREE_ACCESS,
            hours,
            campaigns,
            True,
            until=grant.ends_at,
            still_paying=_paying(plan, now),
        )
    return paid or NO_ACCESS


async def effective(conn: Conn, user_id: int, now: int) -> Access:
    """What this person may do now, inside a transaction the caller already has open
    (as entitlements.read): every plan rule asks this."""
    if user_id in _free_users:
        return access_for(user_id, None, None, now)
    async with _as_person(conn, user_id):
        plan = await _read_plan(conn, user_id)
        grant = await _read_grant(conn, user_id)
    return access_for(user_id, plan, grant, now)


async def plan_and_access(
    db: Database, user_id: int, now: int
) -> tuple[Entitlement | None, Access]:
    """The paid plan and what the person may do, from one read (the account page)."""
    async with db.user(user_id) as conn:
        plan = await _read_plan(conn, user_id)
        grant = None if user_id in _free_users else await _read_grant(conn, user_id)
    return plan, access_for(user_id, plan, grant, now)


async def get(db: Database, user_id: int) -> Entitlement | None:
    """This person's plan, or None if they never had one."""
    async with db.user(user_id) as conn:
        return await read(conn, user_id)


async def read(conn: Conn, user_id: int) -> Entitlement | None:
    """This person's plan, inside a transaction the caller already has open (for example
    `/dmbot start`'s server transaction, #437). The person is set only for this one read
    and then put back as it was, so the rest of the caller's transaction sees nothing more
    of anyone's website rows than before."""
    async with _as_person(conn, user_id):
        return await _read_plan(conn, user_id)


@contextlib.asynccontextmanager
async def _as_person(conn: Conn, user_id: int) -> AsyncIterator[None]:
    """The person set for these reads only, then put back as it was."""
    if conn.info.transaction_status != pq.TransactionStatus.INTRANS:
        raise RuntimeError(
            "entitlements.read and effective need an open transaction (use Database.guild)"
        )
    cur = await conn.execute("SELECT current_setting('dmbot.user_id', true) AS before")
    before = await cur.fetchone()
    await conn.execute("SELECT set_config('dmbot.user_id', %s, true)", (str(int(user_id)),))
    try:
        yield
    finally:
        await conn.execute(
            "SELECT set_config('dmbot.user_id', %s, true)",
            ((before or {}).get("before") or "",),
        )


async def _read_grant(conn: Conn, user_id: int) -> Grant | None:
    cur = await conn.execute(
        "SELECT discord_user_id, level, ends_at, revoked_at FROM access_grants"
        " WHERE discord_user_id = %s",
        (user_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return Grant(
        int(row["discord_user_id"]),
        cast(GrantLevel, row["level"]),
        row["ends_at"],
        row["revoked_at"],
    )


async def _read_plan(conn: Conn, user_id: int) -> Entitlement | None:
    cur = await conn.execute(
        "SELECT user_id, plan, status, hours_cap, extra_hours, campaign_cap, period_start,"
        " period_end, grace_ends_at, lapsed_at, plan_changed_at"
        " FROM entitlements WHERE user_id = %s",
        (user_id,),
    )
    row: dict[str, Any] | None = await cur.fetchone()
    if row is None:
        return None
    return Entitlement(
        user_id=row["user_id"],
        plan=cast(PlanId, row["plan"]),
        status=cast(Status, row["status"]),
        hours_cap=row["hours_cap"],
        extra_hours=row["extra_hours"],
        campaign_cap=row["campaign_cap"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        grace_ends_at=row["grace_ends_at"],
        lapsed_at=row["lapsed_at"],
        plan_changed_at=row["plan_changed_at"],
    )
