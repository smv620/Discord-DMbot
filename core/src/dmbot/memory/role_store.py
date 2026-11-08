"""The story roles kept for a campaign (#3172, item b): its list of role codes and the roles its
entries hold that no connection shows.

A role a connection shows (a mentor of a player character, a ruler who leads a place) is worked
out on read and never stored; this keeps the rest: a hidden villain not yet tied to the party, a
god, a plot artifact. One open row per entry and role, with the session and the line numbers that
showed it, never a word of the story; ending a role closes its row (append-only, like the graph),
so the history says when an entry stopped being one. Roles are for the DM only, like the
importance rank, and never add to it.

The codes are per campaign (`memory_role_codes`), so a role a campaign's reading adds never
crosses campaigns: the owner's seeded roles (`roles.SEEDED_ROLES`) are rows there too, written for
a campaign before its first role (`ensure_codes`), and up to `roles.ADDED_MAX` added ones, each
with the description, examples and not-this the embedding match reads later. A refused code or
role is counted by reason, never logged with its words.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from dmbot.db import Conn, Database
from dmbot.memory import roles
from dmbot.memory.importance_rank import MAX_LINES, ROLE_REASONS, roots
from dmbot.memory.models import new_id
from dmbot.memory.roles_backed import backed_roles


@dataclass(frozen=True, slots=True)
class NewRole:
    """A role the reading (or the DM) found for one entry."""

    entity_id: str
    role: str
    reason: str
    session_started_at: int | None = None
    lines: tuple[int, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class WhyRole:
    """One role an entry holds, for the DM's "why" on a node: backed by connections (their ids,
    to open with their lines) or stored from a reading (its session), or both."""

    role: str
    relation_ids: tuple[str, ...] = ()
    stored_reason: str | None = None
    session_started_at: int | None = None


@dataclass(frozen=True, slots=True)
class HeldRole:
    """An open stored role, for the DM's "why" on a node."""

    entity_id: str
    role: str
    reason: str
    session_started_at: int | None
    recorded_at: int


async def ensure_role_codes(conn: Conn, guild_id: int, campaign_id: str, now: int) -> None:
    """The owner's seeded role codes for this campaign, in the caller's connection, written once
    (a later call writes nothing): a campaign made after migration 0388 gets them before its first
    role. (A restore brings its codes from the file instead, `role_backup`.)"""
    cur = await conn.execute(
        "SELECT 1 FROM memory_role_codes WHERE guild_id = %s AND campaign_id = %s"
        " AND source = 'seeded' LIMIT 1",
        (guild_id, campaign_id),
    )
    if await cur.fetchone() is not None:
        return  # (seeded already: the usual case, one read instead of 29 writes)
    async with conn.cursor() as cursor:
        await cursor.executemany(
            "INSERT INTO memory_role_codes"
            " (guild_id, campaign_id, code, label, source, created_at)"
            " VALUES (%s, %s, %s, %s, 'seeded', %s) ON CONFLICT DO NOTHING",
            [
                (guild_id, campaign_id, code, roles.SEEDED[code].label, now)
                for code in sorted(roles.STORED_SEEDED)  # (one order everywhere: no deadlock)
            ],
        )


class RoleStore:
    def __init__(self, db: Database, now: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = now

    async def ensure_codes(self, guild_id: int, campaign_id: str) -> None:
        """Write the owner's seeded role codes for a campaign that has none yet (one made after
        migration 0388). Safe to call again."""
        async with self._db.guild(guild_id) as conn:
            await ensure_role_codes(conn, guild_id, campaign_id, int(self._now()))

    async def codes(self, guild_id: int, campaign_id: str) -> dict[str, str]:
        """The campaign's role codes and their source ('seeded' or 'reading')."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT code, source FROM memory_role_codes"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            return {str(r["code"]): str(r["source"]) for r in await cur.fetchall()}

    async def add_code(
        self,
        guild_id: int,
        campaign_id: str,
        code: str,
        label: str,
        description: str,
        examples: tuple[str, ...],
        not_this: str,
    ) -> str | None:
        """Add one of the campaign's own roles, as the reading proposed it. None when it is
        stored (or already was); otherwise why it was refused, as a count key: its shape
        (`roles.check_added`), or "too_many" past `roles.ADDED_MAX` for the campaign."""
        why = roles.check_added(code, label, description, examples, not_this)
        if why is not None:
            return why
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            # (two readings adding at once must not both see 39 and both write: a 41st code would
            # make every later backup of the campaign trim it; one lock per campaign, held to the
            # end of this transaction)
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"role_codes:{guild_id}:{campaign_id}",),
            )
            await ensure_role_codes(conn, guild_id, campaign_id, now)
            cur = await conn.execute(
                "SELECT count(*) AS n, bool_or(code = %s) AS have FROM memory_role_codes"
                " WHERE guild_id = %s AND campaign_id = %s AND source = 'reading'",
                (code, guild_id, campaign_id),
            )
            row = await cur.fetchone()
            if row is not None and row["have"]:
                return None
            if row is not None and int(row["n"]) >= roles.ADDED_MAX:
                return "too_many"
            await conn.execute(
                "INSERT INTO memory_role_codes (guild_id, campaign_id, code, label, description,"
                " examples, not_this, source, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, 'reading', %s) ON CONFLICT DO NOTHING",
                (
                    guild_id, campaign_id, code, label.strip(), description.strip(),
                    [e.strip() for e in examples], not_this.strip(), now,
                ),
            )  # fmt: skip
        return None

    async def add(
        self, guild_id: int, campaign_id: str, found: Iterable[NewRole], lost: Counter[str]
    ) -> int:
        """Record these roles and return how many rows were written. A role the entry already
        holds adds nothing (one open row per entry and role); a role the campaign has no code for,
        commoner (never stored), a bad reason, or an entry that is not live is skipped and counted
        in `lost` by reason."""
        wanted = list(found)
        if not wanted:
            return 0
        now = int(self._now())
        written = 0
        async with self._db.guild(guild_id) as conn:
            await ensure_role_codes(conn, guild_id, campaign_id, now)
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
            for r in wanted:
                if r.role == roles.COMMONER:
                    lost["role_commoner"] += 1  # (the default: an entry with no role is one)
                    continue
                if r.role not in known:
                    lost["role_unknown"] += 1
                    continue
                if r.reason not in ROLE_REASONS:
                    lost["role_bad_reason"] += 1
                    continue
                if r.entity_id not in live:
                    lost["role_no_entry"] += 1
                    continue
                cur = await conn.execute(
                    "INSERT INTO memory_roles (guild_id, campaign_id, id, entity_id, role, reason,"
                    " session_started_at, lines, recorded_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (
                        guild_id, campaign_id, new_id(), r.entity_id, r.role, r.reason,
                        r.session_started_at, list(r.lines[:MAX_LINES]), now,
                    ),
                )  # fmt: skip
                written += cur.rowcount
        return written

    async def end(self, guild_id: int, campaign_id: str, entity_id: str, role: str) -> bool:
        """Close the entry's open row for this role (the story showed it is no longer one: the
        "king" with no ship). False when it held none. The row stays, as history."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE memory_roles SET ended_at = GREATEST(%s, recorded_at)"
                " WHERE guild_id = %s AND campaign_id = %s AND entity_id = %s AND role = %s"
                " AND ended_at IS NULL",
                (int(self._now()), guild_id, campaign_id, entity_id, role),
            )
            return bool(cur.rowcount)

    async def held(
        self, guild_id: int, campaign_id: str, entity_id: str | None = None
    ) -> list[HeldRole]:
        """The open stored roles (of one entry, or the whole campaign), oldest first. DM only:
        the caller never hands these to a player's screen."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT entity_id, role, reason, session_started_at, recorded_at FROM memory_roles"
                " WHERE guild_id = %s AND campaign_id = %s AND ended_at IS NULL"
                " AND (%s::text IS NULL OR entity_id = %s) ORDER BY recorded_at, role",
                (guild_id, campaign_id, entity_id, entity_id),
            )
            return [
                HeldRole(
                    str(r["entity_id"]), str(r["role"]), str(r["reason"]),
                    None if r["session_started_at"] is None else int(r["session_started_at"]),
                    int(r["recorded_at"]),
                )
                for r in await cur.fetchall()
            ]  # fmt: skip

    async def why(self, guild_id: int, campaign_id: str, entity_id: str) -> list[WhyRole]:
        """Every role the entry holds, for the DM's "why": those its connections show, worked out
        now (`roles_backed`), and those a reading stored, open ones only. A merged name's roles
        count for the entry it ended up as, the way the importance rank counts them. Empty for an
        entry that is gone. DM only: never handed to a player's screen."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, type, role, status, merged_into FROM memory_entities"
                " WHERE guild_id = %s AND campaign_id = %s AND type <> 'person'"
                " AND status IN ('proposed', 'confirmed', 'merged')",
                (guild_id, campaign_id),
            )
            entities = {str(r["id"]): r for r in await cur.fetchall()}
            ended = roots(entities)
            if ended.get(entity_id) != entity_id:
                return []  # (gone, or merged into another: ask about the one that stayed)
            cur = await conn.execute(
                "SELECT id, subject_id, predicate, object_id, detail, status, to_session_at"
                " FROM memory_relations WHERE guild_id = %s AND campaign_id = %s"
                " AND status = 'confirmed' AND to_session_at IS NULL",
                (guild_id, campaign_id),
            )
            relations = await cur.fetchall()
            group = [e for e, root in ended.items() if root == entity_id]
            cur = await conn.execute(
                "SELECT role, reason, session_started_at FROM memory_roles"
                " WHERE guild_id = %s AND campaign_id = %s AND ended_at IS NULL"
                " AND entity_id = ANY(%s) ORDER BY recorded_at",
                (guild_id, campaign_id, group),
            )
            # (one per role: when two names of a merged group each hold it, the newest wins)
            stored = {str(r["role"]): r for r in await cur.fetchall()}
        backed = backed_roles(entities, relations).get(entity_id, {})
        out = []
        for role in sorted(set(backed) | set(stored)):
            row = stored.get(role)
            out.append(
                WhyRole(
                    role,
                    tuple(sorted(backed.get(role, ()))),
                    None if row is None else str(row["reason"]),
                    None if row is None or row["session_started_at"] is None
                    else int(row["session_started_at"]),
                )
            )  # fmt: skip
        return out


__all__ = ["HeldRole", "NewRole", "RoleStore", "WhyRole", "ensure_role_codes"]
