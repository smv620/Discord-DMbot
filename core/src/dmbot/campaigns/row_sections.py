"""Backup sections for campaign tables that need nothing special (#2552).

A table here is carried row for row: dumped with `to_jsonb`, loaded with `jsonb_populate_record`,
so the database's own column types and CHECKs validate a file that anyone could have edited, and
its foreign keys refuse a row that points at nothing. `guild_id` and `campaign_id` are never in the
file; they are the restore's. Ids that are only unique within a campaign (entities, plan pieces)
keep their value, so the rows that point at them stay true. A table whose rows hang off ids that
change on restore (an adventure's documents, a transcript's sessions) is not for this class.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import errors as pg_errors
from psycopg import sql
from psycopg.types.json import Jsonb

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn

DAMAGED = "This backup file is damaged (bad {what})."
MAX_ROWS = 50_000  # per table: far above any real campaign, and a bound on a forged file
JS_SAFE = 2**53  # a bigger whole number is written as text, as the other sections write ids


@dataclass(frozen=True, slots=True)
class RowTable:
    name: str
    # Columns the database makes again on restore (an identity id nothing else points at).
    regenerate: tuple[str, ...] = ()
    order_by: str = "1"


def _safe(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and abs(value) >= JS_SAFE:
        return str(value)
    if isinstance(value, list):
        return [_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    return value


class RowsSection:
    """Several tables as one backup section (an `ExportSection`). Tables load in the order given,
    so a table that points at another comes after it."""

    def __init__(self, name: str, tables: tuple[RowTable, ...]) -> None:
        self.name = name
        self.tables = tables
        self._by_name = {t.name: t for t in tables}

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        out: list[Any] = []
        for table in self.tables:
            drop = sql.SQL("").join(
                sql.SQL(" - {}").format(sql.Literal(c))
                for c in ("guild_id", "campaign_id", *table.regenerate)
            )
            query = sql.SQL(
                "SELECT (to_jsonb(t){drop}) AS r FROM {table} t"
                " WHERE guild_id = %s AND campaign_id = %s ORDER BY {order}"
            ).format(
                drop=drop,
                table=sql.Identifier(table.name),
                order=sql.SQL(table.order_by),
            )
            cur = await conn.execute(query, (guild_id, campaign_id))
            out += [{"table": table.name, **_safe(r["r"])} for r in await cur.fetchall()]
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        grouped = self._grouped(rows)
        try:
            for table in self.tables:
                batch = grouped[table.name]
                if batch:
                    await self._insert(conn, table, guild_id, campaign_id, batch)
            await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")  # a broken link: a plain message
        except (pg_errors.IntegrityError, pg_errors.DataError) as exc:
            raise CampaignError(DAMAGED.format(what=self.name)) from exc

    def _grouped(self, rows: Any) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {t.name: [] for t in self.tables}
        if not isinstance(rows, list):
            raise CampaignError(DAMAGED.format(what=self.name))
        for raw in rows:
            if not isinstance(raw, dict) or raw.get("table") not in grouped:
                raise CampaignError(DAMAGED.format(what=self.name))
            row = {k: v for k, v in raw.items() if k != "table"}
            if {"guild_id", "campaign_id"} & set(row):  # the restore's, never the file's
                raise CampaignError(DAMAGED.format(what=self.name))
            grouped[raw["table"]].append(row)
        if any(len(batch) > MAX_ROWS for batch in grouped.values()):
            raise CampaignError(DAMAGED.format(what=self.name))
        return grouped

    async def _insert(
        self,
        conn: Conn,
        table: RowTable,
        guild_id: int,
        campaign_id: str,
        batch: list[dict[str, Any]],
    ) -> None:
        cur = await conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = current_schema() AND table_name = %s ORDER BY ordinal_position",
            (table.name,),
        )
        columns = [str(r["column_name"]) for r in await cur.fetchall()]
        known = set(columns)
        for row in batch:  # a column the table does not have is a file from somewhere else
            if not set(row) <= known - {"guild_id", "campaign_id"}:
                raise CampaignError(DAMAGED.format(what=self.name))
        into = [c for c in columns if c not in table.regenerate]
        query = sql.SQL(
            "INSERT INTO {table} ({cols}) SELECT {picked}"
            " FROM jsonb_array_elements(%s::jsonb) AS e,"
            " LATERAL jsonb_populate_record(NULL::{table}, e.value || %s::jsonb) AS r"
        ).format(
            table=sql.Identifier(table.name),
            cols=sql.SQL(", ").join(sql.Identifier(c) for c in into),
            picked=sql.SQL(", ").join(sql.SQL("r.{}").format(sql.Identifier(c)) for c in into),
        )
        owner = {"guild_id": guild_id, "campaign_id": campaign_id}
        await conn.execute(query, (Jsonb(batch), Jsonb(owner)))

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        for table in reversed(self.tables):
            await conn.execute(
                sql.SQL("DELETE FROM {} WHERE guild_id = %s AND campaign_id = %s").format(
                    sql.Identifier(table.name)
                ),
                (guild_id, campaign_id),
            )


# Part 2a (#2552): what the DM and the story keep about names. The ids they point at are the
# memory section's, which keep their value on restore, so this section loads after it.
STORY_NOTES = RowsSection(
    "story_notes",
    (
        RowTable("npc_notes", order_by="entry_id, user_id"),
        RowTable("npc_roster", order_by="entry_id"),
        RowTable("backstory_links", order_by="created_at, subject_id, predicate, object_id"),
        RowTable("name_later", order_by="entity_id"),
        RowTable("name_not_same", order_by="a_id, b_id"),
        RowTable("said_as_suggestions", order_by="entity_id, heard"),
        RowTable("recap_name_checks", order_by="session_at, entity_id"),
        RowTable("secret_exposures", regenerate=("id",), order_by="id"),
        RowTable("house_rules_file", order_by="link"),
    ),
)
