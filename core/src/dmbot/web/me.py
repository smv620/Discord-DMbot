"""`GET /me`: what the account page shows (#434, #435). The shape matches the website's
contract, web/src/account/api.ts (`Me`).

Campaigns are read server by server with that server's row-level security, and only for
servers in the person's own Discord list from sign-in, where they are a DM. A server's
other campaigns, and servers the person isn't in, are never read.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from dmbot import consent, entitlements, hours
from dmbot.campaigns.models import HANDOVER_SECONDS
from dmbot.db import Database
from dmbot.web import support
from dmbot.web.accounts import dmbot_id
from dmbot.web.entitlements_writer import PAID_EVENT_SQL, PAID_KINDS
from dmbot.web.moving import can_move
from dmbot.web.offers import offer_ref
from dmbot.web.sessions import Session

# A person in many servers: read at most this many (Discord's own list stops at 200).
MAX_GUILDS = 200


def _recap_audio_view(
    plan: entitlements.Entitlement | None, access: entitlements.Access, now: int, for_sale: bool
) -> dict[str, Any]:
    included = access.kind in ("free", "grant") or (
        plan is not None and plan.usable(now) and plan.plan in entitlements.RECAP_AUDIO_PLANS
    )
    add_on = (
        not included
        and plan is not None
        and plan.recap_audio_until is not None
        and now < plan.recap_audio_until
        and access.recap_audio
    )
    working = plan is not None and plan.usable(now) and plan.plan != "try-it" and plan.has_customer
    return (
        {"included": included, "addOn": bool(add_on)}
        | ({"endsOn": _iso_date(plan.recap_audio_until)} if add_on and plan is not None else {})
        | {"canBuy": bool(for_sale and working and not included and not add_on)}
    )


def _iso_time(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, UTC).isoformat().replace("+00:00", "Z")


def _plan_state(plan: entitlements.Entitlement, now: int) -> dict[str, Any]:
    """The plan line's state and its dates (#2165). Dates are ISO days; the page words them."""
    expired = plan.status == "lapsed" or (plan.status == "active" and not plan.usable(now))
    if expired:
        ended = plan.lapsed_at or plan.ends_at or plan.period_end
        return {"state": "expired", "endedOn": _iso_date(ended)}
    if plan.ends_at is not None:
        return {"state": "ending"}
    if plan.next_plan and plan.next_plan != plan.plan and plan.next_plan_on is not None:
        return {
            "state": "changing",
            "nextPlan": plan.next_plan,
            "nextPlanOn": _iso_date(plan.next_plan_on),
        }
    return {"state": "active"}


def _iso_date(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, UTC).date().isoformat()


async def build_me(
    db: Database,
    session: Session,
    *,
    now: int,
    recap_audio_for_sale: bool = False,
    imports_for: Callable[[int], bool] = lambda guild_id: False,
) -> dict[str, Any]:
    plan, grant, access = await entitlements.plan_grant_access(db, session.user_id, now)
    # The month whose hours count now (None: no meter), and the minutes used in it, read through
    # the database door the website's role has to the owner's hours (0048).
    month = hours.month_for(access, plan, grant.granted_at if grant else None, now)
    used_minutes = 0
    campaigns: list[dict[str, Any]] = []
    servers: list[dict[str, Any]] = []
    incoming: list[dict[str, Any]] = []
    recording: list[dict[str, Any]] = []
    outgoing: list[dict[str, Any]] = []
    names = {g.id: g.name for g in session.guilds}  # for the installs list below
    plan_works = plan is not None and plan.usable(now) and plan.status != "lapsed"

    async with db.user(session.user_id, session=session.id_hash) as conn:
        # One batch (a pipeline), not hundreds of round trips. Each server's statements
        # switch the transaction to that server first, so row-level security shows only
        # its rows, exactly as Database.guild() would. The person and their session are
        # set throughout: the website's role sees a server only if it's in this
        # session's own server list (#498).
        pending = []
        async with conn.pipeline():
            for guild in session.guilds[:MAX_GUILDS]:
                await conn.execute(
                    "SELECT set_config('dmbot.guild_id', %s, true)", (str(int(guild.id)),)
                )
                mine = await conn.execute(
                    "SELECT c.id, c.name, c.last_played_at, c.owner_user_id FROM campaigns c"
                    " JOIN campaign_dms d ON d.campaign_id = c.id AND d.guild_id = c.guild_id"
                    " WHERE c.guild_id = %s AND d.user_id = %s ORDER BY c.name",
                    (guild.id, session.user_id),
                )
                # Hand-over offers this person sent or was sent here (#614). Only open
                # ones under 7 days old: an older one is marked expired by the store the
                # next time anyone touches it, but has already ended.
                offered = await conn.execute(
                    "SELECT o.id, o.campaign_id, c.name, o.to_user_id, o.from_name,"
                    " o.to_name, o.created_at FROM campaign_handover_offers o"
                    " JOIN campaigns c ON c.id = o.campaign_id AND c.guild_id = o.guild_id"
                    " WHERE o.guild_id = %(g)s AND o.status = 'open'"
                    "   AND o.created_at + %(days)s > %(now)s"
                    "   AND %(me)s IN (o.from_user_id, o.to_user_id)"
                    " ORDER BY o.created_at",
                    {"g": guild.id, "days": HANDOVER_SECONDS, "now": now, "me": session.user_id},
                )
                # Where DMbot may record this person here (#1151): their own consent row.
                agreed = await conn.execute(
                    "SELECT granted_at, terms_version FROM consent"
                    " WHERE guild_id = %s AND user_id = %s",
                    (guild.id, session.user_id),
                )
                here = None
                if guild.manage:
                    here = await conn.execute(
                        "SELECT EXISTS (SELECT 1 FROM installs WHERE guild_id = %(g)s"
                        "   AND left_at IS NULL) AS installed,"
                        " EXISTS (SELECT 1 FROM installs WHERE guild_id = %(g)s) AS recorded,"
                        " (SELECT installed_by_user_id FROM installs WHERE guild_id = %(g)s)"
                        "   AS installer,"
                        " EXISTS (SELECT 1 FROM campaigns WHERE guild_id = %(g)s) AS played",
                        {"g": guild.id},
                    )
                pending.append((guild, mine, offered, here, agreed))
        for guild, mine, offered, here, agreed in pending:
            for row in await mine.fetchall():
                campaigns.append(
                    {
                        "id": str(row["id"]),
                        # The campaign's address, as the other pages' (`<server>.<campaign>`), so
                        # the account page can link to the campaign's own page (#2416).
                        "ref": f"{guild.id}.{row['id']}",
                        "name": row["name"],
                        "serverName": guild.name,
                        "lastPlayedAt": _iso_time(row["last_played_at"]),
                        # Paused campaigns arrive with #437 part 2; until then every one
                        # is active. "owner": it uses this person's plan, and only they
                        # can hand it over (#437); a campaign with no owner yet (from
                        # before owners were recorded) is "co-dm" for everyone.
                        "status": "active",
                        "role": "owner" if row["owner_user_id"] == session.user_id else "co-dm",
                        # Whether importing older sessions works in its server (#2259), so the
                        # page shows the importer only where it does.
                        "importsOn": imports_for(guild.id),
                    }
                )
            for row in await offered.fetchall():
                to_me = row["to_user_id"] == session.user_id
                (incoming if to_me else outgoing).append(
                    {
                        "id": offer_ref(guild.id, row["id"]),
                        "campaignId": str(row["campaign_id"]),
                        "campaignName": row["name"],
                        "serverName": guild.name,
                        # Incoming: who offers it. Outgoing: who it's offered to. The
                        # names were copied when the offer was made (migration 0022).
                        "personName": row["from_name"] if to_me else row["to_name"],
                        "expiresAt": _iso_time(row["created_at"] + HANDOVER_SECONDS),
                    }
                )
            for row in await agreed.fetchall():
                recording.append(
                    {
                        "serverId": str(guild.id),
                        "serverName": guild.name,
                        "agreedOn": _iso_date(row["granted_at"]),
                        # Said yes under older wording: not recorded until they say yes again.
                        # (Not checked here: the outside speech company the yes named, #170;
                        # the bot knows which one is in use. A yes for another company shows
                        # as on, and DMbot asks again in Discord.)
                        "on": row["terms_version"] == consent.TERMS_VERSION,
                    }
                )
            if here is not None:
                found = await here.fetchone()
                installed = bool(found and found["installed"])
                installer = found["installer"] if found else None
                servers.append(
                    {
                        "id": str(guild.id),
                        "name": guild.name,
                        # In the server now: an install without a leave. Campaigns count only
                        # for servers from before installs were recorded (no row at all);
                        # a server DMbot has left is offered again even with campaigns.
                        "hasDmbot": installed
                        or bool(found and found["played"] and not found["recorded"]),
                        # Joined through a plain link and nobody has said who added it.
                        "canLink": installed and installer is None,
                        "installedByYou": installer == session.user_id,
                    }
                )

        # "Experience points": every minute DMbot has run for this account, all time, through the
        # one door the website's role has to the hours meter (#1228, #1231).
        cur = await conn.execute("SELECT dmbot_minutes_total() AS minutes")
        total_row = await cur.fetchone()
        experience = int(total_row["minutes"]) if total_row else 0

        if month is not None:
            cur = await conn.execute("SELECT dmbot_hours_used(%s) AS minutes", (month.start,))
            hours_row = await cur.fetchone()
            used_minutes = int(hours_row["minutes"]) if hours_row else 0
        # A failed first payment: a recorded failure, no payment on record, and no plan
        # that works now.
        cur = await conn.execute(
            "SELECT EXISTS (SELECT 1 FROM payment_events WHERE user_id = %(u)s"
            "   AND kind = 'payment_failed') AS failed,"
            f" EXISTS (SELECT 1 FROM payment_events WHERE user_id = %(u)s AND {PAID_EVENT_SQL})"
            "   AS paid",
            {"u": session.user_id, "paid": PAID_KINDS},
        )
        payments = await cur.fetchone()
        first_failed = bool(
            payments and payments["failed"] and not payments["paid"] and not plan_works
        )
        # The person's own installs, in the same transaction (one connection per /me).
        # The last server's setting is cleared first, so only the installer rule applies:
        # otherwise that server's installs row would be visible here too.
        await conn.execute("SELECT set_config('dmbot.guild_id', '', true)")
        cur = await conn.execute(
            "SELECT guild_id, installed_at, via FROM installs"
            " WHERE installed_by_user_id = %s AND left_at IS NULL ORDER BY installed_at",
            (session.user_id,),
        )
        installs = [
            {
                "serverId": str(row["guild_id"]),
                "serverName": names.get(row["guild_id"], ""),
                "installedAt": _iso_time(row["installed_at"]),
                "via": row["via"],
            }
            for row in await cur.fetchall()
        ]

    # The account's stored DMbot ID, kept for life (a move to a new Discord account keeps it).
    account_number = await dmbot_id(db, session.user_id)

    return {
        "user": {"id": str(session.user_id), "name": session.display_name},
        # The account number (the Discord ID in base 36) and the minutes DMbot has run (#1231).
        # A move code is for an active paid plan only (not Try It, a grant or the free list).
        **({"dmbotId": account_number} if account_number else {}),
        "experiencePoints": experience,
        "canMove": can_move(plan, now),
        # The Support form is for every account with a plan row, Try It too (#1435), and for free
        # access without one (#2190); someone with no account yet sees the public Q&A link (#1340).
        "canSupport": support.may_write(plan, access),
        # A first payment that failed and no payment since: the page says "try again" (#922).
        # Left out otherwise, so the common reply is unchanged.
        **({"firstPaymentFailed": True} if first_failed else {}),
        "plan": None
        if plan is None
        else {
            "id": plan.plan,
            # An "active" plan whose period ended without a renewal no longer works.
            "status": "lapsed" if plan.status == "active" and not plan.usable(now) else plan.status,
            # Listening hours are metered by the bot (#437): the minutes used this month, in
            # hours to one decimal.
            # Only from the plan's own month (a lapsed plan beside a grant would read the
            # grant's minutes).
            "hoursUsed": round(used_minutes / 60, 1) if plan.usable(now) else 0,
            "hoursCap": entitlements.cap_hours(plan.hours_this_period, plan.minutes_taken),
            "renewsOn": _iso_date(plan.period_end),
            "graceEndsOn": _iso_date(plan.grace_ends_at),
        }
        # Cancelled on the payment page: it works to this date and then ends (#824).
        | ({"endsOn": _iso_date(plan.ends_at)} if plan.ends_at is not None else {})
        # What the plan is doing, for the account page's plan line (#2165): "active" (renews),
        # "ending" (cancelled, still works), "changing" (another plan starts at the period's end)
        # or "expired" (it has stopped). Read from what the payment company's events stored; the
        # page load never asks the company.
        | _plan_state(plan, now),
        # Free access (#771): "free" (the owner's list or the admin's grant: people see
        # "Free access", never why), "paid" or "none". endsOn: a grant's end date.
        # stillPaying: covered while a paid plan still works, so the page offers to stop
        # paying. Covered people see no price or payment button.
        "access": {"kind": "free" if access.kind in ("free", "grant") else access.kind}
        | ({"endsOn": _iso_date(access.until)} if access.until is not None else {})
        | ({"stillPaying": True, "paidPlan": access.still_paying} if access.still_paying else {})
        # A paid plan under free access that was cancelled: it ends on this date (#824).
        | (
            {"paidPlan": access.ending_plan, "paidPlanEndsOn": _iso_date(access.ending_at)}
            if access.ending_plan
            else {}
        )
        # No limit at all: the page leaves "your hours" and "slots" out of its words (#824).
        | (
            {"unlimited": True}
            if access.kind in ("free", "grant")
            and access.hours_cap is None
            and access.campaign_cap is None
            else {}
        )
        # A capped grant ("like Guild") has hours too: the page shows them (#824).
        | (
            {
                "hours": {
                    "used": round(used_minutes / 60, 1),
                    "cap": access.hours_cap,
                    "renewsOn": _iso_date(month.end),
                }
            }
            if access.kind == "grant" and access.hours_cap and month is not None
            else {}
        ),
        # Spoken recaps (#1389): in the plan (Guild and up, or free access), bought as the
        # add-on, or neither; canBuy only while the add-on is for sale (its price is set), so
        # the page hides the button until then.
        "recapAudio": _recap_audio_view(plan, access, now, recap_audio_for_sale),
        "recording": recording,
        "campaigns": campaigns,
        "servers": servers,
        "installs": installs,
        "offers": {"incoming": incoming, "outgoing": outgoing},
    }
