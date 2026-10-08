"""Free access given by hand (#771; docs/PLAN.md, "Free access and the admin page"): the
admin page's writes. Only this module opens Database.grant_writer() (a test checks), and
every change is logged in `access_log` (the admin's email, the action, the Discord id,
when). The rules people get from a grant live in dmbot.entitlements.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from dmbot.db import Conn, Database
from dmbot.entitlements import GRANT_LEVELS, GrantLevel

NOTE_MAX = 200
Action = Literal["grant", "change", "revoke"]


class GrantError(ValueError):
    """A request the admin can fix; the message is safe to show them."""


@dataclass(frozen=True)
class GrantRow:
    discord_user_id: int
    level: GrantLevel
    ends_at: int | None
    note: str
    granted_by: str
    granted_at: int
    revoked_at: int | None


@dataclass(frozen=True)
class LogEntry:
    admin_email: str
    action: Action
    discord_user_id: int
    at: int


async def give(
    db: Database,
    admin_email: str,
    discord_user_id: int,
    level: str,
    *,
    ends_at: int | None,
    note: str,
    now: int,
) -> Action:
    """Give free access, or change an existing grant ("grant" or "change"). A revoked
    grant given again starts afresh."""
    if not 0 < discord_user_id < 2**63:
        raise GrantError("That isn't a Discord account number.")
    if not 3 <= len(admin_email) <= 320:
        raise GrantError("Sign in again: DMbot doesn't know which admin you are.")
    if level not in GRANT_LEVELS:
        raise GrantError("Pick a level: like Guild, or no limits.")
    if ends_at is not None and ends_at <= now:
        raise GrantError("The end date must be in the future.")
    note = " ".join(note.split())
    if len(note) > NOTE_MAX:
        raise GrantError(f"Keep the note to {NOTE_MAX} characters.")
    async with db.grant_writer() as conn:
        # One change per person at a time: a row lock can't cover a grant not made yet,
        # so two gives at once would both log "grant".
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"dmbot.grant_writer:{int(discord_user_id)}",),
        )
        cur = await conn.execute(
            "SELECT revoked_at FROM access_grants WHERE discord_user_id = %s",
            (discord_user_id,),
        )
        before = await cur.fetchone()
        action: Action = (
            "change" if before is not None and before["revoked_at"] is None else "grant"
        )
        await conn.execute(
            "INSERT INTO access_grants"
            " (discord_user_id, level, ends_at, note, granted_by, granted_at, revoked_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, NULL)"
            " ON CONFLICT (discord_user_id) DO UPDATE SET level = EXCLUDED.level,"
            " ends_at = EXCLUDED.ends_at, note = EXCLUDED.note,"
            " granted_by = EXCLUDED.granted_by, granted_at = EXCLUDED.granted_at,"
            " revoked_at = NULL",
            (discord_user_id, level, ends_at, note, admin_email, now),
        )
        await _log(conn, admin_email, action, discord_user_id, now)
    return action


async def revoke(db: Database, admin_email: str, discord_user_id: int, *, now: int) -> bool:
    """End someone's free access now. False if they had none."""
    if not 3 <= len(admin_email) <= 320:
        raise GrantError("Sign in again: DMbot doesn't know which admin you are.")
    async with db.grant_writer() as conn:
        cur = await conn.execute(
            "UPDATE access_grants SET revoked_at = %s"
            " WHERE discord_user_id = %s AND revoked_at IS NULL",
            (now, discord_user_id),
        )
        if cur.rowcount != 1:
            return False
        await _log(conn, admin_email, "revoke", discord_user_id, now)
    return True


async def grants(db: Database) -> list[GrantRow]:
    """Every grant, newest first (the admin page's list)."""
    async with db.grant_writer() as conn:
        cur = await conn.execute(
            "SELECT discord_user_id, level, ends_at, note, granted_by, granted_at, revoked_at"
            " FROM access_grants ORDER BY granted_at DESC, discord_user_id"
        )
        rows = await cur.fetchall()
    return [
        GrantRow(
            int(r["discord_user_id"]),
            cast(GrantLevel, r["level"]),
            r["ends_at"],
            r["note"],
            r["granted_by"],
            r["granted_at"],
            r["revoked_at"],
        )
        for r in rows
    ]


async def log_entries(db: Database, limit: int = 200) -> list[LogEntry]:
    """The newest changes first."""
    async with db.grant_writer() as conn:
        cur = await conn.execute(
            "SELECT admin_email, action, discord_user_id, at FROM access_log"
            " ORDER BY id DESC LIMIT %s",
            (limit,),
        )
        rows = await cur.fetchall()
    return [
        LogEntry(r["admin_email"], cast(Action, r["action"]), int(r["discord_user_id"]), r["at"])
        for r in rows
    ]


async def _log(
    conn: Conn, admin_email: str, action: Action, discord_user_id: int, now: int
) -> None:
    await conn.execute(
        "INSERT INTO access_log (admin_email, action, discord_user_id, at) VALUES (%s, %s, %s, %s)",
        (admin_email, action, discord_user_id, now),
    )
