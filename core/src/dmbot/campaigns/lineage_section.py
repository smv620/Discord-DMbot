"""The build stamps of tables whose backup sections do not carry them (#3751, #3739).

A table's `made_by_commit` says which build wrote each row (see `dmbot.lineage`). The memory section
and the transcript parts carry it with their rows; every other section was written before the stamp
and its strict row checks do not know it. Rather than add a key to each of them, this one section
carries the stamps of all the rest, keyed by the row's primary key, and puts them back after every
other section has loaded.

Which tables and which columns are decided here and by the database, never by the file: the tables
are the stamped tables that some section restores (`campaigns/tables.py`), minus the ones the memory
section carries itself; the key columns are the table's primary key read from the catalog, without
`guild_id` and `campaign_id`; the stamp columns are `STAMP_COLUMNS`. A row the restore did not
recreate under that key matches nothing and is skipped, so the section can never add a row. The
file is untrusted: a table that is not on the list, a key of the wrong shape or a stamp that is not
40 lower-case hex characters refuses the whole backup. A restored stamp is as trusted as the file
(`dmbot.lineage`). A backup from before this section simply has none, and restores with none.
"""

from __future__ import annotations

from typing import Any

from psycopg import errors as pg_errors
from psycopg import sql

from dmbot import lineage
from dmbot.campaigns.models import CampaignError
from dmbot.campaigns.tables import SECTION_TABLES
from dmbot.db import Conn

DAMAGED = "This backup file is damaged (bad build records)."
# The columns that hold a stamp: the row's own, and the voiceover's (`recap_jobs`, 0380).
STAMP_COLUMNS = ("made_by_commit", "audio_made_by_commit")
MAX_ROWS = 500_000
KEY_TEXT_MAX = 200
KEY_PARTS_MAX = 8
_SCOPE = ("guild_id", "campaign_id")


def carried_tables() -> frozenset[str]:
    """The stamped tables this section carries: those some section restores, except the memory
    section's own tables (it carries `made_by` itself)."""
    from dmbot.memory.backup import _TAGS  # (late: the memory module is large)

    native = {table.name for table in _TAGS.values()}
    restored = {table for tables in SECTION_TABLES.values() for table in tables}
    return frozenset((restored & lineage.STAMPED) - native)


class LineageSection:
    """The stamps in campaign backups (an `ExportSection`)."""

    name = "lineage"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        out: list[Any] = []
        for table in sorted(carried_tables()):
            shape = await _shape(conn, table)
            if shape is None:
                continue
            keys, stamps = shape
            columns = sql.SQL(", ").join(sql.Identifier(c) for c in (*keys, *stamps))
            present = sql.SQL(" OR ").join(
                sql.SQL("{} IS NOT NULL").format(sql.Identifier(c)) for c in stamps
            )
            order = (
                sql.SQL(" ORDER BY {}").format(sql.SQL(", ").join(sql.Identifier(c) for c in keys))
                if keys
                else sql.SQL("")
            )
            cur = await conn.execute(
                sql.SQL(
                    "SELECT {} FROM {} WHERE guild_id = %s AND campaign_id = %s AND ({}){}"
                ).format(columns, sql.Identifier(table), present, order),
                (guild_id, campaign_id),
            )
            for row in await cur.fetchall():
                key = [row[c] for c in keys]
                if any(type(v) not in (int, str) for v in key):
                    continue  # (a key this section cannot carry: the row restores with none)
                out.append(
                    {
                        "table": table,
                        "key": key,
                        "stamps": {c: row[c] for c in stamps if row[c] is not None},
                    }
                )
        return out

    def check(self, rows: list[Any]) -> list[dict[str, Any]]:
        """Every row checked: pure CPU, run before the transaction opens."""
        if not isinstance(rows, list) or len(rows) > MAX_ROWS:
            raise CampaignError(DAMAGED)
        allowed = carried_tables()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"table", "key", "stamps"}:
                raise CampaignError(DAMAGED)
            key, stamps = row["key"], row["stamps"]
            if (
                row["table"] not in allowed
                or not isinstance(key, list)
                or len(key) > KEY_PARTS_MAX
                or not all(_key_part(v) for v in key)
                or not isinstance(stamps, dict)
                or not stamps
                or not set(stamps) <= set(STAMP_COLUMNS)
                or not all(isinstance(v, str) and lineage.valid(v) for v in stamps.values())
            ):
                raise CampaignError(DAMAGED)
        return rows

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        checked = self.check(rows or [])
        shapes: dict[str, tuple[list[str], list[str]] | None] = {}
        try:
            for row in checked:
                table = row["table"]
                if table not in shapes:
                    shapes[table] = await _shape(conn, table)
                shape = shapes[table]
                if shape is None or len(row["key"]) != len(shape[0]):
                    continue  # (the table's key is not the one the file was made with)
                keys, columns = shape
                stamps = {c: v for c, v in row["stamps"].items() if c in columns}
                if not stamps:
                    continue
                sets = sql.SQL(", ").join(
                    sql.SQL("{} = %s").format(sql.Identifier(c)) for c in stamps
                )
                where = sql.SQL("").join(
                    sql.SQL(" AND {} = %s").format(sql.Identifier(c)) for c in keys
                )
                await conn.execute(
                    sql.SQL("UPDATE {} SET {} WHERE guild_id = %s AND campaign_id = %s{}").format(
                        sql.Identifier(table), sets, where
                    ),
                    (*stamps.values(), guild_id, campaign_id, *row["key"]),
                )
        except (pg_errors.DataError, pg_errors.IntegrityError) as exc:
            raise CampaignError(DAMAGED) from exc

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        """Nothing of its own: the stamps live on the other sections' rows, which they clear."""


def _key_part(value: object) -> bool:
    return (isinstance(value, str) and len(value) <= KEY_TEXT_MAX) or (
        type(value) is int and -(2**63) <= value < 2**63
    )


async def _shape(conn: Conn, table: str) -> tuple[list[str], list[str]] | None:
    """(primary key columns without guild and campaign, stamp columns) of a table, from the
    catalog; None when the table does not hang off a campaign or has no primary key."""
    cur = await conn.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = current_schema() AND table_name = %s",
        (table,),
    )
    have = {str(r["column_name"]) for r in await cur.fetchall()}
    if not set(_SCOPE) <= have:
        return None
    stamps = [c for c in STAMP_COLUMNS if c in have]
    if not stamps:
        return None
    cur = await conn.execute(
        "SELECT a.attname AS name FROM pg_index i"
        " JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)"
        " WHERE i.indrelid = (quote_ident(current_schema()) || '.' || quote_ident(%s))::regclass"
        " AND i.indisprimary ORDER BY array_position(i.indkey::int2[], a.attnum)",
        (table,),
    )
    pk = [str(r["name"]) for r in await cur.fetchall()]
    if not pk:
        return None
    return [c for c in pk if c not in _SCOPE], stamps
