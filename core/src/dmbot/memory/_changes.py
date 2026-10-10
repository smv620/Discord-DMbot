"""Row access for campaign memory, and the change log that makes every write undoable.

`Scope` reads one campaign's rows; `Changes` also writes them, logging every row with
its before and after values under one batch number. `undo_batch` reverses a batch.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, LiteralString

from psycopg import errors, sql
from psycopg.types.json import Jsonb

from dmbot.db import Conn
from dmbot.memory.models import MemoryRuleError, TooLateToUndo

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
        "role",
        "needs_look",
    ),
)
# A character's links to the rules (#1034): species, creature type, stat block, class,
# background.
RULE_LINKS = Table(
    "memory_rule_links",
    "id",
    ("id", "entity_id", "kind", "rules_source", "name", "edition", "known", "created_at"),
)
# What a column holds in change-log rows written before the column existed.
OLD_LOG_DEFAULTS: dict[str, dict[str, Any]] = {
    "memory_entities": {"role": None, "needs_look": False},
}
# A player character's sheet (#723): not in the undo log, but read and backed up like
# the rest of the campaign's memory.
SHEETS = Table(
    "character_sheets",
    "entity_id",
    ("entity_id", "url", "sheet", "source", "fetched_at", "player_id"),
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
ALL = (TYPES, PREDICATES, ENTITIES, ALIASES, MENTIONS, RELATIONS, CORRECTIONS, FLAGS, RULE_LINKS)
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
        ("memory_rule_links", "entity_id", False),
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

    # ---- many rows in one statement (#164, #253) -------------------------------------
    # Each row is still logged on its own, with its before and after values, exactly as
    # update/insert/delete log it, so undo and its tests see the same change log.

    async def insert_many(
        self, table: Table, rows: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Insert many rows in one statement, logged in their order as insert logs each
        (#253). Every row needs its own key. Returns the rows as written, in order."""
        if not rows:
            return []
        cols = sql.SQL(", ").join(sql.Identifier(c) for c in table.columns)
        cur = await self.conn.execute(
            sql.SQL(
                "INSERT INTO {} (guild_id, campaign_id, {}) SELECT %s, %s, {}"
                " FROM jsonb_populate_recordset(NULL::{}, %s) RETURNING {}"
            ).format(sql.Identifier(table.name), cols, cols, sql.Identifier(table.name), cols),
            (*self.ids, Jsonb([{c: row[c] for c in table.columns} for row in rows])),
        )
        written = {r[table.key]: row_of(r, table) for r in await cur.fetchall()}
        after = [written[row[table.key]] for row in rows]
        await self._log_many(table, "insert", [(r[table.key], None, r) for r in after])
        return after

    async def update_where(
        self, table: Table, changes: dict[str, Any], where: LiteralString, params: Sequence[Any]
    ) -> list[dict[str, Any]]:
        """Set the same values on every matching row that differs, in two statements.
        `where` reads like Scope.select's (" AND entity_id = %s"). Returns the rows
        after, in key order.

        First the keys, with the filter's own index; then update_rows by key. One
        UPDATE … FROM a filtered subquery picked a quadratic plan whenever the table's
        statistics were missing or stale (a campaign added since the last ANALYZE):
        seconds for a few thousand rows (#342 review)."""
        if not changes:
            return []
        self._check_columns(table, changes)
        differs = sql.SQL(" OR ").join(
            sql.SQL("{} IS DISTINCT FROM %s").format(sql.Identifier(c)) for c in changes
        )
        query = sql.SQL(
            "SELECT {} FROM {} WHERE guild_id = %s AND campaign_id = %s{} AND ({}) FOR UPDATE"
        ).format(sql.Identifier(table.key), sql.Identifier(table.name), sql.SQL(where), differs)
        cur = await self.conn.execute(query, (*self.ids, *params, *changes.values()))
        keys = [r[table.key] for r in await cur.fetchall()]
        return await self.update_rows(table, {key: dict(changes) for key in keys})

    async def update_rows(
        self, table: Table, rows: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Give each row its own new values, in one statement. `rows`: row id → the
        columns to set (the same columns for every row). Rows already holding those
        values are left alone and not logged. Returns the rows after, in key order."""
        if not rows:
            return []
        columns = sorted({c for changes in rows.values() for c in changes})
        if not columns:
            return []
        for changes in rows.values():
            self._check_columns(table, changes)
            if sorted(changes) != columns:
                raise ValueError("update_rows needs the same columns for every row")
        new = [{table.key: row_id, **changes} for row_id, changes in rows.items()]
        sets = sql.SQL(", ").join(sql.SQL("{0} = n.{0}").format(sql.Identifier(c)) for c in columns)
        joined = sql.SQL("{} o JOIN jsonb_populate_recordset(NULL::{}, %s) n USING ({})").format(
            sql.Identifier(table.name), sql.Identifier(table.name), sql.Identifier(table.key)
        )
        where = sql.SQL("o.guild_id = %s AND o.campaign_id = %s AND ({})").format(
            sql.SQL(" OR ").join(
                sql.SQL("o.{0} IS DISTINCT FROM n.{0}").format(sql.Identifier(c)) for c in columns
            )
        )
        # One UPDATE, the table joined to itself on its key so the plan always uses the
        # key's index, even before Postgres has statistics for the table (#164 review).
        # It returns each row before (o, read at the statement's start) and after (t);
        # the campaign row is locked for the whole write, so nothing else changes them.
        query = sql.SQL(
            "UPDATE {} t SET {} FROM {} WHERE {} AND t.guild_id = o.guild_id"
            " AND t.campaign_id = o.campaign_id AND t.{} = o.{} RETURNING {}"
        ).format(
            sql.Identifier(table.name),
            sets,
            joined,
            where,
            sql.Identifier(table.key),
            sql.Identifier(table.key),
            sql.SQL(", ").join(
                [
                    sql.SQL("o.{0} AS {1}").format(sql.Identifier(c), sql.Identifier("b_" + c))
                    for c in table.columns
                ]
                + [
                    sql.SQL("t.{0} AS {1}").format(sql.Identifier(c), sql.Identifier("a_" + c))
                    for c in table.columns
                ]
            ),
        )
        cur = await self.conn.execute(query, (Jsonb(new), *self.ids))
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
        """Log many row changes, each with its own version, in one statement. The rows go
        as one JSON value, so the statement is the same however many there are (no limit
        on values, one prepared statement)."""
        if not rows:
            return
        entries = []
        for row_id, before, after in rows:
            self.version += 1
            if self.batch is None:
                self.batch = self.version
            entries.append(
                {
                    "version": self.version,
                    "batch": self.batch,
                    "table_name": table.name,
                    "row_id": row_id,
                    "op": op,
                    "before": before,
                    "after": after,
                    "source": self.source,
                    "undoes": self.undoes,
                    "made_at": self.now,
                }
            )
        self.tables.add(table.name)
        await self.conn.execute(
            "INSERT INTO memory_changes (guild_id, campaign_id, version, batch, table_name,"
            " row_id, op, before, after, source, undoes, made_at)"
            " SELECT %s, %s, version, batch, table_name, row_id, op, before, after, source,"
            " undoes, made_at FROM jsonb_populate_recordset(NULL::memory_changes, %s)",
            (*self.ids, Jsonb(entries)),
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


def _filled(table: Table, snapshot: Any) -> Any:
    """A change-log snapshot with the columns added since it was written (#1034) filled in
    with what they held then."""
    defaults = OLD_LOG_DEFAULTS.get(table.name)
    if not defaults or not isinstance(snapshot, dict):
        return snapshot
    return {**defaults, **snapshot}


def _unchanged_since(table: Table, current: dict[str, Any] | None, after: Any) -> bool:
    """The row is as the batch left it. A flag the after-session cleanup closed since
    (#348) counts as unchanged: undoing an insert deletes it, undoing an update restores
    it as it was (the next cleanup closes it again if the clash is still gone)."""
    after = _filled(table, after)
    if current == after:
        return True
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
        raise TooLateToUndo(NOT_FOUND)  # pruned (#164); the store says how long Undo works
    start = 0
    while start < len(rows):
        end = start + 1
        kind = (rows[start]["table_name"], rows[start]["op"])
        while end < len(rows) and (rows[end]["table_name"], rows[end]["op"]) == kind:
            end += 1
        run = rows[start:end]
        if len(run) == 1 or not await _undo_run_or_not(changes, run):
            await _undo_rows(changes, run)
        start = end


async def _undo_rows(changes: Changes, run: Sequence[dict[str, Any]]) -> None:
    """One row at a time: the general way.

    A run several rows long may hold a chain of unique values (a name leaving E as
    another takes its words to E). Rows written in one statement are logged in key
    order, not in the order such a chain needs, so a row that trips a unique rule waits
    for the others and is tried again (#342 review). A row with nothing left to wait
    for has really been blocked since: CHANGED_SINCE."""
    for change in run:
        table = BY_NAME[change["table_name"]]
        current = await changes.get(table, change["row_id"])
        if not _unchanged_since(table, current, change["after"]):
            raise MemoryRuleError(CHANGED_SINCE)
    if len(run) == 1:
        await _undo_row(changes, run[0])
        return
    pending = list(run)
    while pending:
        waiting = []
        for change in pending:
            try:
                async with changes.conn.transaction():  # a savepoint
                    await _undo_row(changes, change)
            except errors.UniqueViolation:
                waiting.append(change)
        if len(waiting) == len(pending):
            raise MemoryRuleError(CHANGED_SINCE)
        pending = waiting


async def _undo_row(changes: Changes, change: dict[str, Any]) -> None:
    table = BY_NAME[change["table_name"]]
    if change["op"] == "insert":
        await changes.delete(table, change["row_id"])
    elif change["op"] == "update":
        await changes.update(table, change["row_id"], _filled(table, change["before"]))
    else:
        await changes.insert(table, _filled(table, change["before"]))


async def _undo_run_or_not(changes: Changes, run: Sequence[dict[str, Any]]) -> bool:
    """_undo_run, but False (nothing written) if one statement trips over a unique
    rule that one row at a time wouldn't: a chain like A takes B's name as B gives it
    up. Nothing is logged before the statement that fails, so going row by row after
    is safe."""
    try:
        async with changes.conn.transaction():  # a savepoint
            return await _undo_run(changes, run)
    except errors.UniqueViolation:
        return False


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
        if not _unchanged_since(table, current.get(change["row_id"]), change["after"]):
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
            {
                change["row_id"]: {c: _filled(table, change["before"])[c] for c in others}
                for change in run
            },
        )
    else:  # undo a delete: put the rows back
        cols = sql.SQL(", ").join(sql.Identifier(c) for c in table.columns)
        cur = await conn.execute(
            sql.SQL(
                "INSERT INTO {} (guild_id, campaign_id, {}) SELECT %s, %s, {}"
                " FROM jsonb_populate_recordset(NULL::{}, %s) RETURNING {}"
            ).format(sql.Identifier(table.name), cols, cols, sql.Identifier(table.name), cols),
            (*ids, Jsonb([_filled(table, change["before"]) for change in run])),
        )
        back = {r[table.key]: row_of(r, table) for r in await cur.fetchall()}
        await changes._log_many(table, "insert", [(k, None, back[k]) for k in keys])
    return True
