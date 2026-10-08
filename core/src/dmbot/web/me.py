"""`GET /me`: what the account page shows (#434, #435). The shape matches the website's
contract, web/src/account/api.ts (`Me`).

Campaigns are read server by server with that server's row-level security, and only for
servers in the person's own Discord list from sign-in, where they are a DM. A server's
other campaigns, and servers the person isn't in, are never read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from dmbot import entitlements
from dmbot.campaigns.models import HANDOVER_SECONDS
from dmbot.db import Database
from dmbot.web.offers import offer_ref
from dmbot.web.sessions import Session

# A person in many servers: read at most this many (Discord's own list stops at 200).
MAX_GUILDS = 200


def _iso_time(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, UTC).isoformat().replace("+00:00", "Z")


def _iso_date(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, UTC).date().isoformat()


async def build_me(db: Database, session: Session, *, now: int) -> dict[str, Any]:
    plan, access = await entitlements.plan_and_access(db, session.user_id, now)
    campaigns: list[dict[str, Any]] = []
    servers: list[dict[str, Any]] = []
    incoming: list[dict[str, Any]] = []
    outgoing: list[dict[str, Any]] = []
    names = {g.id: g.name for g in session.guilds}  # for the installs list below

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
                pending.append((guild, mine, offered, here))
        for guild, mine, offered, here in pending:
            for row in await mine.fetchall():
                campaigns.append(
                    {
                        "id": str(row["id"]),
                        "name": row["name"],
                        "serverName": guild.name,
                        "lastPlayedAt": _iso_time(row["last_played_at"]),
                        # Paused campaigns arrive with #437 part 2; until then every one
                        # is active. "owner": it uses this person's plan, and only they
                        # can hand it over (#437); a campaign with no owner yet (from
                        # before owners were recorded) is "co-dm" for everyone.
                        "status": "active",
                        "role": "owner" if row["owner_user_id"] == session.user_id else "co-dm",
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

    return {
        "user": {"id": str(session.user_id), "name": session.display_name},
        "plan": None
        if plan is None
        else {
            "id": plan.plan,
            # An "active" plan whose period ended without a renewal no longer works.
            "status": "lapsed" if plan.status == "active" and not plan.usable(now) else plan.status,
            # Listening hours are metered by the bot (#437); until then nothing is used.
            "hoursUsed": 0,
            "hoursCap": plan.hours_this_period,
            "renewsOn": _iso_date(plan.period_end),
            "graceEndsOn": _iso_date(plan.grace_ends_at),
        },
        # Free access (#771): "free" (the owner's list), "grant" (given by the admin, maybe
        # until a date), "paid" or "none". Covered people see no price or payment button.
        "access": {"kind": access.kind}
        | ({"until": access.until} if access.until is not None else {}),
        "campaigns": campaigns,
        "servers": servers,
        "installs": installs,
        "offers": {"incoming": incoming, "outgoing": outgoing},
    }
