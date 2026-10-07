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
LOG_ROWS_PER_STATEMENT = 1000  # 12 values each, well under Postgres's 65,535
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

    # ---- many rows in one statement (#164) ------------------------------------------
    # Each row is still logged on its own, with its before and after values, exactly as
    # update/insert/delete log it, so undo and its tests see the same change log.

    async def update_where(
        self, table: Table, changes: dict[str, Any], where: LiteralString, params: Sequence[Any]
    ) -> list[dict[str, Any]]:
        """Set the same values on every matching row that differs, in one statement.
        `where` reads like Scope.select's (" AND entity_id = %s"); the columns compared
        must be text-like (ids). Returns the rows after, in key order."""
        self._check_columns(table, changes)
        differs = sql.SQL(" OR ").join(
            sql.SQL("{} IS DISTINCT FROM %s").format(sql.Identifier(c)) for c in changes
        )
        old = sql.SQL(
            "SELECT {} FROM {} WHERE guild_id = %s AND campaign_id = %s{} AND ({}) FOR UPDATE"
        ).format(
            sql.SQL(", ").join(sql.Identifier(c) for c in table.columns),
            sql.Identifier(table.name),
            sql.SQL(where),
            differs,
        )
        sets = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in changes)
        params_all = (*self.ids, *params, *changes.values(), *changes.values())
        return await self._update_from(table, old, sets, params_all)

    async def update_rows(
        self, table: Table, rows: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Give each row its own new values, in one statement. `rows`: row id → the
        columns to set (the same columns for every row). Rows already holding those
        values are left alone and not logged. Returns the rows after, in key order."""
        if not rows:
            return []
        columns = sorted({c for changes in rows.values() for c in changes})
        for changes in rows.values():
            self._check_columns(table, changes)
            if sorted(changes) != columns:
                raise ValueError("update_rows needs the same columns for every row")
        new = [{table.key: row_id, **changes} for row_id, changes in rows.items()]
        differs = sql.SQL(" OR ").join(
            sql.SQL("t.{0} IS DISTINCT FROM n.{0}").format(sql.Identifier(c)) for c in columns
        )
        old = sql.SQL(
            "SELECT {} FROM {} t JOIN jsonb_populate_recordset(NULL::{}, %s) n USING ({})"
            " WHERE t.guild_id = %s AND t.campaign_id = %s AND ({}) FOR UPDATE OF t"
        ).format(
            sql.SQL(", ").join(sql.SQL("t.{}").format(sql.Identifier(c)) for c in table.columns),
            sql.Identifier(table.name),
            sql.Identifier(table.name),
            sql.Identifier(table.key),
            differs,
        )
        sets = sql.SQL(", ").join(sql.SQL("{0} = n.{0}").format(sql.Identifier(c)) for c in columns)
        new_rows = sql.SQL(", jsonb_populate_recordset(NULL::{}, %s) n").format(
            sql.Identifier(table.name)
        )
        key_match = sql.SQL(" AND n.{0} = t.{0}").format(sql.Identifier(table.key))
        return await self._update_from(
            table, old, sets, (Jsonb(new), *self.ids, Jsonb(new)), extra=(new_rows, key_match)
        )

    async def _update_from(
        self,
        table: Table,
        old: sql.Composable,
        sets: sql.Composable,
        params: Sequence[Any],
        *,
        extra: tuple[sql.Composable, sql.Composable] = (sql.SQL(""), sql.SQL("")),
    ) -> list[dict[str, Any]]:
        """One UPDATE that also returns each row's values before it, then one log write."""
        query = sql.SQL(
            "WITH old AS ({}) UPDATE {} t SET {} FROM old{}"
            " WHERE t.guild_id = %s AND t.campaign_id = %s AND t.{} = old.{}{} RETURNING {}"
        ).format(
            old,
            sql.Identifier(table.name),
            sets,
            extra[0],
            sql.Identifier(table.key),
            sql.Identifier(table.key),
            extra[1],
            sql.SQL(", ").join(
                [
                    sql.SQL("old.{0} AS {1}").format(sql.Identifier(c), sql.Identifier("b_" + c))
                    for c in table.columns
                ]
                + [
                    sql.SQL("t.{0} AS {1}").format(sql.Identifier(c), sql.Identifier("a_" + c))
                    for c in table.columns
                ]
            ),
        )
        cur = await self.conn.execute(query, (*params, *self.ids))
        found = sorted(await cur.fetchall(), key=lambda r: str(r["b_" + table.key]))
        logged = [
            (
                {c: r["b_" + c] for c in table.columns},
                {c: r["a_" + c] for c in table.columns},
            )
            for r in found
        ]
        await self._log_many(
            table, "update", [(after[table.key], before, after) for before, after in logged]
        )
        return [after for _, after in logged]

    def _check_columns(self, table: Table, changes: dict[str, Any]) -> None:
        unknown = set(changes) - set(table.columns)
        if unknown:
            raise ValueError(f"Not a column of {table.name}: {sorted(unknown)}")
        if table.key in changes:
            raise ValueError(f"{table.name}.{table.key} can't change")

    async def _log_many(
        self,
        table: Table,
        op: str,
        rows: Sequence[tuple[str, dict[str, Any] | None, dict[str, Any] | None]],
    ) -> None:
        """Log many row changes, each with its own version, a thousand to a statement
        (Postgres takes at most 65,535 values in one statement; each row has 12)."""
        for start in range(0, len(rows), LOG_ROWS_PER_STATEMENT):
            await self._log_some(table, op, rows[start : start + LOG_ROWS_PER_STATEMENT])

    async def _log_some(
        self,
        table: Table,
        op: str,
        rows: Sequence[tuple[str, dict[str, Any] | None, dict[str, Any] | None]],
    ) -> None:
        values = []
        params: list[Any] = []
        for row_id, before, after in rows:
            self.version += 1
            if self.batch is None:
                self.batch = self.version
            values.append(sql.SQL("(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"))
            params += [
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
            ]
        self.tables.add(table.name)
        await self.conn.execute(
            sql.SQL(
                "INSERT INTO memory_changes (guild_id, campaign_id, version, batch, table_name,"
                " row_id, op, before, after, source, undoes, made_at) VALUES {}"
            ).format(sql.SQL(", ").join(values)),
            params,
        )

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

    Runs of changes to the same table and of the same kind are reversed in a few
    statements each (#164); a run that can't be done that way safely falls back to one
    row at a time, which gives the same result.
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
    start = 0
    while start < len(rows):
        end = start + 1
        kind = (rows[start]["table_name"], rows[start]["op"])
        while end < len(rows) and (rows[end]["table_name"], rows[end]["op"]) == kind:
            end += 1
        run = rows[start:end]
        if len(run) == 1 or not await _undo_run(changes, run):
            await _undo_rows(changes, run)
        start = end


async def _undo_rows(changes: Changes, run: Sequence[dict[str, Any]]) -> None:
    """One row at a time: the general way."""
    for change in run:
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


async def _undo_run(changes: Changes, run: Sequence[dict[str, Any]]) -> bool:
    """Reverse a run of one table's changes of one kind in a few statements. False (and
    nothing written) when it must go one row at a time instead."""
    table = BY_NAME[run[0]["table_name"]]
    op = run[0]["op"]
    keys = [change["row_id"] for change in run]
    if len(set(keys)) != len(keys):  # the same row twice: order matters, go row by row
        return False
    conn, ids = changes.conn, changes.ids
    where = sql.SQL(" AND {} = ANY(%s) FOR UPDATE").format(sql.Identifier(table.key))
    cur = await conn.execute(scoped_select(table, where), (*ids, keys))
    current = {r[table.key]: row_of(r, table) for r in await cur.fetchall()}
    for change in run:
        if current.get(change["row_id"]) != change["after"]:
            raise MemoryRuleError(CHANGED_SINCE)
    if op == "insert":  # undo: delete them, unless something else links to them
        for dep_table, column, is_list in DEPENDENTS.get(table.name, ()):
            test = "{} && %s" if is_list else "{} = ANY(%s)"
            query = sql.SQL(
                "SELECT 1 FROM {} WHERE guild_id = %s AND campaign_id = %s AND " + test
            ).format(sql.Identifier(dep_table), sql.Identifier(column))
            params: list[Any] = [*ids, keys]
            if dep_table == table.name:  # rows of this run linking to each other
                query += sql.SQL(" AND NOT {} = ANY(%s)").format(sql.Identifier(table.key))
                params.append(keys)
            cur = await conn.execute(query + sql.SQL(" LIMIT 1"), params)
            if await cur.fetchone() is not None:
                return False
        await conn.execute(
            sql.SQL(
                "DELETE FROM {} WHERE guild_id = %s AND campaign_id = %s AND {} = ANY(%s)"
            ).format(sql.Identifier(table.name), sql.Identifier(table.key)),
            (*ids, keys),
        )
        await changes._log_many(table, "delete", [(k, current[k], None) for k in keys])
    elif op == "update":  # undo: put every row back as it was
        others = [c for c in table.columns if c != table.key]
        await changes.update_rows(
            table,
            {change["row_id"]: {c: change["before"][c] for c in others} for change in run},
        )
    else:  # undo a delete: put the rows back
        cols = sql.SQL(", ").join(sql.Identifier(c) for c in table.columns)
        cur = await conn.execute(
            sql.SQL(
                "INSERT INTO {} (guild_id, campaign_id, {}) SELECT %s, %s, {}"
                " FROM jsonb_populate_recordset(NULL::{}, %s) RETURNING {}"
            ).format(sql.Identifier(table.name), cols, cols, sql.Identifier(table.name), cols),
            (*ids, Jsonb([change["before"] for change in run])),
        )
        back = {r[table.key]: row_of(r, table) for r in await cur.fetchall()}
        await changes._log_many(table, "insert", [(k, None, back[k]) for k in keys])
    return True
