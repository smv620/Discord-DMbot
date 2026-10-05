"""Campaign memory storage (docs/PLAN.md, "Campaign memory (EntityBot)", #126).

EntityBot is the only writer, through this class. Every write:
- runs in one per-server transaction (`Database.guild`), filters on server AND campaign,
  and can only link rows of the same campaign (composite foreign keys);
- is logged row by row in `memory_changes`, grouped in a numbered batch, so any
  operation can be undone with `undo(batch)`;
- bumps the campaign's `memory_version` and sends a Postgres NOTIFY carrying only the
  campaign ID and version (notifications skip row-level security, so never names), so
  copies held in memory can reload.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, LiteralString

from psycopg import errors as pg_errors
from psycopg import sql
from psycopg.types.json import Jsonb

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database
from dmbot.memory.checks import FLAG_KINDS, check_relation, duplicate_of, ordered
from dmbot.memory.models import (
    ALIAS_KINDS,
    CONFIRMED,
    DESCRIPTION_MAX,
    DETAIL_MAX,
    ENTITY_STATUSES,
    FACT_STATUSES,
    FIX,
    KEEP,
    LINE_REF_MAX,
    LIVE,
    MENTION_METHODS,
    MERGED,
    NAME_MAX,
    PROPOSED,
    REJECTED,
    SOURCES,
    Alias,
    Correction,
    Entity,
    Flag,
    MemoryRuleError,
    Relation,
    Written,
    clean_text,
    is_id,
    name_key,
    new_id,
)
from dmbot.memory.ontology import (
    ACTIVE,
    CORE_PREDICATES,
    CORE_TYPES,
    DEPRECATED,
    LABEL_MAX,
    MAX_NEW_TERMS_PER_SESSION,
    Ontology,
    PredicateTerm,
    TypeTerm,
)

NOTIFY_CHANNEL = "dmbot_memory"
NOT_HERE = "That campaign doesn't exist in this server."
NOT_FOUND = "DMbot doesn't remember that any more."
CHANGED_SINCE = "That was changed again since, so it can't be undone on its own."
INT64_MAX = 2**63 - 1


@dataclass(frozen=True, slots=True)
class _Table:
    name: str
    key: str  # the column identifying a row within a campaign
    columns: tuple[str, ...]  # excluding guild_id and campaign_id


_TYPES = _Table(
    "memory_types",
    "key",
    (
        "key",
        "parent",
        "label",
        "description",
        "examples",
        "reason",
        "status",
        "replaced_by",
        "created_at",
    ),
)
_PREDICATES = _Table(
    "memory_predicates",
    "key",
    (
        "key",
        "parent",
        "label",
        "description",
        "examples",
        "reason",
        "subject_types",
        "object_types",
        "is_symmetric",
        "max_per_subject",
        "conflicts_with",
        "status",
        "replaced_by",
        "created_at",
    ),
)
_ENTITIES = _Table(
    "memory_entities",
    "id",
    ("id", "type", "name", "description", "status", "merged_into", "source", "created_at"),
)
_ALIASES = _Table(
    "memory_aliases",
    "id",
    (
        "id",
        "entity_id",
        "text",
        "key",
        "kind",
        "used_by",
        "secret",
        "status",
        "sound_codes",
        "source",
        "created_at",
    ),
)
_MENTIONS = _Table(
    "memory_mentions",
    "id",
    (
        "id",
        "entity_id",
        "session_started_at",
        "line_ref",
        "span_start",
        "span_end",
        "confidence",
        "method",
        "created_at",
    ),
)
_RELATIONS = _Table(
    "memory_relations",
    "id",
    (
        "id",
        "subject_id",
        "predicate",
        "object_id",
        "detail",
        "confidence",
        "status",
        "source",
        "mention_ids",
        "from_session_at",
        "to_session_at",
        "from_game_time",
        "to_game_time",
        "secret",
        "created_at",
    ),
)
_CORRECTIONS = _Table(
    "memory_corrections",
    "id",
    ("id", "heard", "heard_key", "entity_id", "action", "source", "created_at"),
)
_FLAGS = _Table(
    "memory_flags",
    "id",
    ("id", "kind", "relation_id", "other_id", "status", "created_at"),
)
# What links to a row (table, column, is the column a list), checked before deleting it.
_DEPENDENTS: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "memory_types": (
        ("memory_types", "parent", False),
        ("memory_entities", "type", False),
        ("memory_predicates", "subject_types", True),
        ("memory_predicates", "object_types", True),
    ),
    "memory_predicates": (
        ("memory_predicates", "parent", False),
        ("memory_predicates", "conflicts_with", True),
        ("memory_relations", "predicate", False),
    ),
    "memory_entities": (
        ("memory_entities", "merged_into", False),
        ("memory_aliases", "entity_id", False),
        ("memory_aliases", "used_by", False),
        ("memory_mentions", "entity_id", False),
        ("memory_relations", "subject_id", False),
        ("memory_relations", "object_id", False),
        ("memory_corrections", "entity_id", False),
    ),
    "memory_mentions": (("memory_relations", "mention_ids", True),),
    "memory_relations": (
        ("memory_flags", "relation_id", False),
        ("memory_flags", "other_id", False),
    ),
}

# Backup and load order: anything a row links to comes first.
_BACKED_UP = (_TYPES, _PREDICATES, _ENTITIES, _ALIASES, _MENTIONS, _RELATIONS, _CORRECTIONS, _FLAGS)
_BY_NAME = {t.name: t for t in _BACKED_UP}


def _scoped(table: _Table, where: sql.Composable | None = None) -> sql.Composed:
    """SELECT a table's columns for one server and campaign, plus `where`."""
    return sql.SQL("SELECT {} FROM {} WHERE guild_id = %s AND campaign_id = %s{}").format(
        sql.SQL(", ").join(sql.Identifier(c) for c in table.columns),
        sql.Identifier(table.name),
        where if where is not None else sql.SQL(""),
    )


def _by_key(table: _Table, *, lock: bool) -> sql.Composed:
    return _scoped(
        table,
        sql.SQL(" AND {} = %s{}").format(
            sql.Identifier(table.key), sql.SQL(" FOR UPDATE" if lock else "")
        ),
    )


def _row(raw: dict[str, Any], table: _Table) -> dict[str, Any]:
    return {c: raw[c] for c in table.columns}


class _Op:
    """One logged operation inside a campaign's transaction. Every row it touches is
    recorded with its before and after values, under one batch number."""

    def __init__(
        self,
        conn: Conn,
        guild_id: int,
        campaign_id: str,
        source: str,
        version: int,
        now: int,
        undoes: int | None,
    ) -> None:
        self.conn = conn
        self.guild_id = guild_id
        self.campaign_id = campaign_id
        self.source = source
        self.version = version
        self.now = now
        self.undoes = undoes
        self.batch: int | None = None

    @property
    def scope(self) -> tuple[int, str]:
        return self.guild_id, self.campaign_id

    async def get(self, table: _Table, row_id: str, *, lock: bool = True) -> dict[str, Any] | None:
        cur = await self.conn.execute(_by_key(table, lock=lock), (*self.scope, row_id))
        raw = await cur.fetchone()
        return None if raw is None else _row(raw, table)

    async def select(
        self, table: _Table, where: LiteralString = "", params: Sequence[Any] = ()
    ) -> list[dict[str, Any]]:
        cur = await self.conn.execute(_scoped(table, sql.SQL(where)), (*self.scope, *params))
        return [_row(r, table) for r in await cur.fetchall()]

    async def insert(self, table: _Table, row: dict[str, Any]) -> dict[str, Any]:
        cols = ("guild_id", "campaign_id", *table.columns)
        query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING {}").format(
            sql.Identifier(table.name),
            sql.SQL(", ").join(sql.Identifier(c) for c in cols),
            sql.SQL(", ").join(sql.Placeholder() * len(cols)),
            sql.SQL(", ").join(sql.Identifier(c) for c in table.columns),
        )
        cur = await self.conn.execute(query, (*self.scope, *(row[c] for c in table.columns)))
        raw = await cur.fetchone()
        assert raw is not None
        after = _row(raw, table)
        await self._log(table, after[table.key], "insert", None, after)
        return after

    async def update(self, table: _Table, row_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        before = await self.get(table, row_id)
        if before is None:
            raise MemoryRuleError(NOT_FOUND)
        changes = {k: v for k, v in changes.items() if before[k] != v}
        if not changes:
            return before
        if any(c not in table.columns or c == table.key for c in changes):
            raise ValueError(f"Not an updatable column of {table.name}: {sorted(changes)}")
        query = sql.SQL(
            "UPDATE {} SET {} WHERE guild_id = %s AND campaign_id = %s AND {} = %s RETURNING {}"
        ).format(
            sql.Identifier(table.name),
            sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in changes),
            sql.Identifier(table.key),
            sql.SQL(", ").join(sql.Identifier(c) for c in table.columns),
        )
        cur = await self.conn.execute(query, (*changes.values(), *self.scope, row_id))
        raw = await cur.fetchone()
        assert raw is not None
        after = _row(raw, table)
        await self._log(table, row_id, "update", before, after)
        return after

    async def delete(self, table: _Table, row_id: str) -> None:
        """Delete one row. Refused while anything still links to it: the database would
        otherwise delete those rows too, without logging them, and undo couldn't bring
        them back."""
        before = await self.get(table, row_id)
        if before is None:
            return
        for dep_table, column, is_list in _DEPENDENTS.get(table.name, ()):
            test = "%s = ANY({})" if is_list else "{} = %s"
            cur = await self.conn.execute(
                sql.SQL(
                    "SELECT 1 FROM {} WHERE guild_id = %s AND campaign_id = %s AND "
                    + test
                    + " LIMIT 1"
                ).format(sql.Identifier(dep_table), sql.Identifier(column)),
                (*self.scope, row_id),
            )
            if await cur.fetchone() is not None:
                raise MemoryRuleError(CHANGED_SINCE)
        await self.conn.execute(
            sql.SQL("DELETE FROM {} WHERE guild_id = %s AND campaign_id = %s AND {} = %s").format(
                sql.Identifier(table.name), sql.Identifier(table.key)
            ),
            (*self.scope, row_id),
        )
        await self._log(table, row_id, "delete", before, None)

    async def _log(
        self,
        table: _Table,
        row_id: str,
        op: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        self.version += 1
        if self.batch is None:
            self.batch = self.version
        await self.conn.execute(
            "INSERT INTO memory_changes (guild_id, campaign_id, version, batch, table_name,"
            " row_id, op, before, after, source, undoes, made_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                *self.scope,
                self.version,
                self.batch,
                table.name,
                row_id,
                op,
                None if before is None else Jsonb(before),
                None if after is None else Jsonb(after),
                self.source,
                self.undoes,
                self.now,
            ),
        )


# ---- conversions ---------------------------------------------------------------------


def _entity(r: dict[str, Any]) -> Entity:
    return Entity(
        r["id"],
        r["type"],
        r["name"],
        r["description"],
        r["status"],
        r["merged_into"],
        r["source"],
        r["created_at"],
    )


def _alias(r: dict[str, Any]) -> Alias:
    return Alias(
        r["id"],
        r["entity_id"],
        r["text"],
        r["key"],
        r["kind"],
        r["used_by"],
        r["secret"],
        r["status"],
        tuple(r["sound_codes"]),
        r["source"],
        r["created_at"],
    )


def _relation(r: dict[str, Any]) -> Relation:
    return Relation(
        r["id"],
        r["subject_id"],
        r["predicate"],
        r["object_id"],
        r["detail"],
        float(r["confidence"]),
        r["status"],
        r["source"],
        tuple(r["mention_ids"]),
        r["from_session_at"],
        r["to_session_at"],
        r["from_game_time"],
        r["to_game_time"],
        r["secret"],
        r["created_at"],
    )


def _relation_row(rel: Relation) -> dict[str, Any]:
    return {
        "id": rel.id,
        "subject_id": rel.subject_id,
        "predicate": rel.predicate,
        "object_id": rel.object_id,
        "detail": rel.detail,
        "confidence": rel.confidence,
        "status": rel.status,
        "source": rel.source,
        "mention_ids": list(rel.mention_ids),
        "from_session_at": rel.from_session_at,
        "to_session_at": rel.to_session_at,
        "from_game_time": rel.from_game_time,
        "to_game_time": rel.to_game_time,
        "secret": rel.secret,
        "created_at": rel.created_at,
    }


def _correction(r: dict[str, Any]) -> Correction:
    return Correction(
        r["id"],
        r["heard"],
        r["heard_key"],
        r["entity_id"],
        r["action"],
        r["source"],
        r["created_at"],
    )


def _flag(r: dict[str, Any]) -> Flag:
    return Flag(r["id"], r["kind"], r["relation_id"], r["other_id"], r["status"], r["created_at"])


def _type_term(r: dict[str, Any]) -> TypeTerm:
    return TypeTerm(
        r["key"], r["parent"], r["label"], r["description"], False, r["status"], r["replaced_by"]
    )


def _predicate_term(r: dict[str, Any]) -> PredicateTerm:
    return PredicateTerm(
        r["key"],
        r["label"],
        r["description"],
        tuple(r["subject_types"]),
        tuple(r["object_types"]),
        r["is_symmetric"],
        r["max_per_subject"],
        tuple(r["conflicts_with"]),
        r["parent"],
        False,
        r["status"],
        r["replaced_by"],
    )


# ---- checks on arguments -------------------------------------------------------------


def _check_source(source: str) -> None:
    if source not in SOURCES:
        raise ValueError(f"Unknown memory source: {source!r}")


def _check_choice(value: str, allowed: Sequence[str], what: str) -> None:
    if value not in allowed:
        raise ValueError(f"Unknown {what}: {value!r}")


def _check_time(value: int | None) -> None:
    if value is not None and not 0 <= value <= INT64_MAX:
        raise ValueError(f"Time out of range: {value}")


def _check_span(lo: int | None, hi: int | None) -> None:
    _check_time(lo)
    _check_time(hi)
    if lo is not None and hi is not None and hi <= lo:
        raise MemoryRuleError("A fact can't end before it starts.")


def _check_confidence(value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"Confidence must be between 0 and 1: {value}")


class MemoryStore:
    """Reads and writes one campaign's memory at a time. See the module docstring."""

    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

    @asynccontextmanager
    async def _op(
        self, guild_id: int, campaign_id: str, source: str, *, undoes: int | None = None
    ) -> AsyncIterator[_Op]:
        _check_source(source)
        async with self._db.guild(guild_id) as conn:
            # Locks the campaign row: changes to one campaign's memory happen one at a
            # time, so versions never clash.
            cur = await conn.execute(
                "SELECT memory_version FROM campaigns WHERE guild_id = %s AND id = %s FOR UPDATE",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise MemoryRuleError(NOT_HERE)
            start = int(row["memory_version"])
            op = _Op(conn, guild_id, campaign_id, source, start, int(self._clock()), undoes)
            yield op
            if op.version != start:
                await conn.execute(
                    "UPDATE campaigns SET memory_version = %s WHERE guild_id = %s AND id = %s",
                    (op.version, guild_id, campaign_id),
                )
                await conn.execute(
                    "SELECT pg_notify(%s, %s)", (NOTIFY_CHANNEL, f"{campaign_id}:{op.version}")
                )

    @asynccontextmanager
    async def _read(self, guild_id: int, campaign_id: str) -> AsyncIterator[_Op]:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT memory_version FROM campaigns WHERE guild_id = %s AND id = %s",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise MemoryRuleError(NOT_HERE)
            yield _Op(conn, guild_id, campaign_id, "entitybot", int(row["memory_version"]), 0, None)

    # ---- reads -------------------------------------------------------------------------

    async def version(self, guild_id: int, campaign_id: str) -> int:
        async with self._read(guild_id, campaign_id) as op:
            return op.version

    async def ontology(self, guild_id: int, campaign_id: str) -> Ontology:
        async with self._read(guild_id, campaign_id) as op:
            return await _load_ontology(op)

    async def entity(self, guild_id: int, campaign_id: str, entity_id: str) -> Entity | None:
        async with self._read(guild_id, campaign_id) as op:
            row = await op.get(_ENTITIES, entity_id, lock=False)
            return None if row is None else _entity(row)

    async def entities(
        self, guild_id: int, campaign_id: str, *, statuses: Sequence[str] = LIVE
    ) -> list[Entity]:
        async with self._read(guild_id, campaign_id) as op:
            rows = await op.select(
                _ENTITIES, " AND status = ANY(%s) ORDER BY name, id", [list(statuses)]
            )
            return [_entity(r) for r in rows]

    async def aliases(
        self,
        guild_id: int,
        campaign_id: str,
        *,
        entity_id: str | None = None,
        include_secret: bool = False,
    ) -> list[Alias]:
        """Live aliases. Secret ones (DM-only identities) only when asked for."""
        where = " AND status <> 'rejected'"
        params: list[Any] = []
        if entity_id is not None:
            where += " AND entity_id = %s"
            params.append(entity_id)
        if not include_secret:
            where += " AND NOT secret"
        async with self._read(guild_id, campaign_id) as op:
            rows = await op.select(_ALIASES, where + " ORDER BY key, id", params)
            return [_alias(r) for r in rows]

    async def relations(
        self,
        guild_id: int,
        campaign_id: str,
        *,
        entity_id: str | None = None,
        confirmed_only: bool = False,
        include_secret: bool = False,
    ) -> list[Relation]:
        """Facts not rejected. Helpers that record story (NPC tracker, PlotBot) pass
        `confirmed_only=True`; secret facts only when asked for."""
        where = " AND status = 'confirmed'" if confirmed_only else " AND status <> 'rejected'"
        params: list[Any] = []
        if entity_id is not None:
            where += " AND (subject_id = %s OR object_id = %s)"
            params += [entity_id, entity_id]
        if not include_secret:
            where += " AND NOT secret"
        async with self._read(guild_id, campaign_id) as op:
            rows = await op.select(_RELATIONS, where + " ORDER BY created_at, id", params)
            return [_relation(r) for r in rows]

    async def corrections(self, guild_id: int, campaign_id: str) -> list[Correction]:
        async with self._read(guild_id, campaign_id) as op:
            rows = await op.select(_CORRECTIONS, " ORDER BY heard_key, id")
            return [_correction(r) for r in rows]

    async def flags(self, guild_id: int, campaign_id: str, *, open_only: bool = True) -> list[Flag]:
        where = " AND status = 'open'" if open_only else ""
        async with self._read(guild_id, campaign_id) as op:
            rows = await op.select(_FLAGS, where + " ORDER BY created_at, id")
            return [_flag(r) for r in rows]

    # ---- entities and aliases -----------------------------------------------------------

    async def add_entity(
        self,
        guild_id: int,
        campaign_id: str,
        *,
        type: str,
        name: str,
        source: str,
        status: str = PROPOSED,
        description: str = "",
    ) -> Written[Entity]:
        """A new person, place or thing, with its name as the first alias."""
        _check_choice(status, (PROPOSED, CONFIRMED), "entity status")
        name = clean_text(name)
        if len(description) > DESCRIPTION_MAX:
            raise MemoryRuleError(f"That's too long (at most {DESCRIPTION_MAX} characters).")
        async with self._op(guild_id, campaign_id, source) as op:
            (await _load_ontology(op)).active_type(type)
            row = await op.insert(
                _ENTITIES,
                {
                    "id": new_id(),
                    "type": type,
                    "name": name,
                    "description": description.strip(),
                    "status": status,
                    "merged_into": None,
                    "source": source,
                    "created_at": op.now,
                },
            )
            await op.insert(_ALIASES, _new_alias(op, row["id"], name, "full", status, False, None))
            return Written(_entity(row), op.batch)

    async def set_entity_status(
        self, guild_id: int, campaign_id: str, entity_id: str, status: str, *, source: str
    ) -> Written[Entity]:
        _check_choice(status, FACT_STATUSES, "entity status")
        async with self._op(guild_id, campaign_id, source) as op:
            await _live_entity(op, entity_id, allow_rejected=True)
            row = await op.update(_ENTITIES, entity_id, {"status": status})
            return Written(_entity(row), op.batch)

    async def add_alias(
        self,
        guild_id: int,
        campaign_id: str,
        entity_id: str,
        text: str,
        *,
        kind: str,
        source: str,
        status: str = PROPOSED,
        secret: bool = False,
        used_by: str | None = None,
    ) -> Written[Alias]:
        """Another way this entity is said or written. Already known: nothing changes."""
        _check_choice(kind, ALIAS_KINDS, "alias kind")
        _check_choice(status, (PROPOSED, CONFIRMED), "alias status")
        text = clean_text(text)
        async with self._op(guild_id, campaign_id, source) as op:
            await _live_entity(op, entity_id)
            if used_by is not None:
                await _live_entity(op, used_by)
            key = name_key(text)
            existing = await op.select(
                _ALIASES, " AND entity_id = %s AND key = %s", [entity_id, key]
            )
            if existing:
                return Written(_alias(existing[0]), None)
            row = await op.insert(
                _ALIASES, _new_alias(op, entity_id, text, kind, status, secret, used_by)
            )
            return Written(_alias(row), op.batch)

    async def update_alias(
        self,
        guild_id: int,
        campaign_id: str,
        alias_id: str,
        *,
        source: str,
        status: str | None = None,
        secret: bool | None = None,
    ) -> Written[Alias]:
        """Confirm or reject an alias, or mark it DM-only ([🤫 Keep secret from players])."""
        changes: dict[str, Any] = {}
        if status is not None:
            _check_choice(status, FACT_STATUSES, "alias status")
            changes["status"] = status
        if secret is not None:
            changes["secret"] = secret
        async with self._op(guild_id, campaign_id, source) as op:
            row = await op.update(_ALIASES, alias_id, changes)
            return Written(_alias(row), op.batch)

    async def merge(
        self,
        guild_id: int,
        campaign_id: str,
        keep_id: str,
        gone_id: str,
        *,
        source: str,
        dm_said_same: bool = False,
    ) -> Written[Entity]:
        """Two entries are the same person or thing: move everything onto `keep_id`.

        Two proposed entries may merge on strong evidence. If either is confirmed, only
        the DM can say they're the same (`dm_said_same`). Undo splits them again.
        """
        if keep_id == gone_id:
            raise MemoryRuleError("That's the same entry.")
        async with self._op(guild_id, campaign_id, source) as op:
            keep = await _live_entity(op, keep_id)
            gone = await _live_entity(op, gone_id)
            if CONFIRMED in (keep["status"], gone["status"]) and not dm_said_same:
                raise MemoryRuleError("Only the DM can say these two are the same.")
            keep_keys = {
                a["key"] for a in await op.select(_ALIASES, " AND entity_id = %s", [keep_id])
            }
            for alias in await op.select(_ALIASES, " AND entity_id = %s", [gone_id]):
                if alias["key"] in keep_keys:
                    await op.delete(_ALIASES, alias["id"])
                else:
                    await op.update(_ALIASES, alias["id"], {"entity_id": keep_id})
            for alias in await op.select(_ALIASES, " AND used_by = %s", [gone_id]):
                await op.update(_ALIASES, alias["id"], {"used_by": keep_id})
            onto = await _load_ontology(op)
            for rel in await op.select(
                _RELATIONS, " AND (subject_id = %s OR object_id = %s)", [gone_id, gone_id]
            ):
                subject = keep_id if rel["subject_id"] == gone_id else rel["subject_id"]
                obj = keep_id if rel["object_id"] == gone_id else rel["object_id"]
                if subject == obj:  # "Bell is an ally of Belleros" says nothing any more
                    for flag in await op.select(
                        _FLAGS, " AND (relation_id = %s OR other_id = %s)", [rel["id"], rel["id"]]
                    ):
                        await op.delete(_FLAGS, flag["id"])
                    await op.delete(_RELATIONS, rel["id"])
                    continue
                pred = onto.predicates.get(rel["predicate"])
                if pred is not None:
                    subject, obj = ordered(pred, subject, obj)
                await op.update(_RELATIONS, rel["id"], {"subject_id": subject, "object_id": obj})
            for table in (_MENTIONS, _CORRECTIONS):
                for row in await op.select(table, " AND entity_id = %s", [gone_id]):
                    await op.update(table, row["id"], {"entity_id": keep_id})
            if gone["status"] == CONFIRMED and keep["status"] == PROPOSED:
                await op.update(_ENTITIES, keep_id, {"status": CONFIRMED})
            # Older merges pointing at gone_id now chain to keep_id; `resolve` follows it.
            await op.update(_ENTITIES, gone_id, {"status": MERGED, "merged_into": keep_id})
            row = await op.get(_ENTITIES, keep_id)
            assert row is not None
            return Written(_entity(row), op.batch)

    async def resolve(self, guild_id: int, campaign_id: str, entity_id: str) -> Entity | None:
        """The entity an ID stands for now, following merges."""
        async with self._read(guild_id, campaign_id) as op:
            seen: set[str] = set()
            current: str | None = entity_id
            while current is not None and current not in seen:
                seen.add(current)
                row = await op.get(_ENTITIES, current, lock=False)
                if row is None:
                    return None
                if row["status"] != MERGED:
                    return _entity(row)
                current = row["merged_into"]
            return None

    # ---- relationships ----------------------------------------------------------------

    async def add_relation(
        self,
        guild_id: int,
        campaign_id: str,
        subject_id: str,
        predicate: str,
        object_id: str,
        *,
        source: str,
        confidence: float,
        status: str = PROPOSED,
        detail: str = "",
        from_session_at: int | None = None,
        to_session_at: int | None = None,
        secret: bool = False,
        mention_ids: Sequence[str] = (),
    ) -> Written[tuple[Relation, list[Flag]]]:
        """Store a fact, checked against the rules. Problems become flags for the DM to
        review; the fact is stored as given either way. An identical fact already known
        is returned unchanged."""
        _check_choice(status, (PROPOSED, CONFIRMED), "relation status")
        _check_confidence(confidence)
        _check_span(from_session_at, to_session_at)
        if len(detail) > DETAIL_MAX:
            raise MemoryRuleError(f"That's too long (at most {DETAIL_MAX} characters).")
        if not all(is_id(m) for m in mention_ids):
            raise ValueError("Bad mention ID")
        if subject_id == object_id:
            raise MemoryRuleError("A relationship needs two different entries.")
        async with self._op(guild_id, campaign_id, source) as op:
            onto = await _load_ontology(op)
            pred = onto.active_predicate(predicate)
            subject = await _live_entity(op, subject_id)
            obj = await _live_entity(op, object_id)
            s, o = ordered(pred, subject_id, object_id)
            types = {subject_id: subject["type"], object_id: obj["type"]}
            new = Relation(
                new_id(),
                s,
                predicate,
                o,
                " ".join(detail.split()),
                confidence,
                status,
                source,
                tuple(mention_ids),
                from_session_at,
                to_session_at,
                None,
                None,
                secret,
                op.now,
            )
            existing = [
                _relation(r)
                for r in await op.select(
                    _RELATIONS,
                    " AND status <> 'rejected' AND (subject_id = ANY(%s) OR object_id = ANY(%s))",
                    [[s, o], [s, o]],
                )
            ]
            same = duplicate_of(new, existing)
            if same is not None:
                return Written((same, []), None)
            await op.insert(_RELATIONS, _relation_row(new))
            flags = []
            for problem in check_relation(onto, new, types[s], types[o], existing):
                flag = await op.insert(
                    _FLAGS,
                    {
                        "id": new_id(),
                        "kind": problem.kind,
                        "relation_id": new.id,
                        "other_id": problem.other_id,
                        "status": "open",
                        "created_at": op.now,
                    },
                )
                flags.append(_flag(flag))
            return Written((new, flags), op.batch)

    async def update_relation(
        self,
        guild_id: int,
        campaign_id: str,
        relation_id: str,
        *,
        source: str,
        status: str | None = None,
        to_session_at: int | None = None,
        secret: bool | None = None,
    ) -> Written[Relation]:
        """Confirm or reject a fact, mark it secret, or end it (history is kept: a new
        fact replaces it rather than overwriting it)."""
        changes: dict[str, Any] = {}
        if status is not None:
            _check_choice(status, FACT_STATUSES, "relation status")
            changes["status"] = status
        if secret is not None:
            changes["secret"] = secret
        async with self._op(guild_id, campaign_id, source) as op:
            if to_session_at is not None:
                current = await op.get(_RELATIONS, relation_id)
                if current is None:
                    raise MemoryRuleError(NOT_FOUND)
                _check_span(current["from_session_at"], to_session_at)
                changes["to_session_at"] = to_session_at
            row = await op.update(_RELATIONS, relation_id, changes)
            return Written(_relation(row), op.batch)

    async def resolve_flag(
        self, guild_id: int, campaign_id: str, flag_id: str, *, source: str
    ) -> Written[Flag]:
        async with self._op(guild_id, campaign_id, source) as op:
            row = await op.update(_FLAGS, flag_id, {"status": "resolved"})
            return Written(_flag(row), op.batch)

    # ---- mentions and corrections -------------------------------------------------------

    async def add_mention(
        self,
        guild_id: int,
        campaign_id: str,
        entity_id: str,
        *,
        line_ref: str,
        span: tuple[int, int],
        confidence: float,
        method: str,
        source: str,
        session_started_at: int | None = None,
    ) -> Written[str]:
        _check_choice(method, MENTION_METHODS, "mention method")
        _check_confidence(confidence)
        _check_time(session_started_at)
        if not 0 < len(line_ref) <= LINE_REF_MAX or not 0 <= span[0] < span[1] <= 2**31 - 1:
            raise ValueError("Bad line reference or span")
        async with self._op(guild_id, campaign_id, source) as op:
            await _live_entity(op, entity_id)
            row = await op.insert(
                _MENTIONS,
                {
                    "id": new_id(),
                    "entity_id": entity_id,
                    "session_started_at": session_started_at,
                    "line_ref": line_ref,
                    "span_start": span[0],
                    "span_end": span[1],
                    "confidence": confidence,
                    "method": method,
                    "created_at": op.now,
                },
            )
            return Written(str(row["id"]), op.batch)

    async def add_correction(
        self,
        guild_id: int,
        campaign_id: str,
        heard: str,
        *,
        action: str,
        source: str,
        entity_id: str | None = None,
    ) -> Written[Correction]:
        """A word heard should become an entity's name (`fix`), or must stay as heard
        (`keep`: from an Undo or "Keep as heard"). Already known: nothing changes."""
        _check_choice(action, (FIX, KEEP), "correction action")
        if (action == FIX) != (entity_id is not None):
            raise ValueError("A fix needs an entity; a keep must not have one")
        heard = clean_text(heard)
        async with self._op(guild_id, campaign_id, source) as op:
            if entity_id is not None:
                await _live_entity(op, entity_id)
            key = name_key(heard)
            for row in await op.select(_CORRECTIONS, " AND heard_key = %s", [key]):
                if (row["action"], row["entity_id"]) == (action, entity_id):
                    return Written(_correction(row), None)
            row = await op.insert(
                _CORRECTIONS,
                {
                    "id": new_id(),
                    "heard": heard,
                    "heard_key": key,
                    "entity_id": entity_id,
                    "action": action,
                    "source": source,
                    "created_at": op.now,
                },
            )
            return Written(_correction(row), op.batch)

    # ---- extending the rules ----------------------------------------------------------

    async def add_type(
        self,
        guild_id: int,
        campaign_id: str,
        term: TypeTerm,
        *,
        examples: Sequence[str],
        reason: str,
        source: str,
        session_started_at: int | None = None,
    ) -> Written[TypeTerm]:
        """A new kind of thing for this campaign only. Refused when an existing one
        fits, or when this session already added its share of new terms."""
        async with self._op(guild_id, campaign_id, source) as op:
            onto = await _load_ontology(op)
            onto.check_new_term(term.key, term.label, term.description, examples, reason)
            onto.check_new_type(term)
            await _check_growth(op, session_started_at)
            row = await op.insert(
                _TYPES,
                {
                    "key": term.key,
                    "parent": term.parent,
                    "label": term.label.strip(),
                    "description": term.description.strip(),
                    "examples": [e.strip() for e in examples],
                    "reason": reason.strip(),
                    "status": ACTIVE,
                    "replaced_by": None,
                    "created_at": op.now,
                },
            )
            return Written(_type_term(row), op.batch)

    async def add_predicate(
        self,
        guild_id: int,
        campaign_id: str,
        term: PredicateTerm,
        *,
        examples: Sequence[str],
        reason: str,
        source: str,
        session_started_at: int | None = None,
    ) -> Written[PredicateTerm]:
        """A new kind of relationship for this campaign only (see `add_type`)."""
        async with self._op(guild_id, campaign_id, source) as op:
            onto = await _load_ontology(op)
            onto.check_new_term(term.key, term.label, term.description, examples, reason)
            onto.check_new_predicate(term)
            await _check_growth(op, session_started_at)
            row = await op.insert(
                _PREDICATES,
                {
                    "key": term.key,
                    "parent": term.parent,
                    "label": term.label.strip(),
                    "description": term.description.strip(),
                    "examples": [e.strip() for e in examples],
                    "reason": reason.strip(),
                    "subject_types": list(term.subject_types),
                    "object_types": list(term.object_types),
                    "is_symmetric": term.symmetric,
                    "max_per_subject": term.max_per_subject,
                    "conflicts_with": list(term.conflicts_with),
                    "status": ACTIVE,
                    "replaced_by": None,
                    "created_at": op.now,
                },
            )
            return Written(_predicate_term(row), op.batch)

    async def deprecate_term(
        self,
        guild_id: int,
        campaign_id: str,
        key: str,
        *,
        source: str,
        replaced_by: str | None = None,
    ) -> Written[None]:
        """Retire a campaign's own term. It stays, so old facts still read; the core
        can't be retired here."""
        async with self._op(guild_id, campaign_id, source) as op:
            onto = await _load_ontology(op)
            table = _TYPES if key in onto.types else _PREDICATES
            term = onto.types.get(key) or onto.predicates.get(key)
            if term is None or term.core:
                raise MemoryRuleError(f"{key} can't be retired.")
            if replaced_by is not None:
                if table is _TYPES:
                    onto.active_type(replaced_by)
                else:
                    onto.active_predicate(replaced_by)
            await op.update(table, key, {"status": DEPRECATED, "replaced_by": replaced_by})
            return Written(None, op.batch)

    # ---- undo -------------------------------------------------------------------------

    async def undo(
        self, guild_id: int, campaign_id: str, batch: int, *, source: str = "undo"
    ) -> Written[None]:
        """Reverse every row change of one operation, newest first. Refused if those
        rows changed again since (undo the later change first) or it was already
        undone. Undoing an undo redoes it."""
        async with self._op(guild_id, campaign_id, source, undoes=batch) as op:
            cur = await op.conn.execute(
                "SELECT 1 FROM memory_changes WHERE guild_id = %s AND campaign_id = %s"
                " AND undoes = %s LIMIT 1",
                (*op.scope, batch),
            )
            if await cur.fetchone() is not None:
                raise MemoryRuleError("That was already undone.")
            cur = await op.conn.execute(
                "SELECT table_name, row_id, op, before, after FROM memory_changes"
                " WHERE guild_id = %s AND campaign_id = %s AND batch = %s"
                " ORDER BY version DESC",
                (*op.scope, batch),
            )
            changes = await cur.fetchall()
            if not changes:
                raise MemoryRuleError(NOT_FOUND)
            for change in changes:
                table = _BY_NAME[change["table_name"]]
                current = await op.get(table, change["row_id"])
                if current != change["after"]:
                    raise MemoryRuleError(CHANGED_SINCE)
                before = change["before"]
                if change["op"] == "insert":
                    await op.delete(table, change["row_id"])
                elif change["op"] == "update":
                    await op.update(table, change["row_id"], before)
                else:
                    await op.insert(table, before)
            return Written(None, op.batch)


def _new_alias(
    op: _Op,
    entity_id: str,
    text: str,
    kind: str,
    status: str,
    secret: bool,
    used_by: str | None,
) -> dict[str, Any]:
    return {
        "id": new_id(),
        "entity_id": entity_id,
        "text": text,
        "key": name_key(text) or text.casefold(),
        "kind": kind,
        "used_by": used_by,
        "secret": secret,
        "status": status,
        "sound_codes": [],  # filled by the matching step (Transcript Cleaner, #127)
        "source": op.source,
        "created_at": op.now,
    }


async def _live_entity(op: _Op, entity_id: str, *, allow_rejected: bool = False) -> dict[str, Any]:
    row = await op.get(_ENTITIES, entity_id)
    allowed = (*LIVE, REJECTED) if allow_rejected else LIVE
    if row is None or row["status"] not in allowed:
        raise MemoryRuleError(NOT_FOUND)
    return row


async def _load_ontology(op: _Op) -> Ontology:
    types = [_type_term(r) for r in await op.select(_TYPES)]
    predicates = [_predicate_term(r) for r in await op.select(_PREDICATES)]
    return Ontology.build(types, predicates)


async def _check_growth(op: _Op, session_started_at: int | None) -> None:
    if session_started_at is None:
        return
    cur = await op.conn.execute(
        "SELECT (SELECT count(*) FROM memory_types WHERE guild_id = %s AND campaign_id = %s"
        " AND created_at >= %s) + (SELECT count(*) FROM memory_predicates"
        " WHERE guild_id = %s AND campaign_id = %s AND created_at >= %s) AS n",
        (*op.scope, session_started_at, *op.scope, session_started_at),
    )
    row = await cur.fetchone()
    if row is not None and row["n"] >= MAX_NEW_TERMS_PER_SESSION:
        raise MemoryRuleError("Enough new kinds of things for one session; review them first.")


# ---- backups -------------------------------------------------------------------------

DAMAGED = "This backup file is damaged (bad campaign memory entry)."
_KIND_OF = {
    "memory_types": "type",
    "memory_predicates": "predicate",
    "memory_entities": "entity",
    "memory_aliases": "alias",
    "memory_mentions": "mention",
    "memory_relations": "relation",
    "memory_corrections": "correction",
    "memory_flags": "flag",
}
_TABLE_OF = {kind: _BY_NAME[name] for name, kind in _KIND_OF.items()}

_TEXT_LIMITS = {
    "label": LABEL_MAX,
    "description": DESCRIPTION_MAX,
    "reason": DESCRIPTION_MAX,
    "name": NAME_MAX,
    "text": NAME_MAX,
    "key": NAME_MAX,
    "heard": NAME_MAX,
    "heard_key": NAME_MAX,
    "detail": DETAIL_MAX,
    "line_ref": LINE_REF_MAX,
    "parent": 40,
    "replaced_by": 40,
    "type": 40,
    "predicate": 40,
}
_CHOICES: dict[str, Sequence[str]] = {
    "status": (*ENTITY_STATUSES, ACTIVE, DEPRECATED, "open", "resolved"),
    "kind": (*ALIAS_KINDS, *FLAG_KINDS),
    "method": MENTION_METHODS,
    "action": (FIX, KEEP),
    "source": SOURCES,
}
_IDS = {
    "id",
    "entity_id",
    "merged_into",
    "used_by",
    "subject_id",
    "object_id",
    "relation_id",
    "other_id",
}
_INTS = {
    "created_at",
    "session_started_at",
    "span_start",
    "span_end",
    "from_session_at",
    "to_session_at",
    "from_game_time",
    "to_game_time",
    "max_per_subject",
}
_LISTS = {
    "examples",
    "subject_types",
    "object_types",
    "conflicts_with",
    "sound_codes",
    "mention_ids",
}
_NULLABLE = {
    "merged_into",
    "used_by",
    "other_id",
    "entity_id",
    "replaced_by",
    "parent",
    "session_started_at",
    "from_session_at",
    "to_session_at",
    "from_game_time",
    "to_game_time",
    "max_per_subject",
}
_LIST_MAX = 50


def _valid(column: str, value: object) -> bool:
    """Is a value from an untrusted backup file the right shape for its column?"""
    if value is None:
        return column in _NULLABLE
    if column in _IDS:
        return is_id(value)
    if column in _INTS:
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= INT64_MAX
    if column in _LISTS:
        return (
            isinstance(value, list)
            and len(value) <= _LIST_MAX
            and all(isinstance(v, str) and 0 < len(v) <= DESCRIPTION_MAX for v in value)
        )
    if column in ("secret", "is_symmetric"):
        return isinstance(value, bool)
    if column == "confidence":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1
    if column in _CHOICES:
        return isinstance(value, str) and value in _CHOICES[column]
    limit = _TEXT_LIMITS.get(column)
    return limit is not None and isinstance(value, str) and len(value) <= limit


class MemorySection:
    """Campaign memory in backups (an `ExportSection`). The change log isn't included:
    a restored campaign starts with a fresh undo history."""

    name = "memory"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        out: list[Any] = []
        for table in _BACKED_UP:
            order = sql.SQL(" ORDER BY {}").format(sql.Identifier(table.key))
            cur = await conn.execute(_scoped(table, order), (guild_id, campaign_id))
            out += [{"kind": _KIND_OF[table.name], **_row(r, table)} for r in await cur.fetchall()]
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: list[Any]) -> None:
        by_kind: dict[str, list[dict[str, Any]]] = {kind: [] for kind in _TABLE_OF}
        for raw in rows:
            if not isinstance(raw, dict) or raw.get("kind") not in _TABLE_OF:
                raise CampaignError(DAMAGED)
            table = _TABLE_OF[raw["kind"]]
            if set(raw) != {"kind", *table.columns}:
                raise CampaignError(DAMAGED)
            if not all(_valid(c, raw[c]) for c in table.columns):
                raise CampaignError(DAMAGED)
            by_kind[raw["kind"]].append(raw)
        _check_terms(by_kind)
        try:
            for kind, table in _TABLE_OF.items():
                for raw in by_kind[kind]:
                    cols = ("guild_id", "campaign_id", *table.columns)
                    await conn.execute(
                        sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                            sql.Identifier(table.name),
                            sql.SQL(", ").join(sql.Identifier(c) for c in cols),
                            sql.SQL(", ").join(sql.Placeholder() * len(cols)),
                        ),
                        (guild_id, campaign_id, *(raw[c] for c in table.columns)),
                    )
            # Check links now, not at commit, so a broken file gives a plain message.
            await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        except (pg_errors.IntegrityError, pg_errors.DataError) as exc:
            raise CampaignError(DAMAGED) from exc

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        for name in ("memory_changes", *(t.name for t in reversed(_BACKED_UP))):
            await conn.execute(
                sql.SQL("DELETE FROM {} WHERE guild_id = %s AND campaign_id = %s").format(
                    sql.Identifier(name)
                ),
                (guild_id, campaign_id),
            )


def _check_terms(by_kind: dict[str, list[dict[str, Any]]]) -> None:
    """Entities and facts in a backup must use known kinds; extensions can't replace
    the core."""
    core = {t.key for t in CORE_TYPES} | {p.key for p in CORE_PREDICATES}
    if any(r["key"] in core for r in (*by_kind["type"], *by_kind["predicate"])):
        raise CampaignError(DAMAGED)
    onto = Ontology.build(
        (_type_term(r) for r in by_kind["type"]),
        (_predicate_term(r) for r in by_kind["predicate"]),
    )
    if any(r["type"] not in onto.types for r in by_kind["entity"]):
        raise CampaignError(DAMAGED)
    if any(r["predicate"] not in onto.predicates for r in by_kind["relation"]):
        raise CampaignError(DAMAGED)
