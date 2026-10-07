"""Row access for campaign memory, and the change log that makes every write undoable.

`Scope` reads one campaign's rows; `Changes` also writes them, logging every row with
its before and after values under one batch number. `undo_batch` reverses a batch.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, LiteralString

from psycopg import sql
from psycopg.types.json import Jsonb

from dmbot.db import Conn
from dmbot.memory.models import MemoryRuleError

NOT_FOUND = "DMbot doesn't remember that any more."
CHANGED_SINCE = "That was changed again since, so it can't be undone on its own."


@dataclass(frozen=True, slots=True)
class Table:
    name: str
    key: str  # the column identifying a row within a campaign
    columns: tuple[str, ...]  # excluding guild_id and campaign_id


TYPES = Table(
    "memory_types",
    "key",
    ("key", "parent", "label", "description", "examples", "reason", "status", "replaced_by",
     "created_at"),
)  # fmt: skip
PREDICATES = Table(
    "memory_predicates",
    "key",
    ("key", "parent", "label", "description", "examples", "reason", "subject_types",
     "object_types", "is_symmetric", "max_per_subject", "conflicts_with", "status",
     "replaced_by", "created_at"),
)  # fmt: skip
ENTITIES = Table(
    "memory_entities",
    "id",
    (
        "id",
        "type",
        "name",
        "description",
        "status",
        "merged_into",
        "source",
        "created_at",
        "played_by",
    ),
)
ALIASES = Table(
    "memory_aliases",
    "id",
    ("id", "entity_id", "text", "key", "kind", "used_by", "secret", "status", "sound_codes",
     "source", "created_at"),
)  # fmt: skip
MENTIONS = Table(
    "memory_mentions",
    "id",
    ("id", "entity_id", "session_started_at", "line_ref", "span_start", "span_end",
     "confidence", "method", "created_at"),
)  # fmt: skip
RELATIONS = Table(
    "memory_relations",
    "id",
    ("id", "subject_id", "predicate", "object_id", "detail", "confidence", "status", "source",
     "mention_ids", "from_session_at", "to_session_at", "from_game_time", "to_game_time",
     "secret", "created_at"),
)  # fmt: skip
CORRECTIONS = Table(
    "memory_corrections",
    "id",
    ("id", "heard", "heard_key", "entity_id", "action", "source", "created_at"),
)
FLAGS = Table(
    "memory_flags",
    "id",
    ("id", "kind", "relation_id", "other_id", "status", "created_at"),
)
ALL = (TYPES, PREDICATES, ENTITIES, ALIASES, MENTIONS, RELATIONS, CORRECTIONS, FLAGS)
BY_NAME = {t.name: t for t in ALL}

# What links to a row (table, column, is the column a list), checked before deleting it,
# because the database would otherwise delete those rows too, without logging them.
DEPENDENTS: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "memory_types": (
        ("memory_types", "parent", False),
        ("memory_types", "replaced_by", False),
        ("memory_entities", "type", False),
        ("memory_predicates", "subject_types", True),
        ("memory_predicates", "object_types", True),
    ),
    "memory_predicates": (
        ("memory_predicates", "parent", False),
        ("memory_predicates", "replaced_by", False),
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


def scoped_select(table: Table, where: sql.Composable | None = None) -> sql.Composed:
    """SELECT a table's columns for one server and campaign, plus `where`."""
    return sql.SQL("SELECT {} FROM {} WHERE guild_id = %s AND campaign_id = %s{}").format(
        sql.SQL(", ").join(sql.Identifier(c) for c in table.columns),
        sql.Identifier(table.name),
        where if where is not None else sql.SQL(""),
    )


def row_of(raw: dict[str, Any], table: Table) -> dict[str, Any]:
    return {c: raw[c] for c in table.columns}


class Scope:
    """Reads one campaign's memory inside an open per-server transaction."""

    def __init__(self, conn: Conn, guild_id: int, campaign_id: str, version: int) -> None:
        self.conn = conn
        self.guild_id = guild_id
        self.campaign_id = campaign_id
        self.version = version

    @property
    def ids(self) -> tuple[int, str]:
        return self.guild_id, self.campaign_id

    async def get(self, table: Table, row_id: str, *, lock: bool = True) -> dict[str, Any] | None:
        where = sql.SQL(" AND {} = %s{}").format(
            sql.Identifier(table.key), sql.SQL(" FOR UPDATE" if lock else "")
        )
        cur = await self.conn.execute(scoped_select(table, where), (*self.ids, row_id))
        raw = await cur.fetchone()
        return None if raw is None else row_of(raw, table)

    async def select(
        self, table: Table, where: LiteralString = "", params: Sequence[Any] = ()
    ) -> list[dict[str, Any]]:
        cur = await self.conn.execute(scoped_select(table, sql.SQL(where)), (*self.ids, *params))
        return [row_of(r, table) for r in await cur.fetchall()]


class Changes(Scope):
    """Writes one campaign's memory. Every row it touches is logged in memory_changes
    with its before and after values, under one batch number."""

    def __init__(
        self,
        conn: Conn,
        guild_id: int,
        campaign_id: str,
        version: int,
        *,
        source: str,
        now: int,
        undoes: int | None = None,
    ) -> None:
        super().__init__(conn, guild_id, campaign_id, version)
        self.source = source
        self.now = now
        self.undoes = undoes
        self.batch: int | None = None
        self.tables: set[str] = set()  # tables this write changed

    async def insert(self, table: Table, row: dict[str, Any]) -> dict[str, Any]:
        cols = ("guild_id", "campaign_id", *table.columns)
        query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING {}").format(
            sql.Identifier(table.name),
            sql.SQL(", ").join(sql.Identifier(c) for c in cols),
            sql.SQL(", ").join(sql.Placeholder() * len(cols)),
            sql.SQL(", ").join(sql.Identifier(c) for c in table.columns),
        )
        cur = await self.conn.execute(query, (*self.ids, *(row[c] for c in table.columns)))
        raw = await cur.fetchone()
        assert raw is not None
        after = row_of(raw, table)
        await self._log(table, after[table.key], "insert", None, after)
        return after

    async def update(self, table: Table, row_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        unknown = set(changes) - set(table.columns)
        if unknown:
            raise ValueError(f"Not a column of {table.name}: {sorted(unknown)}")
        before = await self.get(table, row_id)
        if before is None:
            raise MemoryRuleError(NOT_FOUND)
        changes = {k: v for k, v in changes.items() if before[k] != v}
        if not changes:
            return before
        if table.key in changes:
            raise ValueError(f"{table.name}.{table.key} can't change")
        query = sql.SQL(
            "UPDATE {} SET {} WHERE guild_id = %s AND campaign_id = %s AND {} = %s RETURNING {}"
        ).format(
            sql.Identifier(table.name),
            sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in changes),
            sql.Identifier(table.key),
            sql.SQL(", ").join(sql.Identifier(c) for c in table.columns),
        )
        cur = await self.conn.execute(query, (*changes.values(), *self.ids, row_id))
        raw = await cur.fetchone()
        assert raw is not None
        after = row_of(raw, table)
        await self._log(table, row_id, "update", before, after)
        return after

    async def delete(self, table: Table, row_id: str) -> None:
        """Delete one row. Refused while anything still links to it (see DEPENDENTS)."""
        before = await self.get(table, row_id)
        if before is None:
            return
        for dep_table, column, is_list in DEPENDENTS.get(table.name, ()):
            test = "%s = ANY({})" if is_list else "{} = %s"
            query = sql.SQL(
                "SELECT 1 FROM {} WHERE guild_id = %s AND campaign_id = %s AND " + test + " LIMIT 1"
            ).format(sql.Identifier(dep_table), sql.Identifier(column))
            cur = await self.conn.execute(query, (*self.ids, row_id))
            if await cur.fetchone() is not None:
                raise MemoryRuleError(CHANGED_SINCE)
        await self.conn.execute(
            sql.SQL("DELETE FROM {} WHERE guild_id = %s AND campaign_id = %s AND {} = %s").format(
                sql.Identifier(table.name), sql.Identifier(table.key)
            ),
            (*self.ids, row_id),
        )
        await self._log(table, row_id, "delete", before, None)

    async def _log(
        self,
        table: Table,
        row_id: str,
        op: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        self.version += 1
        self.tables.add(table.name)
        if self.batch is None:
            self.batch = self.version
        await self.conn.execute(
            "INSERT INTO memory_changes (guild_id, campaign_id, version, batch, table_name,"
            " row_id, op, before, after, source, undoes, made_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                *self.ids,
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


def _only_closed_since(table: Table, current: dict[str, Any] | None, after: Any) -> bool:
    """A flag the after-session cleanup closed since this batch (#348): still the batch's
    own row, so undoing the batch may go ahead. Undoing an insert deletes the flag;
    undoing an update restores the flag as it was, open again if it was open then (the
    next cleanup closes it again if the clash is still gone)."""
    return (
        table is FLAGS
        and current is not None
        and after is not None
        and (after["status"], current["status"]) == ("open", "resolved")
        and {**current, "status": after["status"]} == after
    )


async def undo_batch(changes: Changes, batch: int) -> None:
    """Reverse every row change of one batch, newest first.

    Refused if the batch was already undone, or if any of its rows changed again since
    (undo the later change first). Undoing an undo redoes it; an undo that was itself
    undone stays undone (the original batch can't be undone a second time).
    """
    conn, ids = changes.conn, changes.ids
    cur = await conn.execute(
        "SELECT 1 FROM memory_changes WHERE guild_id = %s AND campaign_id = %s AND undoes = %s"
        " LIMIT 1",
        (*ids, batch),
    )
    if await cur.fetchone() is not None:
        raise MemoryRuleError("That was already undone.")
    cur = await conn.execute(
        "SELECT table_name, row_id, op, before, after FROM memory_changes"
        " WHERE guild_id = %s AND campaign_id = %s AND batch = %s ORDER BY version DESC",
        (*ids, batch),
    )
    rows = await cur.fetchall()
    if not rows:
        raise MemoryRuleError(NOT_FOUND)
    for change in rows:
        table = BY_NAME[change["table_name"]]
        current = await changes.get(table, change["row_id"])
        if current != change["after"] and not _only_closed_since(table, current, change["after"]):
            raise MemoryRuleError(CHANGED_SINCE)
        if change["op"] == "insert":
            await changes.delete(table, change["row_id"])
        elif change["op"] == "update":
            await changes.update(table, change["row_id"], change["before"])
        else:
            await changes.insert(table, change["before"])
