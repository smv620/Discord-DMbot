"""Row access for campaign memory, and the change log that makes every write undoable.

`Scope` reads one campaign's rows; `Changes` also writes them, logging every row with
its before and after values under one batch number. `undo_batch` reverses a batch.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, LiteralString

from psycopg import errors, sql
from psycopg.types.json import Jsonb

from dmbot.db import Conn
from dmbot.memory import merge_restore
from dmbot.memory.models import MemoryRuleError, TooLateToUndo
from dmbot.memory.ontology import MIGRATED_KINDS

NOT_FOUND = "DMbot doesn't remember that any more."
CHANGED_SINCE = "That was changed again since, so it can't be undone on its own."


@dataclass(frozen=True, slots=True)
class Table:
    name: str
    key: str  # the column identifying a row within a campaign
    columns: tuple[str, ...]  # excluding guild_id and campaign_id
    # False: kept out of the change log, so Undo can't reach it (the extractor's claims are
    # answered once; a fact written from one is undone as a fact, not by flipping the claim).
    logged: bool = True
    # A column added later: a row built without it gets this (code that predates the column
    # keeps working, and no other column is ever filled in silently).
    defaults: Mapping[str, Any] = field(default_factory=dict, hash=False, compare=False)


def _cell(table: Table, row: Mapping[str, Any], column: str) -> Any:
    """A row's value for a column, or the column's default when it was added after the code that
    built the row."""
    if column in row or column not in table.defaults:
        return row[column]
    return table.defaults[column]


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
     "replaced_by", "created_at", "covers", "not_this", "inverse_label", "synonyms",
     "version", "from_set"),
    defaults={"from_set": None},  # the set a relationship was borrowed from (#1456), if any
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
        "unique_item",
        "audience_limited",
        "party",
        "hidden_from_players",
        "made_before_name_gate",
        "manipulated_by",
    ),
    # a clue's switch (#1649), the party, "players don't know this one yet" (#2321); a row written
    # now is after the pronoun gate (#2358)
    defaults={
        "audience_limited": False,
        "party": False,
        "hidden_from_players": False,
        "made_before_name_gate": False,
        "manipulated_by": None,  # the evidence this one faked (#2409)
    },
)
# What a being or thing is now, with the history of what it was (#1267): one current row for
# each entry, a new condition closes it.
STATES = Table(
    "memory_states",
    "id",
    ("id", "entity_id", "condition", "from_session_at", "source", "created_at", "closed_at"),
)
# What the story extractor read out of a session, waiting for the DM (#1297): text only, no
# links to entries, because a claim names things the memory may not know yet. `fact_id` is not a
# database link: the fact it became lives in one of two tables (relationships or states) and can
# be undone or deleted later, so it is a pointer to follow while the fact is still there.
CLAIMS = Table(
    "memory_claims",
    "id",
    ("id", "subject", "relationship", "object", "details", "how", "lines", "speaker_ids",
     "source", "prompt_version", "model", "status", "reason", "session_started_at",
     "created_at", "decided_at", "fact_id"),
    logged=False,
)  # fmt: skip
# A character's links to the rules (#1034): species, creature type, stat block, class,
# background.
RULE_LINKS = Table(
    "memory_rule_links",
    "id",
    ("id", "entity_id", "kind", "rules_source", "name", "edition", "known", "created_at"),
)
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
     "source", "created_at", "said_as"),
    defaults={"said_as": None},
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
     "secret", "created_at", "definition_version", "unrevealed"),
    defaults={"unrevealed": False},  # held back from the players until said at the table (#2404)
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
# How a viewer feels about a subject, and how a group or place feels about a whole people (#1655):
# a new one for a pair closes the old, so the history stays.
IMPRESSIONS = Table(
    "memory_impressions",
    "id",
    ("id", "viewer_id", "subject_id", "score", "weight", "source", "from_session_at",
     "created_at", "closed_at"),
)  # fmt: skip
ATTITUDES = Table(
    "memory_attitudes",
    "id",
    ("id", "holder_id", "toward_id", "score", "weight", "source", "from_session_at",
     "created_at", "closed_at"),
)  # fmt: skip
# Who holds beliefs (The truth, The party, a character) and what each believes of a connection
# (#2409). A belief is never edited: a new row is the new belief, the highest `seq` wins.
# Perspectives are unlogged: The truth and The party are made with the campaign, not by a DM's
# change, so an Undo can never take them away from the beliefs resting on them.
PERSPECTIVES = Table(
    "memory_perspectives", "id", ("id", "kind", "entity_id", "created_at"), logged=False
)
BELIEFS = Table(
    "memory_beliefs",
    "id",
    ("id", "perspective_id", "relation_id", "confidence", "status", "session_at",
     "evidence_ids", "seq", "recorded_at", "pinned"),
    defaults={"pinned": False},  # the DM's 📌 100% truth (#2447)
)  # fmt: skip
# The DM's word that a book entry and a table entry are one, or are not (#1715).
PLAN_SAME_AS = Table(
    "plan_same_as",
    "id",
    ("id", "plan_id", "document_id", "entity_id", "status", "decided_by", "created_at"),
)
# The chapter the DM says the table is in, for each adventure file (#1731).
PLAN_POSITION = Table(
    "plan_position", "id", ("id", "document_id", "chapter_id", "set_by", "created_at")
)
ALL = (TYPES, PREDICATES, ENTITIES, ALIASES, MENTIONS, RELATIONS, CORRECTIONS, FLAGS, RULE_LINKS,
       STATES, CLAIMS, IMPRESSIONS, ATTITUDES, PLAN_SAME_AS, PLAN_POSITION, PERSPECTIVES,
       BELIEFS)  # fmt: skip
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
        ("memory_states", "entity_id", False),
        ("memory_impressions", "viewer_id", False),
        ("memory_impressions", "subject_id", False),
        ("memory_attitudes", "holder_id", False),
        ("memory_attitudes", "toward_id", False),
        ("plan_same_as", "entity_id", False),
        ("memory_perspectives", "entity_id", False),
        ("memory_entities", "manipulated_by", False),
        # (`plan_divergences` is not listed on purpose: it is derived from the plan and the table
        # and unlogged, so it simply goes with the entry and is worked out again when needed.)
    ),
    "memory_mentions": (("memory_relations", "mention_ids", True),),
    "memory_relations": (
        ("memory_flags", "relation_id", False),
        ("memory_flags", "other_id", False),
        ("memory_beliefs", "relation_id", False),
    ),
    "memory_perspectives": (("memory_beliefs", "perspective_id", False),),
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
        self.perspectives: dict[str, str] | None = (
            None  # The truth's and The party's ids, once known
        )

    async def insert(self, table: Table, row: dict[str, Any]) -> dict[str, Any]:
        cols = ("guild_id", "campaign_id", *table.columns)
        query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING {}").format(
            sql.Identifier(table.name),
            sql.SQL(", ").join(sql.Identifier(c) for c in cols),
            sql.SQL(", ").join(sql.Placeholder() * len(cols)),
            sql.SQL(", ").join(sql.Identifier(c) for c in table.columns),
        )
        cur = await self.conn.execute(
            query, (*self.ids, *(_cell(table, row, c) for c in table.columns))
        )
        raw = await cur.fetchone()
        assert raw is not None
        after = row_of(raw, table)
        await self._log(table, after[table.key], "insert", None, after)
        if table is RELATIONS and self.undoes is None:
            await self._beliefs_follow(after)
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
        if "status" in changes:
            await self._forget_later(table, [after])
        if (
            table is RELATIONS
            and self.undoes is None
            and ("status" in changes or "secret" in changes or "unrevealed" in changes)
        ):
            await self._beliefs_follow(after)
        return after

    async def _beliefs_follow(self, relation: dict[str, Any]) -> None:
        """A connection was written: keep The truth's and The party's belief in step with it, in the
        same change (#2409). Imported here: the module needs the tables above."""
        from dmbot.memory import beliefs

        await beliefs.follow(self, relation)

    async def delete(self, table: Table, row_id: str) -> None:
        """Delete one row. Refused while anything still links to it (see DEPENDENTS)."""
        before = await self.get(table, row_id)
        if before is None:
            return
        if table is RELATIONS and self.undoes is None:
            # its beliefs go with it, each logged, so an Undo brings the connection back whole
            for belief in await self.select(BELIEFS, " AND relation_id = %s", [row_id]):
                await self.delete(BELIEFS, belief["id"])
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
            (*self.ids, Jsonb([{c: _cell(table, row, c) for c in table.columns} for row in rows])),
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
        if "status" in columns:
            await self._forget_later(table, [after for _, after in logged])
        return [after for _, after in logged]

    async def _forget_later(self, table: Table, rows: list[dict[str, Any]]) -> None:
        """A name decided on is no longer left "for later" (#2174): if it is ever undone back to
        a suggestion it asks again, rather than staying quiet for an answer nobody gave. Every
        path that changes a status comes through here (one change, many, and undo)."""
        ids = [
            r[table.key]
            for r in rows
            if table.name == "memory_entities" and r["status"] != "proposed"
        ]
        if ids:
            await self.conn.execute(
                "DELETE FROM name_later"
                " WHERE guild_id = %s AND campaign_id = %s AND entity_id = ANY(%s)",
                (*self.ids, ids),
            )

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
        if not rows or not table.logged:
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
        if not table.logged:
            return
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


# Columns added after change-log rows were written, with the value an older snapshot reads as
# (#1189): a fact written before definitions were versioned was written under version 1.
_ADDED_LATER: dict[str, dict[str, Any]] = {
    "memory_relations": {"definition_version": 1, "unrevealed": False},
    # unique_item: nobody said, the name decides (#1267). audience_limited: off (#1649).
    # made_before_name_gate: a snapshot from before the column is a row from before it (#2358)
    "memory_entities": {
        "unique_item": None,
        "audience_limited": False,
        "party": False,
        "hidden_from_players": False,
        "made_before_name_gate": True,
        "manipulated_by": None,  # nothing was faked before beliefs (#2409)
    },
    "memory_beliefs": {"pinned": False},  # nothing was pinned before the pin (#2447)
    "memory_predicates": {
        "covers": [], "not_this": [], "inverse_label": "", "synonyms": [], "version": 1,
        "from_set": None,
    },
}  # fmt: skip


def _filled(table: Table, snapshot: Any) -> Any:
    """A change-log snapshot as it would read today: an entry written before roles existed is
    read the way the migration moved it (npc becomes a character with the role npc, and so
    on, #1034), and a row written before a column existed gets that column's starting value
    (#1189), so Undo across a migration puts back what the row is now."""
    if not isinstance(snapshot, dict):
        return snapshot
    if table.name in _ADDED_LATER:
        snapshot = {**_ADDED_LATER[table.name], **snapshot}
    if table.name != "memory_entities" or "role" in snapshot:
        return snapshot
    kind, role, look = MIGRATED_KINDS.get(
        str(snapshot.get("type")), (snapshot.get("type"), None, False)
    )
    return {**snapshot, "type": kind, "role": role, "needs_look": look}


def _only(snapshot: dict[str, Any], columns: Sequence[str]) -> dict[str, Any]:
    return {c: snapshot[c] for c in columns}


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
    # a merge's notes and sheet live outside the log: they come back with the name (#2438)
    await merge_restore.restore(conn, ids, batch, changes.batch)
    await merge_restore.redo(conn, ids, batch, changes.batch)


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
            {change["row_id"]: _only(_filled(table, change["before"]), others) for change in run},
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
