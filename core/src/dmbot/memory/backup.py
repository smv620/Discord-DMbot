"""Campaign memory in campaign backups (an `ExportSection`).

Left out: the change log (a restored campaign starts a fresh undo history) and mentions
("this word in this line meant Belleros"), which are by far the largest part and are
rebuilt as new sessions are transcribed. Each row carries a `table` tag; rows from the
file are untrusted and fully checked before anything is written.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from psycopg import errors as pg_errors
from psycopg import sql

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn
from dmbot.memory import notify
from dmbot.memory._changes import (
    ALIASES,
    CORRECTIONS,
    ENTITIES,
    FLAGS,
    PREDICATES,
    RELATIONS,
    TYPES,
    Table,
    row_of,
    scoped_select,
)
from dmbot.memory.checks import FLAG_KINDS
from dmbot.memory.models import (
    ALIAS_KINDS,
    DESCRIPTION_MAX,
    DETAIL_MAX,
    ENTITY_STATUSES,
    FIX,
    KEEP,
    LINE_REF_MAX,
    LIST_MAX,
    NAME_MAX,
    SOURCES,
    is_id,
)
from dmbot.memory.ontology import (
    ACTIVE,
    CORE_PREDICATES,
    CORE_TYPES,
    DEPRECATED,
    LABEL_MAX,
    Ontology,
    PredicateTerm,
    TypeTerm,
)

DAMAGED = "This backup file is damaged (bad campaign memory entry)."
INT64_MAX = 2**63 - 1

# Tag in the file → table, in load order (anything a row links to comes first).
_TAGS: dict[str, Table] = {
    "type": TYPES,
    "predicate": PREDICATES,
    "entity": ENTITIES,
    "alias": ALIASES,
    "relation": RELATIONS,
    "correction": CORRECTIONS,
    "flag": FLAGS,
}

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
    "action": (FIX, KEEP),
    "source": SOURCES,
}
_IDS = {"id", "entity_id", "merged_into", "used_by", "subject_id", "object_id", "relation_id",
        "other_id"}  # fmt: skip
_INTS = {"created_at", "from_session_at", "to_session_at", "from_game_time", "to_game_time",
         "max_per_subject"}  # fmt: skip
_LISTS = {"examples", "subject_types", "object_types", "conflicts_with", "sound_codes",
          "mention_ids"}  # fmt: skip
_NULLABLE = {"merged_into", "used_by", "other_id", "entity_id", "replaced_by", "parent",
             "from_session_at", "to_session_at", "from_game_time", "to_game_time",
             "max_per_subject"}  # fmt: skip


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
            and len(value) <= LIST_MAX
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


async def bump_version(conn: Conn, guild_id: int, campaign_id: str) -> None:
    """Tell copies held in memory that this campaign's memory changed wholesale."""
    cur = await conn.execute(
        "UPDATE campaigns SET memory_version = memory_version + 1"
        " WHERE guild_id = %s AND id = %s RETURNING memory_version",
        (guild_id, campaign_id),
    )
    row = await cur.fetchone()
    if row is not None:
        await notify.send(conn, campaign_id, int(row["memory_version"]), names_changed=True)


class MemorySection:
    name = "memory"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        out: list[Any] = []
        for tag, table in _TAGS.items():
            order = sql.SQL(" ORDER BY {}").format(sql.Identifier(table.key))
            cur = await conn.execute(scoped_select(table, order), (guild_id, campaign_id))
            for raw in await cur.fetchall():
                row = row_of(raw, table)
                if table is RELATIONS:
                    row["mention_ids"] = []  # mentions aren't backed up
                out.append({"table": tag, **row})
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: list[Any]) -> None:
        by_tag: dict[str, list[dict[str, Any]]] = {tag: [] for tag in _TAGS}
        for raw in rows:
            if not isinstance(raw, dict) or raw.get("table") not in _TAGS:
                raise CampaignError(DAMAGED)
            table = _TAGS[raw["table"]]
            if set(raw) != {"table", *table.columns}:
                raise CampaignError(DAMAGED)
            if not all(_valid(c, raw[c]) for c in table.columns):
                raise CampaignError(DAMAGED)
            if table is RELATIONS and raw["mention_ids"]:
                raise CampaignError(DAMAGED)
            by_tag[raw["table"]].append(raw)
        _check_terms(by_tag)
        try:
            for tag, table in _TAGS.items():
                if not by_tag[tag]:
                    continue
                cols = ("guild_id", "campaign_id", *table.columns)
                query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier(table.name),
                    sql.SQL(", ").join(sql.Identifier(c) for c in cols),
                    sql.SQL(", ").join(sql.Placeholder() * len(cols)),
                )
                async with conn.cursor() as cur:
                    await cur.executemany(
                        query,
                        [
                            (guild_id, campaign_id, *(r[c] for c in table.columns))
                            for r in by_tag[tag]
                        ],
                    )
            # Check links now, not at commit, so a broken file gives a plain message.
            await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        except (pg_errors.IntegrityError, pg_errors.DataError) as exc:
            raise CampaignError(DAMAGED) from exc
        await bump_version(conn, guild_id, campaign_id)

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        names = ("memory_changes", "memory_mentions", *(t.name for t in reversed(_TAGS.values())))
        for name in names:
            await conn.execute(
                sql.SQL("DELETE FROM {} WHERE guild_id = %s AND campaign_id = %s").format(
                    sql.Identifier(name)
                ),
                (guild_id, campaign_id),
            )
        await bump_version(conn, guild_id, campaign_id)


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


def _check_terms(by_tag: dict[str, list[dict[str, Any]]]) -> None:
    """Entities and facts in a backup must use known kinds; extensions can't replace
    the core."""
    core = {t.key for t in CORE_TYPES} | {p.key for p in CORE_PREDICATES}
    if any(r["key"] in core for r in (*by_tag["type"], *by_tag["predicate"])):
        raise CampaignError(DAMAGED)
    onto = Ontology.build(
        (_type_term(r) for r in by_tag["type"]),
        (_predicate_term(r) for r in by_tag["predicate"]),
    )
    if any(r["type"] not in onto.types for r in by_tag["entity"]):
        raise CampaignError(DAMAGED)
    if any(r["predicate"] not in onto.predicates for r in by_tag["relation"]):
        raise CampaignError(DAMAGED)
