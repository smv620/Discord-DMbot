"""`GET /me/export`: one JSON file of what DMbot holds about the signed-in person (#1150).

Only their own rows, read under the website's database role: the person's own scope, and for
server tables the servers in this session's own list, exactly as /me reads them. Campaign
contents are the whole table's and come through backups, so campaigns here are names and dates
only. Nothing about card data (DMbot never has it) and no payment company ids.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from dmbot.db import Database
from dmbot.web.me import MAX_GUILDS
from dmbot.web.sessions import Session


def _iso(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, UTC).isoformat().replace("+00:00", "Z")


async def build_export(db: Database, session: Session, *, now: int) -> dict[str, Any]:
    guilds = session.guilds[:MAX_GUILDS]
    consent: list[dict[str, Any]] = []
    campaigns: list[dict[str, Any]] = []
    characters: list[dict[str, Any]] = []
    async with db.user(session.user_id, session=session.id_hash) as conn:
        cur = await conn.execute(
            "SELECT email, created_at, last_sign_in_at FROM web_users WHERE user_id = %s",
            (session.user_id,),
        )
        account = await cur.fetchone()
        for guild in guilds:
            # One server at a time, as /me does: row-level security shows only that server's
            # rows, and only the person's own (and only if it is in this session's list).
            await conn.execute(
                "SELECT set_config('dmbot.guild_id', %s, true)", (str(int(guild.id)),)
            )
            cur = await conn.execute(
                "SELECT granted_at, terms_version, method FROM consent"
                " WHERE guild_id = %s AND user_id = %s",
                (guild.id, session.user_id),
            )
            for row in await cur.fetchall():
                consent.append(
                    {
                        "serverName": guild.name,
                        "agreedAt": _iso(row["granted_at"]),
                        "wordingVersion": row["terms_version"],
                        "how": row["method"],
                    }
                )
            cur = await conn.execute(
                "SELECT c.id, c.name, c.created_at, c.last_played_at, c.owner_user_id"
                " FROM campaigns c JOIN campaign_dms d"
                " ON d.campaign_id = c.id AND d.guild_id = c.guild_id"
                " WHERE c.guild_id = %s AND d.user_id = %s ORDER BY c.name",
                (guild.id, session.user_id),
            )
            for row in await cur.fetchall():
                campaigns.append(
                    {
                        "name": row["name"],
                        "serverName": guild.name,
                        "createdAt": _iso(row["created_at"]),
                        "lastPlayedAt": _iso(row["last_played_at"]),
                        "role": "owner" if row["owner_user_id"] == session.user_id else "co-dm",
                    }
                )
            cur = await conn.execute(
                "SELECT campaign_id, url, sheet, source, fetched_at FROM character_sheets"
                " WHERE guild_id = %s AND player_id = %s ORDER BY campaign_id, entity_id",
                (guild.id, session.user_id),
            )
            sheet_rows = await cur.fetchall()
            # The name of each campaign the person has a sheet in, by id only: they play there
            # but may not be a DM, so the list above doesn't have it. Ids from their own rows,
            # never a list of the server's campaigns.
            names: dict[str, str] = {}
            if sheet_rows:
                cur = await conn.execute(
                    "SELECT id, name FROM campaigns WHERE guild_id = %s AND id = ANY(%s)",
                    (guild.id, [r["campaign_id"] for r in sheet_rows]),
                )
                names = {r["id"]: r["name"] for r in await cur.fetchall()}
            for row in sheet_rows:
                characters.append(
                    {
                        "serverName": guild.name,
                        "campaign": names.get(row["campaign_id"]),
                        "sheetLink": row["url"],
                        "typedIn": row["source"] == "typed",
                        "snapshot": row["sheet"],
                        "readAt": _iso(row["fetched_at"]),
                    }
                )
        await conn.execute("SELECT set_config('dmbot.guild_id', '', true)")
        cur = await conn.execute(
            "SELECT plan, status, hours_cap, extra_hours, campaign_cap, period_start, period_end,"
            " grace_ends_at, lapsed_at, provider FROM entitlements WHERE user_id = %s",
            (session.user_id,),
        )
        plan = await cur.fetchone()
        cur = await conn.execute(
            "SELECT provider, kind, received_at FROM payment_events WHERE user_id = %s"
            " ORDER BY received_at",
            (session.user_id,),
        )
        payments = await cur.fetchall()
        cur = await conn.execute("SELECT 1 FROM try_it_used WHERE user_id = %s", (session.user_id,))
        tried = await cur.fetchone() is not None

    return {
        "about": (
            "What DMbot holds about you. Campaign contents are not here: they belong to the"
            " whole table and come through a campaign backup. A stopped recording consent is"
            " deleted, so it is not listed."
        ),
        "exportedAt": _iso(now),
        "account": {
            "discordId": str(session.user_id),
            "name": session.display_name,
            "email": account["email"] if account else None,
            "firstSignedInAt": _iso(account["created_at"]) if account else None,
            "lastSignedInAt": _iso(account["last_sign_in_at"]) if account else None,
        },
        "recordingConsent": consent,
        "campaigns": campaigns,
        "characters": characters,
        "plan": None
        if plan is None
        else {
            "plan": plan["plan"],
            "status": plan["status"],
            "hoursCap": plan["hours_cap"],
            "extraHours": plan["extra_hours"],
            "campaignCap": plan["campaign_cap"],
            "periodStart": _iso(plan["period_start"]),
            "periodEnd": _iso(plan["period_end"]),
            "graceEndsAt": _iso(plan["grace_ends_at"]),
            "stoppedAt": _iso(plan["lapsed_at"]),
            "paymentCompany": plan["provider"],
        },
        "usedTryIt": tried,
        "paymentEvents": [
            {"company": p["provider"], "kind": p["kind"] or "unknown", "at": _iso(p["received_at"])}
            for p in payments
        ],
    }
