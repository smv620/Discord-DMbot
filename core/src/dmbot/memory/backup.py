"""Campaign memory in campaign backups (an `ExportSection`).

Left out: the change log (a restored campaign starts a fresh undo history) and mentions
("this word in this line meant Belleros"), which are by far the largest part and are
rebuilt as new sessions are transcribed. Each row carries a `table` tag; rows from the
file are untrusted and fully checked before anything is written.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from psycopg import errors as pg_errors
from psycopg import sql
from psycopg.types.json import Jsonb

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn
from dmbot.memory import beliefs, evidence, notify, sheets
from dmbot.memory._changes import (
    ALIAS_KNOWERS,
    ALIASES,
    ATTITUDES,
    BELIEFS,
    CLAIMS,
    CORRECTIONS,
    ENTITIES,
    EVIDENCE,
    FLAGS,
    IMPRESSIONS,
    KNOWERS,
    PERSPECTIVES,
    PREDICATES,
    RELATIONS,
    RULE_LINKS,
    SHEETS,
    STATES,
    TYPES,
    Table,
    row_of,
    scoped_select,
)
from dmbot.memory.checks import FLAG_KINDS
from dmbot.memory.models import (
    ALIAS_KINDS,
    BELIEF_STATUSES,
    CHANGE_KINDS,
    CLAIM_STATUSES,
    CONDITIONS,
    DESCRIPTION_MAX,
    DETAIL_MAX,
    ENTITY_STATUSES,
    EVIDENCE_ID_MAX,
    EVIDENCE_MAX,
    FIX,
    HOWS,
    KEEP,
    LINE_REF_MAX,
    LINK_EDITIONS,
    LINK_KINDS,
    LINK_SOURCE_MAX,
    LIST_MAX,
    NAME_MAX,
    PERSPECTIVE_KINDS,
    PLAYER_CHARACTER,
    ROLES,
    SOURCES,
    THREAD_STATES,
    is_id,
)
from dmbot.memory.ontology import (
    ACTIVE,
    CORE_PREDICATES,
    CORE_TYPES,
    DEPRECATED,
    LABEL_MAX,
    MIGRATED_KINDS,
    Ontology,
    PredicateTerm,
    TypeTerm,
    conditions_for,
    god_is_an_npc,
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
    "state": STATES,
    "claim": CLAIMS,
    "alias": ALIASES,
    "relation": RELATIONS,
    "perspective": PERSPECTIVES,  # who holds beliefs (#2409)
    "alias_knower": ALIAS_KNOWERS,  # which characters know an other name (#2935)
    "knower": KNOWERS,  # which characters know each statement (#2995)
    "belief": BELIEFS,
    "evidence": EVIDENCE,  # what a connection or belief stands on (#2662)
    "impression": IMPRESSIONS,
    "attitude": ATTITUDES,
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
    "said_as": 80,
    "key": NAME_MAX,
    "heard": NAME_MAX,
    "heard_key": NAME_MAX,
    "detail": DETAIL_MAX,
    "line_ref": LINE_REF_MAX,
    "parent": 40,
    "inverse_label": LABEL_MAX,
    "rules_source": LINK_SOURCE_MAX,
    "replaced_by": 40,
    "type": 40,
    "predicate": 40,
    "subject": NAME_MAX,
    "relationship": NAME_MAX,
    "object": NAME_MAX,
    "details": DETAIL_MAX,
    "prompt_version": 40,
    "model": 80,
    "unnamed_role": 40,  # a person the story never names (#2790)
    "unnamed_what": NAME_MAX,
    "decided": 60,  # how a pronoun was settled (#2790): fixed words and a number
}
_CHOICES: dict[str, Sequence[str]] = {
    "status": (
        *ENTITY_STATUSES,
        ACTIVE,
        DEPRECATED,
        "open",
        "resolved",
        *CLAIM_STATUSES,
        *BELIEF_STATUSES,
    ),
    "how": HOWS,
    "kind": (*ALIAS_KINDS, *FLAG_KINDS, *LINK_KINDS, *PERSPECTIVE_KINDS),
    "condition": (*CONDITIONS, *THREAD_STATES),
    "role": ROLES,
    "edition": LINK_EDITIONS,
    "action": (FIX, KEEP),
    "source": SOURCES,
    "change_kind": CHANGE_KINDS,
}
_IDS = {"id", "entity_id", "fact_id", "merged_into", "used_by", "subject_id", "object_id",
        "relation_id", "other_id", "from_set", "viewer_id", "holder_id", "toward_id",
        "perspective_id", "manipulated_by", "unnamed_anchor", "alias_id",
        "via_alias_id", "derived_from"}  # fmt: skip
_INTS = {"played_by", "speaker_id", "session_started_at", "decided_at", "created_at",
         "from_session_at", "to_session_at", "from_game_time", "to_game_time",
         "max_per_subject", "closed_at", "session_at", "recorded_at", "seq"}  # fmt: skip
_VERSIONS = {"version", "definition_version"}  # from 1 up (#1189)
_LISTS = {"examples", "subject_types", "object_types", "conflicts_with", "sound_codes",
          "mention_ids", "covers", "not_this", "synonyms", "evidence_ids", "hidden_by"}  # fmt: skip
_NULLABLE = {
    "role", "edition", "played_by", "merged_into", "used_by", "other_id", "entity_id",
    "replaced_by", "parent", "from_session_at", "to_session_at", "from_game_time",
    "to_game_time", "max_per_subject", "closed_at", "unique_item", "fact_id",
    "session_started_at", "decided_at", "said_as", "from_set", "session_at", "manipulated_by",
    "unnamed_anchor", "unnamed_role", "unnamed_what", "decided", "via_alias_id",
    "derived_from", "change_kind",
}  # fmt: skip


def _valid(column: str, value: object) -> bool:
    """Is a value from an untrusted backup file the right shape for its column?"""
    if value is None:
        return column in _NULLABLE
    if column in _IDS:
        return is_id(value)
    if column in _VERSIONS:
        return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 10_000
    if column in _INTS:
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= INT64_MAX
    if column in _LISTS:
        return (
            isinstance(value, list)
            and len(value) <= LIST_MAX
            and all(isinstance(v, str) and 0 < len(v) <= DESCRIPTION_MAX for v in value)
        )
    if column == "lines":  # transcript line references
        return (
            isinstance(value, list)
            and len(value) <= LIST_MAX
            and all(isinstance(v, str) and 0 < len(v) <= LINE_REF_MAX for v in value)
        )
    if column == "speaker_ids":  # Discord ids
        return (
            isinstance(value, list)
            and len(value) <= LIST_MAX
            and all(
                isinstance(v, int) and not isinstance(v, bool) and 0 < v <= INT64_MAX for v in value
            )
        )
    if column in (
        "secret",
        "is_symmetric",
        "needs_look",
        "known",
        "unique_item",
        "audience_limited",
        "party",
        "hidden_from_players",
        "made_before_name_gate",
        "unnamed",
        "unrevealed",
        "directional",
        "pinned",
    ):
        return isinstance(value, bool)
    if column == "score":  # an impression's or attitude's feeling (#1655)
        return isinstance(value, int) and not isinstance(value, bool) and -100 <= value <= 100
    if column == "weight":  # how much evidence is behind it
        return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 100
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
            if by_tag["perspective"]:
                # the file brings its own; the two a new campaign was made with have no beliefs yet
                await conn.execute(
                    "DELETE FROM memory_perspectives WHERE guild_id = %s AND campaign_id = %s",
                    (guild_id, campaign_id),
                )
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
            # a file from before beliefs, or a connection its file gave none: worked out from the
            # connections, as the migration did (a belief the file has is left as it is)
            await beliefs.backfill(conn, guild_id, campaign_id)
        except (pg_errors.IntegrityError, pg_errors.DataError) as exc:
            raise CampaignError(DAMAGED) from exc
        await bump_version(conn, guild_id, campaign_id)

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        names = (
            "memory_changes",
            "merge_restores",
            "memory_mentions",
            *(t.name for t in reversed(_TAGS.values())),
        )
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
            if table is CLAIMS:
                # a claim's line references name their speakers (#2676); a restored campaign has
                # none of those lines, and a backup is read by the whole server
                row["lines"] = []
            if table is BELIEFS:
                # the old list can hold a line's reference, which names its speaker; the typed
                # links (a line's as a placeholder) carry the evidence now, and a backup is read
                # by the whole server
                row["evidence_ids"] = []
            if table is EVIDENCE and row["kind"] == evidence.LINE:
                # A line of this server's transcripts: its reference names the speaker, and a
                # restored campaign has none of these lines, so the link goes as a placeholder
                # (it keeps the kind and the session). A backup is read by the whole server.
                row["target_id"] = None
            out.append({"table": tag, **row})
    return out


def _checked_rows(rows: list[Any]) -> dict[str, list[dict[str, Any]]]:
    """A backup's memory rows, every value checked, grouped by table tag. Untrusted:
    raises CampaignError(DAMAGED) on anything out of shape.

    A loop among the "led to" links is not out of shape (#2673): the restore trusts the backup
    for that, on purpose. A backup holds the whole campaign and must always be loadable (CLAUDE.md:
    a campaign is never lost), and a file written before the loop checks could hold one; refusing
    it would strand the campaign, while a loop only shows as a neighbour on a card (the readers
    follow one link, not a chain) and a new link or a merge can't add to it."""
    by_tag: dict[str, list[dict[str, Any]]] = {tag: [] for tag in _TAGS}
    rows = _with_definition_fields(_gods_are_npcs(_from_before_kinds(rows)))
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
        elif table is EVIDENCE:
            _check_evidence(raw)
        elif table is KNOWERS:  # four statement columns, exactly one set (`_check_knowers`)
            if not all(
                raw[c] is None or is_id(raw[c])
                for c in ("about_entity_id", "state_id", "relation_id", "alias_id")
            ) or not all(
                _valid(c, raw[c])
                for c in ("id", "perspective_id", "created_at", "confidence", "hidden_by")
            ):
                raise CampaignError(DAMAGED)
        elif not all(_valid(c, raw[c]) for c in table.columns if c != "reading"):
            # (a claim's reading has its own check, `_check_claim`)
            raise CampaignError(DAMAGED)
        if table is CLAIMS:
            _check_claim(raw)
        elif raw.get("status") == "dropped":
            raise CampaignError(DAMAGED)  # only a claim can be dropped
        if table is BELIEFS and raw["derived_from"] is not None and raw["change_kind"] is None:
            raise CampaignError(DAMAGED)  # a belief came from another, so it says how
        if table is BELIEFS and (
            len(raw["evidence_ids"]) > EVIDENCE_MAX
            or any(len(e) > EVIDENCE_ID_MAX for e in raw["evidence_ids"])
        ):
            raise CampaignError(DAMAGED)
        if table is PERSPECTIVES and (raw["kind"] in ("truth", "party")) != (
            raw["entity_id"] is None
        ):
            raise CampaignError(DAMAGED)  # only a character's perspective points at an entry
        if table is ALIASES and raw["secret"] and raw["said_as"] is not None:
            raise CampaignError(DAMAGED)  # a secret name is never given to the narrator
        if table is ALIASES:  # worked out again, not taken from the file
            raw = {**raw, "sound_codes": list(sound_codes(raw["text"]))}
        by_tag[raw["table"]].append(raw)
    _tidy_evidence(by_tag)
    _check_terms(by_tag)
    # A sheet belongs to the player playing the character, as everywhere else (#723).
    # One left from another player, or on an entry with no player (an undone merge), is
    # shown nowhere: dropped, not a reason to refuse the backup. One for an entry not in
    # the file is damage.
    secret = {r["id"] for r in by_tag["relation"] if r["secret"] or r["unrevealed"]}
    party = {p["id"] for p in by_tag["perspective"] if p["kind"] == "party"}
    newest: dict[str, dict[str, Any]] = {}  # the players' current belief in each connection
    for b in sorted(by_tag["belief"], key=lambda b: b["seq"]):
        if b["perspective_id"] in party:
            newest[b["relation_id"]] = b
    if any(b["confidence"] >= 1.0 and rel in secret for rel, b in newest.items()):
        raise CampaignError(DAMAGED)  # the players would hold a secret connection as fact now
    # (an older belief of theirs in one that is hidden again is history, and is kept)
    # Who knows an other name (#2935): a row for a name or a character the file does not hold is
    # damage; one for a name that is no longer secret, or a point of view that is not a player
    # character's, is history and dropped (a backup must always be loadable).
    aliases = {r["id"]: r for r in by_tag["alias"]}
    views = {r["id"]: r for r in by_tag["perspective"]}
    if any(k["alias_id"] not in aliases or k["perspective_id"] not in views
           for k in by_tag["alias_knower"]):  # fmt: skip
        raise CampaignError(DAMAGED)
    by_tag["alias_knower"] = [
        k
        for k in by_tag["alias_knower"]
        if aliases[k["alias_id"]]["secret"] and views[k["perspective_id"]]["kind"] == "pc"
    ]
    # Who knows each statement (#2995): a row must name exactly one statement the file holds and a
    # point of view the file holds; anything else is damage. A row for a point of view that is not
    # a character's cannot exist (the party is never a knower), so it is damage too.
    _check_knowers(by_tag, views)
    # A row asleep waits for entries of this campaign: a name in the mark that is not an entry of
    # the file is dropped from it (a row left with none is awake), never a reason to refuse.
    entry_ids = {r["id"] for r in by_tag["entity"]}
    for tag in ("knower", "belief"):
        for row in by_tag[tag]:
            row["hidden_by"] = [h for h in row["hidden_by"] if h in entry_ids]
    entities = {r["id"]: r for r in by_tag["entity"]}
    if any(r["entity_id"] not in entities for r in by_tag["sheet"]):
        raise CampaignError(DAMAGED)
    by_tag["sheet"] = [
        r for r in by_tag["sheet"] if entities[r["entity_id"]]["played_by"] == r["player_id"]
    ]
    return by_tag


def _check_knowers(
    by_tag: dict[str, list[dict[str, Any]]], views: dict[str, dict[str, Any]]
) -> None:
    """Every `memory_knowers` row names one statement in the file and a character's point of
    view in the file (#2995). A backup must hold the whole list or none of it, so damage refuses
    the file."""
    held = {
        "about_entity_id": {r["id"] for r in by_tag["entity"]},
        "state_id": {r["id"] for r in by_tag["state"]},
        "relation_id": {r["id"] for r in by_tag["relation"]},
        "alias_id": {r["id"] for r in by_tag["alias"]},
    }
    for k in by_tag["knower"]:
        named = [c for c in held if k[c] is not None]
        if len(named) != 1 or k[named[0]] not in held[named[0]]:
            raise CampaignError(DAMAGED)
        view = views.get(k["perspective_id"])
        if view is None or view["kind"] not in ("pc", "npc"):
            raise CampaignError(DAMAGED)


_OLD_PARENTS = {k: v[0] for k, v in MIGRATED_KINDS.items()}
_NEW_PREDICATE_FIELDS: dict[str, Any] = {
    "covers": [], "not_this": [], "inverse_label": "", "synonyms": [], "version": 1,
    "from_set": None,  # from before a set could lend relationships (#1456)
}  # fmt: skip


def _replaced_by_core(row: Mapping[str, Any]) -> bool:
    return row.get("status") == DEPRECATED and row.get("replaced_by") == row.get("key")


def _with_definition_fields(rows: list[Any]) -> list[Any]:
    """A backup from before definitions were versioned (#1189) has none of the new columns:
    its relationships get empty definitions at version 1 and its facts version 1. A fact
    that names a version this DMbot doesn't know (a backup from a newer release) is read as
    the oldest known version, 1; the after-session check then looks at it again against the
    definition it is held to now."""
    known = {p.key: p.version for p in CORE_PREDICATES}
    core_predicates = frozenset(known)
    for raw in rows:
        if isinstance(raw, dict) and raw.get("table") == "predicate":
            version = raw.get("version", 1)
            if isinstance(raw.get("key"), str) and isinstance(version, int):
                known.setdefault(raw["key"], version)
    out: list[Any] = []
    for raw in rows:
        if isinstance(raw, dict) and raw.get("table") == "entity":
            # from before one-of-a-kind items (#1267) and before a clue's audience (#1649)
            raw = {
                "unique_item": None,
                "audience_limited": False,
                "party": False,
                "hidden_from_players": False,  # from before #2321
                "made_before_name_gate": True,  # a file from before the gate holds old rows (#2358)
                "manipulated_by": None,  # from before beliefs (#2409)
                "unnamed": False,  # everyone was named before #2790
                "unnamed_anchor": None,
                "unnamed_role": None,
                "unnamed_what": None,
                **raw,
            }
        elif isinstance(raw, dict) and raw.get("table") == "belief":
            # from before the pin (#2447), a hide's sleep (#3082) and lineage (#3091)
            raw = {
                "pinned": False,
                "hidden_by": [],
                "derived_from": None,
                "change_kind": None,
                **raw,
            }
        elif isinstance(raw, dict) and raw.get("table") == "knower":
            raw = {"hidden_by": [], **raw}  # from before a hide could put a row to sleep (#3082)
        elif isinstance(raw, dict) and raw.get("table") == "claim":
            raw = {"reading": None, **raw}  # from before readings (#2412)
        elif isinstance(raw, dict) and raw.get("table") == "alias":
            raw = {"said_as": None, **raw}  # from before the narrator's spelling (#1419)
        elif isinstance(raw, dict) and raw.get("table") == "predicate":
            raw = {**_NEW_PREDICATE_FIELDS, **raw}
            if raw.get("key") in core_predicates:
                # A campaign's own relationship whose name the core has taken since (from an old
                # file, or a current one made before a release added the term, #3035): never
                # refused. The campaign's row is kept as replaced by the core one, its
                # connections use the core term, and `recheck_definitions` looks at its facts
                # against the core meaning.
                raw = {**raw, "status": DEPRECATED, "replaced_by": raw["key"], "version": 1}
        elif isinstance(raw, dict) and raw.get("table") == "relation":
            version = raw.get("definition_version", 1)
            name = raw.get("predicate")
            top = known.get(name, 1) if isinstance(name, str) else 1
            if isinstance(version, int) and not isinstance(version, bool) and version > top:
                version = 1
            # from before #2404: every connection a file holds had been shown to the players;
            # from before #2457: no family role had a direction
            # from before #2790: no connection had a pronoun DMbot settled
            raw = {
                "unrevealed": False,
                "directional": False,
                "decided": None,
                "via_alias_id": None,  # from before #3037: no connection came through a name
                **raw,
                "definition_version": version,
            }
        out.append(raw)
    return out


def _gods_are_npcs(rows: list[Any]) -> list[Any]:
    """A backup made while a god was a role (#2562) reads as the migration moved it: an NPC,
    with "god" kept in its description. Any other backup passes through as it is."""
    return [
        {
            **r,
            "role": "npc",
            "description": god_is_an_npc(str(r.get("description") or "")),
        }
        if isinstance(r, dict) and r.get("table") == "entity" and r.get("role") == "god"
        else r
        for r in rows
    ]


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
            kind, role, look = MIGRATED_KINDS.get(
                str(raw.get("type")), (raw.get("type"), None, False)
            )
            if role is None and raw.get("played_by") is not None:
                role = PLAYER_CHARACTER  # only player characters were played before
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
    return Jsonb(value) if column in ("sheet", "reading") and value is not None else value


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
    # A campaign's own relationship that the core has since taken the name of is kept as a
    # deprecated row "replaced by" the core one (#1189); any other core key is damage.
    if any(r["key"] in core for r in by_tag["type"]) or any(
        r["key"] in core and not _replaced_by_core(r) for r in by_tag["predicate"]
    ):
        raise CampaignError(DAMAGED)
    onto = Ontology.build(
        (_type_term(r) for r in by_tag["type"]),
        (_predicate_term(r) for r in by_tag["predicate"]),
    )
    if any(r["type"] not in onto.types for r in by_tag["entity"]):
        raise CampaignError(DAMAGED)
    # A role belongs to a character; only a player character (or a person) has a player;
    # a link is on a character that is in the file.
    if any(
        (r["role"] is not None and not onto.is_a(r["type"], "character"))
        or (
            r["played_by"] is not None
            and r["role"] != "player_character"
            and r["type"] != "person"  # a person keeps their Discord ID here (#1188)
        )
        for r in by_tag["entity"]
    ):
        raise CampaignError(DAMAGED)
    # One live person for each Discord user, and a `plays` link goes from a person to a
    # character (the database enforces the first; a damaged file must not reach it).
    live_people = [
        r["played_by"]
        for r in by_tag["entity"]
        if r["type"] == "person" and r["status"] in ("proposed", "confirmed")
    ]
    if len(live_people) != len(set(live_people)) or any(
        r["played_by"] is None for r in by_tag["entity"] if r["type"] == "person"
    ):
        raise CampaignError(DAMAGED)
    kinds = {r["id"]: r["type"] for r in by_tag["entity"]}
    if any(
        kinds.get(r["subject_id"]) != "person"
        or not onto.is_a(kinds.get(r["object_id"], ""), "character")
        for r in by_tag["relation"]
        if r["predicate"] == "plays"
    ):
        raise CampaignError(DAMAGED)
    _check_states(onto, by_tag)
    characters = {r["id"] for r in by_tag["entity"] if onto.is_a(r["type"], "character")}
    if any(r["entity_id"] not in characters for r in by_tag["rule_link"]):
        raise CampaignError(DAMAGED)
    if any(r["known"] != bool(r["rules_source"]) for r in by_tag["rule_link"]):
        raise CampaignError(DAMAGED)
    if any(r["predicate"] not in onto.predicates for r in by_tag["relation"]):
        raise CampaignError(DAMAGED)


def _check_claim(row: Mapping[str, Any]) -> None:
    """A claim in a file: a status a claim can have, a fact only when it was confirmed, and
    never a plan that someone says is waiting for the DM (a plan is never asked)."""
    if row["status"] not in CLAIM_STATUSES:
        raise CampaignError(DAMAGED)
    if not _reading_ok(row["reading"]):
        raise CampaignError(DAMAGED)
    if row["reading"] is not None and row["status"] == "confirmed":
        raise CampaignError(DAMAGED)  # a reading is placed as a belief, never kept as a fact
    if row["fact_id"] is not None and row["status"] != "confirmed":
        raise CampaignError(DAMAGED)
    if row["status"] == "proposed" and row["how"] == "plan":
        raise CampaignError(DAMAGED)


def _check_standing(by_tag: dict[str, list[dict[str, Any]]]) -> None:
    """Impressions and attitudes link entries in the file, between the kinds the screen allows,
    and only one of a pair is current (the database enforces the last; a damaged file must not
    reach it). Dates are checked by `_valid`."""
    kinds = {r["id"]: r["type"] for r in by_tag["entity"]}
    open_pairs: set[tuple[str, str, str]] = set()
    for tag, held, aimed in (
        ("impression", "viewer_id", "subject_id"),
        ("attitude", "holder_id", "toward_id"),
    ):
        for r in by_tag[tag]:
            # Only what the database holds to: both ends are in the file and differ. (An entry
            # may have changed kind since the DM set this, so the kinds are not checked.)
            if r[held] not in kinds or r[aimed] not in kinds or r[held] == r[aimed]:
                raise CampaignError(DAMAGED)
            if r["closed_at"] is None:
                key = (tag, r[held], r[aimed])
                if key in open_pairs:
                    raise CampaignError(DAMAGED)
                open_pairs.add(key)


def _check_states(onto: Ontology, by_tag: dict[str, list[dict[str, Any]]]) -> None:
    """A condition belongs to an entry in the file, one its kind can have, one current at a
    time (the database enforces the last; a damaged file must not reach it); only an item is
    one of a kind (#1267)."""
    kinds = {r["id"]: r["type"] for r in by_tag["entity"]}
    if any(
        r["unique_item"] is not None and not onto.is_a(r["type"], "item") for r in by_tag["entity"]
    ):
        raise CampaignError(DAMAGED)
    if any(r["audience_limited"] and r["type"] != "clue" for r in by_tag["entity"]):
        raise CampaignError(DAMAGED)  # only a clue has an audience (#1649)
    parties = [r for r in by_tag["entity"] if r["party"]]
    if len(parties) > 1 or any(r["type"] != "faction" for r in parties):
        raise CampaignError(DAMAGED)  # at most one party, and it is a group (#1655)
    _check_standing(by_tag)
    open_for: set[str] = set()
    for r in by_tag["state"]:
        if r["entity_id"] not in kinds:
            raise CampaignError(DAMAGED)
        if r["closed_at"] is not None:
            continue  # history: an entry that became another kind keeps what it was
        allowed = conditions_for(onto, kinds[r["entity_id"]])
        if allowed is None or r["condition"] not in allowed or r["entity_id"] in open_for:
            raise CampaignError(DAMAGED)
        open_for.add(r["entity_id"])


def _check_evidence(raw: dict[str, Any]) -> None:
    """A link row from a file: one owner (a connection or a belief), a known kind, a short target
    or none (a placeholder), and the session and time as whole numbers. Untrusted."""
    owner_ok = (raw["relation_id"] is None) != (raw["belief_id"] is None)
    ids_ok = all(raw[c] is None or is_id(raw[c]) for c in ("relation_id", "belief_id")) and is_id(
        raw["id"]
    )
    target = raw["target_id"]
    target_ok = target is None or (
        isinstance(target, str) and 1 <= len(target) <= evidence.TARGET_MAX
    )
    ints_ok = (
        all(
            raw[c] is None
            or (isinstance(raw[c], int) and not isinstance(raw[c], bool) and raw[c] >= 0)
            for c in ("session_started_at", "added_at")
        )
        and raw["added_at"] is not None
    )
    if not (owner_ok and ids_ok and target_ok and ints_ok and raw["kind"] in evidence.KINDS):
        raise CampaignError(DAMAGED)


def _tidy_evidence(by_tag: dict[str, list[dict[str, Any]]]) -> None:
    """What a file's links may point at, whatever the file says (it is untrusted): a line is never
    brought back (its reference names a speaker, and a restored campaign has none of those
    lines), and a claim, clue or event must be a row of this same file, else the link is a
    placeholder. A belief's old list and a claim's `lines` are not read from a file for the same
    reason (they hold line references, which name speakers)."""
    held = {r["id"] for r in by_tag["belief"]}
    for row in by_tag["belief"]:
        row["evidence_ids"] = []
        # a lineage link to a belief the file does not hold (or to itself) is a placeholder, and
        # a link always says how (the database's rule); a file is never refused for it
        if row["derived_from"] is not None and (
            row["derived_from"] not in held or row["derived_from"] == row["id"]
        ):
            row["derived_from"] = None
    for row in by_tag["claim"]:  # an older file's references are not brought back (#2676)
        row["lines"] = []
    claims = {r["id"] for r in by_tag["claim"]}
    entity_kind = {r["id"]: r["type"] for r in by_tag["entity"]}
    for row in by_tag["evidence"]:
        if row["kind"] == evidence.SOURCE and row["target_id"] not in entity_kind:
            row["target_id"] = None  # (the entry it names is not in the file)
        kind, target = row["kind"], row["target_id"]
        if (
            kind == evidence.LINE
            or (kind == evidence.CLAIM and target not in claims)
            or (kind in (evidence.CLUE, evidence.EVENT) and entity_kind.get(target) != kind)
        ):
            row["target_id"] = None


_READING_KEYS: dict[str, type | tuple[type, ...]] = {
    "view": str, "confidence": (int, float), "status": str, "because": str, "manipulated_by": str,
    "new_assertion": bool, "dm_confirms": bool,
}  # fmt: skip


def _reading_ok(reading: object) -> bool:
    """A claim's reading from a file: none, or exactly the keys a reading has, each the right kind
    of value, a known status, a confidence from 0 to 1, a view that is the party, the truth or a
    character's, and short words. Untrusted."""
    if reading is None:
        return True
    if not isinstance(reading, dict) or set(reading) != set(_READING_KEYS):
        return False
    for key, kind in _READING_KEYS.items():
        value = reading[key]
        if not isinstance(value, kind) or (kind is not bool and isinstance(value, bool)):
            return False
        if isinstance(value, str) and len(value) > NAME_MAX + 3:
            return False
    view = reading["view"]
    return (
        reading["status"] in BELIEF_STATUSES
        and 0 <= reading["confidence"] <= 1
        and (view in ("party", "truth") or (view.startswith("pc:") and len(view) > 3))
    )
