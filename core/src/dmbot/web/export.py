"""`GET /me/export`: one JSON file of what DMbot holds about the signed-in person (#1150).

Only their own rows, read under the website's database role: the person's own scope, and for
server tables the servers in this session's own list, exactly as /me reads them. Campaign
contents are the whole table's and come through backups, so campaigns here are names and dates
only. Nothing about card data (DMbot never has it) and no payment company ids.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from dmbot.db import Conn, Database
from dmbot.web.discord import DiscordGuild
from dmbot.web.me import MAX_GUILDS
from dmbot.web.sessions import Session


def _iso(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, UTC).isoformat().replace("+00:00", "Z")


async def read_sheet_rows(conn: Conn, guild_id: int, user_id: int) -> list[dict[str, Any]]:
    """The person's own character sheets in one server, each with the name of its campaign
    (the export, #1150, and the account page's My characters, #1164, read them here). The
    server must already be set on the connection: row-level security shows only that server's
    rows, only the person's own, and only if the server is in this session's list. A campaign
    is named by id from the person's own rows, never from a list of the server's campaigns
    (they may play there without being a DM)."""
    cur = await conn.execute(
        "SELECT campaign_id, url, sheet, source, fetched_at FROM character_sheets"
        " WHERE guild_id = %s AND player_id = %s ORDER BY campaign_id, entity_id",
        (guild_id, user_id),
    )
    rows = await cur.fetchall()
    names: dict[str, str] = {}
    if rows:
        cur = await conn.execute(
            "SELECT id, name FROM campaigns WHERE guild_id = %s AND id = ANY(%s)",
            (guild_id, [r["campaign_id"] for r in rows]),
        )
        names = {r["id"]: r["name"] for r in await cur.fetchall()}
    named = [{**r, "campaign": names.get(r["campaign_id"])} for r in rows]
    # By campaign name, then as stored: campaign ids are random, so their order means nothing.
    return sorted(named, key=lambda r: (r["campaign"] or "", r["campaign_id"]))


async def _read_server(
    conn: Conn, guild: DiscordGuild, user_id: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """One server's part of the export: consent, campaigns the person DMs, and their sheets.
    Row-level security shows only that server's rows, and only the person's own (and only
    if the server is in this session's list), as /me reads them."""
    await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(int(guild.id)),))
    cur = await conn.execute(
        "SELECT granted_at, terms_version, method FROM consent"
        " WHERE guild_id = %s AND user_id = %s",
        (guild.id, user_id),
    )
    consent = [
        {
            "serverName": guild.name,
            "agreedAt": _iso(row["granted_at"]),
            "wordingVersion": row["terms_version"],
            "how": row["method"],
        }
        for row in await cur.fetchall()
    ]
    cur = await conn.execute(
        "SELECT c.id, c.name, c.created_at, c.last_played_at, c.owner_user_id"
        " FROM campaigns c JOIN campaign_dms d"
        " ON d.campaign_id = c.id AND d.guild_id = c.guild_id"
        " WHERE c.guild_id = %s AND d.user_id = %s ORDER BY c.name",
        (guild.id, user_id),
    )
    campaigns = [
        {
            "name": row["name"],
            "serverName": guild.name,
            "createdAt": _iso(row["created_at"]),
            "lastPlayedAt": _iso(row["last_played_at"]),
            "role": "owner" if row["owner_user_id"] == user_id else "co-dm",
        }
        for row in await cur.fetchall()
    ]
    characters = [
        {
            "serverName": guild.name,
            "campaign": row["campaign"],
            "sheetLink": row["url"],
            "typedIn": row["source"] == "typed",
            "snapshot": row["sheet"],
            "readAt": _iso(row["fetched_at"]),
        }
        for row in await read_sheet_rows(conn, guild.id, user_id)
    ]
    return consent, campaigns, characters


async def build_export(db: Database, session: Session, *, now: int) -> dict[str, Any]:
    consent: list[dict[str, Any]] = []
    campaigns: list[dict[str, Any]] = []
    characters: list[dict[str, Any]] = []
    async with db.user(session.user_id, session=session.id_hash) as conn:
        cur = await conn.execute(
            "SELECT email, created_at, last_sign_in_at FROM web_users WHERE user_id = %s",
            (session.user_id,),
        )
        account = await cur.fetchone()
        for guild in session.guilds[:MAX_GUILDS]:
            more_consent, more_campaigns, more_characters = await _read_server(
                conn, guild, session.user_id
            )
            consent += more_consent
            campaigns += more_campaigns
            characters += more_characters
        await conn.execute("SELECT set_config('dmbot.guild_id', '', true)")
        cur = await conn.execute(
            "SELECT plan, status, hours_cap, extra_hours, campaign_cap, period_start, period_end,"
            " grace_ends_at, lapsed_at, provider FROM entitlements WHERE user_id = %s",
            (session.user_id,),
        )
        plan = await cur.fetchone()
        # Free access (#771): the level and dates only, never who gave it or the note.
        cur = await conn.execute(
            "SELECT level, ends_at, granted_at, revoked_at FROM access_grants"
            " WHERE discord_user_id = %s",
            (session.user_id,),
        )
        free = await cur.fetchone()
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
            "What DMbot holds about you, in the servers you are in now: servers you have left"
            " are not read. Campaign contents are not here: they belong to the whole table and"
            " come through a campaign backup. A stopped recording consent is deleted, so it is"
            " not listed."
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
        "freeAccess": None
        if free is None
        else {
            "level": free["level"],
            "givenAt": _iso(free["granted_at"]),
            "endsAt": _iso(free["ends_at"]),
            "takenBackAt": _iso(free["revoked_at"]),
        },
        "usedTryIt": tried,
        "paymentEvents": [
            {"company": p["provider"], "kind": p["kind"] or "unknown", "at": _iso(p["received_at"])}
            for p in payments
        ],
    }
