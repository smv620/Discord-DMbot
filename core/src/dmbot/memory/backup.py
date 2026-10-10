"""Campaign memory in campaign backups (an `ExportSection`).

Left out: the change log (a restored campaign starts a fresh undo history) and mentions
("this word in this line meant Belleros"), which are by far the largest part and are
rebuilt as new sessions are transcribed. Each row carries a `table` tag; rows from the
file are untrusted and fully checked before anything is written.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from psycopg import errors as pg_errors
from psycopg import sql
from psycopg.types.json import Jsonb

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn
from dmbot.memory import notify, sheets
from dmbot.memory._changes import (
    ALIASES,
    CORRECTIONS,
    ENTITIES,
    FLAGS,
    PREDICATES,
    RELATIONS,
    RULE_LINKS,
    SHEETS,
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
    LINK_EDITIONS,
    LINK_KINDS,
    LINK_SOURCE_MAX,
    LIST_MAX,
    NAME_MAX,
    ROLES,
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
from dmbot.memory.sounds import sound_codes

DAMAGED = "This backup file is damaged (bad campaign memory entry)."
INT64_MAX = 2**63 - 1

# Tag in the file → table, in load order (anything a row links to comes first).
_TAGS: dict[str, Table] = {
    "type": TYPES,
    "predicate": PREDICATES,
    "entity": ENTITIES,
    "rule_link": RULE_LINKS,
    "alias": ALIASES,
    "relation": RELATIONS,
    "correction": CORRECTIONS,
    "flag": FLAGS,
    "sheet": SHEETS,
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
    "rules_source": LINK_SOURCE_MAX,
    "replaced_by": 40,
    "type": 40,
    "predicate": 40,
}
_CHOICES: dict[str, Sequence[str]] = {
    "status": (*ENTITY_STATUSES, ACTIVE, DEPRECATED, "open", "resolved"),
    "kind": (*ALIAS_KINDS, *FLAG_KINDS, *LINK_KINDS),
    "role": ROLES,
    "edition": LINK_EDITIONS,
    "action": (FIX, KEEP),
    "source": SOURCES,
}
_IDS = {"id", "entity_id", "merged_into", "used_by", "subject_id", "object_id", "relation_id",
        "other_id"}  # fmt: skip
_INTS = {"played_by", "created_at", "from_session_at", "to_session_at", "from_game_time",
         "to_game_time", "max_per_subject"}  # fmt: skip
_LISTS = {"examples", "subject_types", "object_types", "conflicts_with", "sound_codes",
          "mention_ids"}  # fmt: skip
_NULLABLE = {
    "role", "edition", "played_by", "merged_into", "used_by", "other_id", "entity_id",
    "replaced_by", "parent", "from_session_at", "to_session_at", "from_game_time",
    "to_game_time", "max_per_subject",
}  # fmt: skip


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
    if column in ("secret", "is_symmetric", "needs_look", "known"):
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
            raws = await cur.fetchall()
            # Building the rows is pure CPU (a big campaign takes a good fraction of a
            # second): off the event loop, so voice and other servers don't wait (#164).
            # One table at a time, so only one table's raw rows are held at once (#393).
            out += await asyncio.to_thread(_dump_rows, [(tag, table, raws)])
            del raws
        return out

    def check(self, rows: list[Any]) -> dict[str, list[dict[str, Any]]]:
        """Every value checked and sound codes worked out: pure CPU, which the store
        runs in a worker thread before its transaction (#164)."""
        return _checked_rows(rows)

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        """`rows` as `check` returned them, or straight from a file (checked here)."""
        by_tag = rows if isinstance(rows, dict) else await asyncio.to_thread(_checked_rows, rows)
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
                            (guild_id, campaign_id, *(_db_value(c, r[c]) for c in table.columns))
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


def _dump_rows(fetched: list[tuple[str, Table, list[Any]]]) -> list[Any]:
    """The file's rows from the database's, each with its table tag."""
    out: list[Any] = []
    for tag, table, raws in fetched:
        for raw in raws:
            row = row_of(raw, table)
            if table is RELATIONS:
                row["mention_ids"] = []  # mentions aren't backed up
            out.append({"table": tag, **row})
    return out


def _checked_rows(rows: list[Any]) -> dict[str, list[dict[str, Any]]]:
    """A backup's memory rows, every value checked, grouped by table tag. Untrusted:
    raises CampaignError(DAMAGED) on anything out of shape."""
    by_tag: dict[str, list[dict[str, Any]]] = {tag: [] for tag in _TAGS}
    rows = _from_before_kinds(rows)
    for raw in rows:
        if not isinstance(raw, dict) or raw.get("table") not in _TAGS:
            raise CampaignError(DAMAGED)
        table = _TAGS[raw["table"]]
        if set(raw) != {"table", *table.columns}:
            raise CampaignError(DAMAGED)
        if table is RELATIONS and raw["mention_ids"]:
            raise CampaignError(DAMAGED)
        if table is SHEETS:
            raw = _checked_sheet(raw)
        elif not all(_valid(c, raw[c]) for c in table.columns):
            raise CampaignError(DAMAGED)
        if table is ALIASES:  # worked out again, not taken from the file
            raw = {**raw, "sound_codes": list(sound_codes(raw["text"]))}
        by_tag[raw["table"]].append(raw)
    _check_terms(by_tag)
    # A sheet belongs to the player playing the character, as everywhere else (#723).
    # One left from another player, or on an entry with no player (an undone merge), is
    # shown nowhere: dropped, not a reason to refuse the backup. One for an entry not in
    # the file is damage.
    entities = {r["id"]: r for r in by_tag["entity"]}
    if any(r["entity_id"] not in entities for r in by_tag["sheet"]):
        raise CampaignError(DAMAGED)
    by_tag["sheet"] = [
        r for r in by_tag["sheet"] if entities[r["entity_id"]]["played_by"] == r["player_id"]
    ]
    return by_tag


# The older kinds, as a backup from before #1034 holds them → (kind now, role, needs a look).
_OLD_KINDS: dict[str, tuple[str, str | None, bool]] = {
    "player_character": ("character", "player_character", False),
    "npc": ("character", "npc", False),
    "deity": ("character", "god", False),
    "creature": ("character", None, True),
    "spell": ("concept", None, True),
}
_OLD_PARENTS = {k: v[0] for k, v in _OLD_KINDS.items()}


def _from_before_kinds(rows: list[Any]) -> list[Any]:
    """A backup from before #1034 (its entries have no role) read as the migration reads
    the database: npc, player character and god become a character with that role, a named
    creature a character and a spell an idea, both marked "needs a look"; a campaign's own
    kinds and relationships that named an older kind name its replacement. Nothing is
    dropped. A current backup passes through as it is."""
    entities = [r for r in rows if isinstance(r, dict) and r.get("table") == "entity"]
    if not entities or all("role" in r for r in entities):
        return rows
    out: list[Any] = []
    for raw in rows:
        if not isinstance(raw, dict):
            out.append(raw)
            continue
        table = raw.get("table")
        if table == "entity" and "role" not in raw:
            kind, role, look = _OLD_KINDS.get(str(raw.get("type")), (raw.get("type"), None, False))
            raw = {**raw, "type": kind, "role": role, "needs_look": look}
        elif table == "type" and raw.get("parent") in _OLD_PARENTS:
            raw = {**raw, "parent": _OLD_PARENTS[raw["parent"]]}
        elif table == "predicate":
            raw = {
                **raw,
                **{
                    column: _renamed(raw.get(column))
                    for column in ("subject_types", "object_types")
                    if column in raw
                },
            }
        out.append(raw)
    return out


def _renamed(kinds: Any) -> Any:
    """A list of kinds with the older ones replaced, each once."""
    if not isinstance(kinds, list):
        return kinds
    seen: list[Any] = []
    for kind in kinds:
        now = _OLD_PARENTS.get(kind, kind) if isinstance(kind, str) else kind
        if now not in seen:
            seen.append(now)
    return seen


def _checked_sheet(raw: dict[str, Any]) -> dict[str, Any]:
    """A sheet row from a backup: the link must be a character link, and the snapshot
    goes through the same allow list as one read from D&D Beyond, so a hand-edited file
    can't store anything else (CLAUDE.md, IP rule)."""
    url, snapshot, source, fetched = raw["url"], raw["sheet"], raw["source"], raw["fetched_at"]
    if not is_id(raw["entity_id"]) or not _valid("played_by", raw["player_id"]):
        raise CampaignError(DAMAGED)
    if raw["player_id"] is None or raw["player_id"] == 0:
        raise CampaignError(DAMAGED)
    if url is not None and (
        not isinstance(url, str) or sheets.sheet_url(sheets.character_id(url) or 0) != url
    ):
        raise CampaignError(DAMAGED)
    cleaned = None if snapshot is None else sheets.clean(snapshot)
    if snapshot is not None and cleaned is None:
        if url is None or not _other_version(snapshot):
            raise CampaignError(DAMAGED)
        # A snapshot from another version of DMbot: keep the link, read it again at the
        # next session, rather than refuse the whole backup.
        return {**raw, "sheet": None, "source": None, "fetched_at": None}
    if url is None and cleaned is None:
        raise CampaignError(DAMAGED)
    if cleaned is not None and (source != cleaned["source"] or not _valid("created_at", fetched)):
        raise CampaignError(DAMAGED)
    if cleaned is None and (source is not None or fetched is not None):
        raise CampaignError(DAMAGED)
    return {**raw, "sheet": cleaned}


def _other_version(snapshot: Any) -> bool:
    return isinstance(snapshot, dict) and snapshot.get("v") != sheets.SNAPSHOT_VERSION


def _db_value(column: str, value: Any) -> Any:
    return Jsonb(value) if column == "sheet" and value is not None else value


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
    # A role belongs to a character; only a player character has a player; a link is on a
    # character that is in the file.
    if any(
        (r["role"] is not None and not onto.is_a(r["type"], "character"))
        or (r["played_by"] is not None and r["role"] != "player_character")
        for r in by_tag["entity"]
    ):
        raise CampaignError(DAMAGED)
    characters = {r["id"] for r in by_tag["entity"] if onto.is_a(r["type"], "character")}
    if any(r["entity_id"] not in characters for r in by_tag["rule_link"]):
        raise CampaignError(DAMAGED)
    if any(r["known"] != bool(r["rules_source"]) for r in by_tag["rule_link"]):
        raise CampaignError(DAMAGED)
    if any(r["predicate"] not in onto.predicates for r in by_tag["relation"]):
        raise CampaignError(DAMAGED)
