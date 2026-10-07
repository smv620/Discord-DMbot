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
from dmbot.db import Database
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
    plan = await entitlements.get(db, session.user_id)
    campaigns: list[dict[str, Any]] = []
    servers: list[dict[str, Any]] = []
    names = {g.id: g.name for g in session.guilds}

    async with db.unscoped() as conn:
        # One batch (a pipeline), not hundreds of round trips. Each server's statements
        # switch the transaction to that server first, so row-level security shows only
        # its rows, exactly as Database.guild() would.
        pending = []
        async with conn.pipeline():
            for guild in session.guilds[:MAX_GUILDS]:
                await conn.execute(
                    "SELECT set_config('dmbot.guild_id', %s, true)", (str(int(guild.id)),)
                )
                mine = await conn.execute(
                    "SELECT c.id, c.name, c.last_played_at FROM campaigns c"
                    " JOIN campaign_dms d ON d.campaign_id = c.id AND d.guild_id = c.guild_id"
                    " WHERE c.guild_id = %s AND d.user_id = %s ORDER BY c.name",
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
                pending.append((guild, mine, here))
        for guild, mine, here in pending:
            for row in await mine.fetchall():
                campaigns.append(
                    {
                        "id": str(row["id"]),
                        "name": row["name"],
                        "serverName": names.get(guild.id, ""),
                        "lastPlayedAt": _iso_time(row["last_played_at"]),
                        # Paused campaigns and the campaign owner arrive with #437; until
                        # then every campaign is active and the person a DM, not owner, so
                        # nothing can be handed over before ownership exists.
                        "status": "active",
                        "role": "co-dm",
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
        await conn.execute(
            "SELECT set_config('dmbot.user_id', %s, true)", (str(int(session.user_id)),)
        )
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
        "campaigns": campaigns,
        "servers": servers,
        "installs": installs,
    }
