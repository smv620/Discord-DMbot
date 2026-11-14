"""Story roles in campaign backups (an `ExportSection`; #3172, item b).

A role PlotBot's reading found cannot be found again by re-reading (the lines are not in a backup),
so the section carries the campaign's role codes (the owner's seeded ones by code and label only,
their words living in code; the ones its reading added in full) and every stored role, open or
ended, with its id (so its build stamp comes back with it) and without line numbers (a restored
campaign has none of those lines). Rows from the file are untrusted: the section is refused whole
when one is damaged, an added code is held to the same limits as when the reading proposed it, a
seeded code the owner's list no longer has is left out, and a role whose entry or code is not in
the restored campaign is dropped (the memory section loads first; a backup outlives its entries).
Like the rest of a backup it holds the DM's secrets; roles are never read by a player's screen. An
older backup has no such section and restores with no stored roles.
"""

from __future__ import annotations

from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn
from dmbot.memory import roles
from dmbot.memory.importance_rank import ROLE_REASONS
from dmbot.memory.models import is_id

DAMAGED = "This backup file is damaged (bad story roles)."
ROWS_MAX = 50_000
INT64_MAX = 2**63 - 1
_CODE_KEYS = {"code", "label", "description", "examples", "not_this", "source", "created_at"}
CODES_MAX = len(roles.STORED_SEEDED) + roles.ADDED_MAX
_ROLE_KEYS = {
    "id", "entity_id", "role", "reason", "session_started_at", "recorded_at", "ended_at",
}  # fmt: skip

Code = tuple[str, str, str, tuple[str, ...], str, str, int]
Role = tuple[str, str, str, str, int | None, int, int | None]


def _int(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high  # (a JSON true is not a number)


def _strs(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _code_ok(c: dict[str, Any]) -> bool:
    """A seeded code (its words live in code, not in the row; one the owner's list has since
    renamed or dropped is still well formed, and `checked` leaves it out), or an added one within
    the limits the reading is held to."""
    if c["source"] == "seeded":
        return bool(roles.CODE_SHAPE.fullmatch(c["code"])) and (
            c["description"],
            c["examples"],
            c["not_this"],
        ) == ("", [], "")
    return c["source"] == "reading" and (
        roles.check_added(
            c["code"], c["label"], c["description"], tuple(c["examples"]), c["not_this"]
        )
        is None
    )


def checked(rows: object) -> tuple[list[Code], list[Role]]:
    """The codes and roles as tuples, or CampaignError(DAMAGED) on anything out of shape. Pure.
    Each row of the file is one code (`{"code": {...}}`) or one role (`{"role": {...}}`)."""
    if not (isinstance(rows, list) and len(rows) <= ROWS_MAX + CODES_MAX):
        raise CampaignError(DAMAGED)
    raw_codes: list[object] = []
    raw_roles: list[object] = []
    for row in rows:
        if not (isinstance(row, dict) and len(row) == 1 and set(row) <= {"code", "role"}):
            raise CampaignError(DAMAGED)
        (raw_codes if "code" in row else raw_roles).append(next(iter(row.values())))
    if len(raw_codes) > CODES_MAX or len(raw_roles) > ROWS_MAX:
        raise CampaignError(DAMAGED)
    codes: list[Code] = []
    for c in raw_codes:
        if not (
            isinstance(c, dict)
            and set(c) == _CODE_KEYS
            and all(isinstance(c[k], str) for k in ("code", "label", "description", "not_this"))
            and _strs(c["examples"])
            and _int(c["created_at"], 1, INT64_MAX)
            and _code_ok(c)
        ):
            raise CampaignError(DAMAGED)
        if c["source"] == "seeded" and c["code"] not in roles.STORED_SEEDED:
            continue  # (renamed or dropped from the owner's list since: its roles drop with it)
        if c["source"] == "seeded":  # (today's label, whatever the file says)
            c = {**c, "label": roles.SEEDED[c["code"]].label}
        codes.append(
            (c["code"], c["label"], c["description"], tuple(c["examples"]), c["not_this"],
             c["source"], c["created_at"])
        )  # fmt: skip
    # (more added codes than the limit can only come from a race before the lock existed, or an
    # edited file: the first ones are kept and the rest left out, never the whole backup refused)
    added = [c for c in codes if c[5] == "reading"]
    dropped = {c[0] for c in added[roles.ADDED_MAX :]}
    codes = [c for c in codes if c[0] not in dropped]
    out: list[Role] = []
    for r in raw_roles:
        if not (
            isinstance(r, dict)
            and set(r) == _ROLE_KEYS
            and is_id(r["id"])
            and is_id(r["entity_id"])
            and isinstance(r["role"], str)
            and roles.CODE_SHAPE.fullmatch(r["role"])
            and r["reason"] in ROLE_REASONS
            and (r["session_started_at"] is None or _int(r["session_started_at"], 1, INT64_MAX))
            and _int(r["recorded_at"], 1, INT64_MAX)
            and (r["ended_at"] is None or _int(r["ended_at"], r["recorded_at"], INT64_MAX))
        ):
            raise CampaignError(DAMAGED)
        out.append(
            (r["id"], r["entity_id"], r["role"], r["reason"], r["session_started_at"],
             r["recorded_at"], r["ended_at"])
        )  # fmt: skip
    return codes, out


class RolesSection:
    name = "memory_roles"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT code, label, description, examples, not_this, source, created_at"
            " FROM memory_role_codes WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY created_at, code",
            (guild_id, campaign_id),
        )
        codes = [
            {
                "code": r["code"], "label": r["label"], "description": r["description"],
                "examples": list(r["examples"]), "not_this": r["not_this"],
                "source": r["source"], "created_at": int(r["created_at"]),
            }
            for r in await cur.fetchall()
        ]  # fmt: skip
        cur = await conn.execute(
            "SELECT id, entity_id, role, reason, session_started_at, recorded_at, ended_at"
            " FROM memory_roles WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY recorded_at DESC, id DESC LIMIT %s",
            (guild_id, campaign_id, ROWS_MAX),
        )
        held = [
            {
                "id": r["id"], "entity_id": r["entity_id"], "role": r["role"],
                "reason": r["reason"],
                "session_started_at": None if r["session_started_at"] is None
                else int(r["session_started_at"]),
                "recorded_at": int(r["recorded_at"]),
                "ended_at": None if r["ended_at"] is None else int(r["ended_at"]),
            }
            for r in reversed(await cur.fetchall())
        ]  # fmt: skip
        # (past the cap the oldest go, as in the importance section; the file is oldest first)
        return [{"code": c} for c in codes] + [{"role": r} for r in held]

    def check(self, rows: list[Any]) -> tuple[list[Code], list[Role]]:
        """Pure; the store runs it in a worker thread before its transaction."""
        return checked(rows)

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, data: Any) -> None:
        codes, held = data if isinstance(data, tuple) else checked(data)
        # (the file's codes as they were, seeded ones included, so a restore gives back exactly
        # the campaign's list; a file with none leaves the campaign to be seeded before its first
        # role, as any campaign is)
        async with conn.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO memory_role_codes (guild_id, campaign_id, code, label, description,"
                " examples, not_this, source, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [(guild_id, campaign_id, c, lab, d, list(ex), nt, src, at)
                 for c, lab, d, ex, nt, src, at in codes],
            )  # fmt: skip
        if not held:
            return
        cur = await conn.execute(
            "SELECT code FROM memory_role_codes WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
        known = {str(r["code"]) for r in await cur.fetchall()}
        cur = await conn.execute(
            "SELECT id FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
            " AND type <> 'person' AND status IN ('proposed', 'confirmed')",
            (guild_id, campaign_id),
        )
        live = {str(r["id"]) for r in await cur.fetchall()}
        async with conn.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO memory_roles (guild_id, campaign_id, id, entity_id, role, reason,"
                " session_started_at, lines, recorded_at, ended_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, '{}', %s, %s) ON CONFLICT DO NOTHING",
                [
                    (guild_id, campaign_id, rid, e, role, reason, session, at, ended)
                    for rid, e, role, reason, session, at, ended in held
                    if e in live and role in known
                ],
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM memory_roles WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
        await conn.execute(
            "DELETE FROM memory_role_codes WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


__all__ = ["DAMAGED", "ROWS_MAX", "RolesSection", "checked"]
