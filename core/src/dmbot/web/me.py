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
        for guild in session.guilds[:MAX_GUILDS]:
            # Switch this transaction to one server at a time; row-level security then
            # shows only that server's rows, exactly as Database.guild() would.
            await conn.execute(
                "SELECT set_config('dmbot.guild_id', %s, true)", (str(int(guild.id)),)
            )
            cur = await conn.execute(
                "SELECT c.id, c.name, c.last_played_at FROM campaigns c"
                " JOIN campaign_dms d ON d.campaign_id = c.id AND d.guild_id = c.guild_id"
                " WHERE c.guild_id = %s AND d.user_id = %s ORDER BY c.name",
                (guild.id, session.user_id),
            )
            for row in await cur.fetchall():
                campaigns.append(
                    {
                        "id": row["id"],
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
            if guild.manage:
                cur = await conn.execute(
                    "SELECT EXISTS (SELECT 1 FROM installs WHERE guild_id = %s)"
                    " OR EXISTS (SELECT 1 FROM campaigns WHERE guild_id = %s) AS here",
                    (guild.id, guild.id),
                )
                here = await cur.fetchone()
                servers.append(
                    {
                        "id": str(guild.id),
                        "name": guild.name,
                        "hasDmbot": bool(here and here["here"]),
                    }
                )

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
    }
