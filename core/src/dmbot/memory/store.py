"""Campaign memory storage (docs/PLAN.md, "Campaign memory (EntityBot)", #126).

EntityBot is the only writer, through this class. Every write:
- runs in one per-server transaction (`Database.guild`), filters on server AND campaign,
  and can only link rows of the same campaign (composite foreign keys);
- is logged row by row in `memory_changes`, grouped in a numbered batch, so any
  operation can be undone with `undo(batch)`;
- bumps the campaign's `memory_version` and sends a Postgres NOTIFY carrying only the
  campaign ID and version (notifications skip row-level security, so never names), so
  copies held in memory can reload.

Only the DM's word confirms anything (`source="dm"`); other sources propose.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Collection, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

from psycopg import errors, sql

from dmbot.db import Database, row_int
from dmbot.memory import beliefs, merge_restore, notify, standing, states
from dmbot.memory import threads as threads_mod
from dmbot.memory._changes import (
    ALIASES,
    ATTITUDES,
    BELIEFS,
    CHANGED_SINCE,
    CORRECTIONS,
    DEPENDENTS,
    ENTITIES,
    FLAGS,
    IMPRESSIONS,
    MENTIONS,
    NOT_FOUND,
    PERSPECTIVES,
    PLAN_SAME_AS,
    PREDICATES,
    RELATIONS,
    RULE_LINKS,
    STATES,
    TYPES,
    Changes,
    Scope,
    undo_batch,
)
from dmbot.memory.checks import HELD_TWICE, Ended, check_relation, duplicate_of, ordered
from dmbot.memory.clues import CLUE, KNOWS, NEEDS_SOMEONE, ONLY_CHARACTERS, ONLY_CLUES
from dmbot.memory.lookalikes import Name as LookAlikeName
from dmbot.memory.lookalikes import find as find_look_alikes
from dmbot.memory.lookalikes import pair as not_same_pair
from dmbot.memory.lookup import LookupData
from dmbot.memory.models import (
    AFFECTED,
    ALIAS_KINDS,
    BACKGROUND_SOURCE,
    CONFIRMED,
    CREATURE_TYPE,
    DEED,
    DEED_NAME_MAX,
    DEED_TEXT_MAX,
    DEED_VIEWERS_MAX,
    DESCRIPTION_MAX,
    DETAIL_MAX,
    DID,
    DM,
    ENDED,
    FACT_STATUSES,
    FIX,
    KEEP,
    LINE_REF_MAX,
    LINK_EDITIONS,
    LINK_KINDS,
    LINK_NAME_MAX,
    LINK_SOURCE_MAX,
    LIST_MAX,
    LIVE,
    MENTION_METHODS,
    MERGE_MANY_MAX,
    MERGED,
    PLAYER_CHARACTER,
    PROPOSED,
    REJECTED,
    ROLES,
    SOURCES,
    SPECIES,
    Alias,
    Belief,
    Correction,
    Entity,
    Flag,
    Heard,
    HeardCount,
    MemoryRuleError,
    MoreNames,
    NewName,
    Opinion,
    PeopleSuggestion,
    Perspective,
    Relation,
    RuleLink,
    SaidAsSuggestion,
    State,
    TooLateToUndo,
    Written,
    check_status_change,
    clean_text,
    days,
    is_id,
    lookup_key,
    name_key,
    new_id,
    spelling_key,
)
from dmbot.memory.ontology import (
    ACTIVE,
    CORE_PREDICATES,
    DEPRECATED,
    DM_WORD_REFUSED,
    MAX_NEW_TERMS_PER_SESSION,
    PERSON_NOT_LISTED,
    Ontology,
    PredicateTerm,
    TypeTerm,
    check_named,
    decode_not_this,
    encode_not_this,
    is_dm_word,
    resolve_kind,
)
from dmbot.memory.sounds import sound_codes
from dmbot.names import not_names

log = logging.getLogger(__name__)

PERSON = "person"
PLAYS = "plays"
# The version of the core `plays` definition, so a link made by DMbot is never behind (#1189).
PLAYS_VERSION = next(p.version for p in CORE_PREDICATES if p.key == PLAYS)
NOT_HERE = "That campaign doesn't exist in this server."
ANALYZE_AFTER_NAMES = 500  # a list this long refreshes the planner's statistics (#2376)
INT64_MAX = 2**63 - 1
# For a lookup of a few rows (one key). Reads of a whole campaign's names or facts use
# _live_ids instead: joined to a campaign with no table statistics yet (one just given
# a long list), Postgres compared every name with every entry, 1.4 s for 2,000 (#598).
_LIVE_ENTITY_IDS = (
    " (SELECT id FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
    " AND status IN ('proposed', 'confirmed'))"
)


async def _live_ids(scope: Scope, among: Collection[str] | None = None) -> set[str]:
    """The campaign's live entries (proposed or confirmed), or those of `among`, to filter
    its names and facts by in Python: plain reads, never a join that can go quadratic
    (#598). Read in the same snapshot as the rows they filter."""
    if among is not None:
        cur = await scope.conn.execute(
            "SELECT id FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
            " AND status IN ('proposed', 'confirmed') AND id = ANY(%s)",
            (*scope.ids, sorted(among)),
        )
    else:
        cur = await scope.conn.execute(
            "SELECT id FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
            " AND status IN ('proposed', 'confirmed')",
            scope.ids,
        )
    return {str(r["id"]) for r in await cur.fetchall()}


PARTY_NAME = "The party"
LED_TO_KINDS = "A clue, an event or a thread can lead to a thread or an event."
LED_TO_CONFIRM = "One of them isn't confirmed yet. Confirm it, then try again."
LED_TO_LOOP = "That would make a loop: the other one already led here."
DUE_MAX = 120  # letters in a promise's due note
THREAD_KINDS_ONLY = "A thread is a quest, a mystery, a hook, a goal or a promise."
STANDING_VIEWERS = ("character", "faction", "place")
STANDING_SUBJECTS = ("character", "faction", "people")
STANDING_BETWEEN = (
    "Standing is between a character, a group or a place and a character, a group or a people."
)
ATTITUDE_BETWEEN = "An attitude is a group's or a place's feeling toward a whole people."

# ---- conversions ---------------------------------------------------------------------


def _entity(r: dict[str, Any]) -> Entity:
    return Entity(
        r["id"], r["type"], r["name"], r["description"], r["status"], r["merged_into"],
        r["source"], r["created_at"], r["played_by"], r["role"], r["needs_look"], r["unique_item"],
        r["audience_limited"], r["party"], r["hidden_from_players"], r["made_before_name_gate"],
        r["manipulated_by"],
    )  # fmt: skip


def _rule_link(r: dict[str, Any]) -> RuleLink:
    return RuleLink(
        r["id"], r["entity_id"], r["kind"], r["rules_source"], r["name"], r["edition"], r["known"],
        r["created_at"],
    )  # fmt: skip


SAID_AS_MAX = 80  # characters of a spelling for the narrator


def _alias(r: dict[str, Any]) -> Alias:
    return Alias(
        r["id"], r["entity_id"], r["text"], r["key"], r["kind"], r["used_by"], r["secret"],
        r["status"], tuple(r["sound_codes"]), r["source"], r["created_at"], r["said_as"],
    )  # fmt: skip


def _relation(r: dict[str, Any]) -> Relation:
    return Relation(
        r["id"], r["subject_id"], r["predicate"], r["object_id"], r["detail"],
        float(r["confidence"]), r["status"], r["source"], tuple(r["mention_ids"]),
        r["from_session_at"], r["to_session_at"], r["from_game_time"], r["to_game_time"],
        r["secret"], r["created_at"], r["definition_version"], r["unrevealed"],
    )  # fmt: skip


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
        "definition_version": rel.definition_version,
        "unrevealed": rel.unrevealed,
    }


def _correction(r: dict[str, Any]) -> Correction:
    return Correction(
        r["id"], r["heard"], r["heard_key"], r["entity_id"], r["action"], r["source"],
        r["created_at"],
    )  # fmt: skip


def _flag(r: dict[str, Any]) -> Flag:
    return Flag(r["id"], r["kind"], r["relation_id"], r["other_id"], r["status"], r["created_at"])


def _type_term(r: dict[str, Any]) -> TypeTerm:
    return TypeTerm(
        r["key"], r["parent"], r["label"], r["description"], False, r["status"], r["replaced_by"]
    )


def _predicate_term(r: dict[str, Any]) -> PredicateTerm:
    return PredicateTerm(
        r["key"], r["label"], r["description"], tuple(r["subject_types"]),
        tuple(r["object_types"]), r["is_symmetric"], r["max_per_subject"],
        tuple(r["conflicts_with"]), r["parent"], False, r["status"], r["replaced_by"],
        tuple(r["covers"]), decode_not_this(r["not_this"]), tuple(r["examples"]),
        r["inverse_label"], tuple(r["synonyms"]), r["version"],
    )  # fmt: skip


# ---- checks on arguments -------------------------------------------------------------


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


def _check_list(values: Sequence[str], what: str) -> None:
    if len(values) > LIST_MAX or any(not 0 < len(v) <= DESCRIPTION_MAX for v in values):
        raise MemoryRuleError(f"Too many {what} (at most {LIST_MAX}).")


def _stronger(status_a: str, status_b: str) -> str:
    """The status a merged or repeated fact keeps: confirmed beats proposed beats rejected."""
    order = {REJECTED: 0, PROPOSED: 1, CONFIRMED: 2}
    return max(status_a, status_b, key=lambda s: order[s])


DAY = 24 * 60 * 60
PEOPLE_CACHE_MAX = 512  # campaigns whose who-is-who is kept
YIELD_AFTER_S = 0.015  # the flag check gives the event loop a turn this often


@dataclasses.dataclass(frozen=True, slots=True)
class PersonAdded:
    """What a player or DM row of a names list did (#1559)."""

    names_added: int
    linked: tuple[str, ...]  # characters now theirs
    flagged: tuple[str, ...]  # characters named but not linked: played by someone else, or unknown


def hidden_old_pronoun(entity: Entity) -> bool:
    """A suggestion from before the pronoun gate that is a pronoun: never offered (#2358)."""
    return (
        entity.status == PROPOSED
        and entity.source != DM
        and entity.made_before_name_gate
        and not_names.is_not_name(entity.name)
    )


class MemoryStore:
    """Reads and writes one campaign's memory at a time. See the module docstring."""

    def __init__(
        self, db: Database, *, clock: Callable[[], float] = time.time, keep_days: int = 30
    ) -> None:
        self._db = db
        self._clock = clock
        self.keep_days = keep_days  # how long Undo works: older change-log rows are pruned
        # The memory version each campaign's flags were last checked at (#347): nothing
        # changed since, nothing to check. Only in this process; a restart checks again.
        # A deleted campaign's entry stays: a few bytes, and never matched again.
        self._flags_checked: dict[tuple[int, str], int] = {}
        # Who is who, by campaign, at the memory version it was read at (#1208): the sidebar
        # and every table question ask for it, and it only changes with the campaign's memory.
        # Bounded: the oldest entry goes past PEOPLE_CACHE_MAX campaigns.
        self._people: dict[tuple[int, str], tuple[int, list[tuple[int, list[str], list[str]]]]] = {}
        # The look-alike groups of a campaign (#2426), kept while its names are the same: the
        # names panel asks every time a DM opens it, and finding them takes a moment.
        self._look_alikes: dict[
            tuple[int, str], tuple[tuple[int, int, int], list[list[LookAlikeName]]]
        ] = {}

    @asynccontextmanager
    async def _write(
        self, guild_id: int, campaign_id: str, source: str, *, undoes: int | None = None
    ) -> AsyncIterator[Changes]:
        if source not in SOURCES:
            raise ValueError(f"Unknown memory source: {source!r}")
        async with self._db.guild(guild_id) as conn:
            # Changes to one campaign's memory happen one at a time, so versions never
            # clash. NO KEY UPDATE still lets other tables add rows that link to the
            # campaign (a session starting) while a long write runs.
            cur = await conn.execute(
                "SELECT memory_version FROM campaigns WHERE guild_id = %s AND id = %s"
                " FOR NO KEY UPDATE",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise MemoryRuleError(NOT_HERE)
            start = int(row["memory_version"])
            changes = Changes(
                conn, guild_id, campaign_id, start, source=source, now=int(self._clock()),
                undoes=undoes,
            )  # fmt: skip
            yield changes
            if changes.version != start:
                await conn.execute(
                    "UPDATE campaigns SET memory_version = %s WHERE guild_id = %s AND id = %s",
                    (changes.version, guild_id, campaign_id),
                )
                names_changed = bool(changes.tables & notify.LOOKUP_TABLES)
                kinds_changed = bool(changes.tables & notify.KIND_TABLES)
                await notify.send(conn, campaign_id, changes.version, names_changed, kinds_changed)

    @asynccontextmanager
    async def _read(
        self, guild_id: int, campaign_id: str, *, snapshot: bool = False
    ) -> AsyncIterator[Scope]:
        async with self._db.guild(guild_id, snapshot=snapshot) as conn:
            cur = await conn.execute(
                "SELECT memory_version FROM campaigns WHERE guild_id = %s AND id = %s",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise MemoryRuleError(NOT_HERE)
            yield Scope(conn, guild_id, campaign_id, int(row["memory_version"]))

    def listen(
        self, channel: str, on_listening: Callable[[], None] | None = None
    ) -> AsyncGenerator[str, None]:
        """Change notifications (for `LookupCache.follow`): see `Database.listen`."""
        return self._db.listen(channel, on_listening)

    # ---- reads -------------------------------------------------------------------------

    async def version(self, guild_id: int, campaign_id: str) -> int:
        async with self._read(guild_id, campaign_id) as scope:
            return scope.version

    async def ontology(self, guild_id: int, campaign_id: str) -> Ontology:
        async with self._read(guild_id, campaign_id) as scope:
            return await _load_ontology(scope)

    async def entity(self, guild_id: int, campaign_id: str, entity_id: str) -> Entity | None:
        async with self._read(guild_id, campaign_id) as scope:
            row = await scope.get(ENTITIES, entity_id, lock=False)
            return None if row is None else _entity(row)

    async def defer_name(self, guild_id: int, campaign_id: str, entity_id: str) -> None:
        """The DM pressed "Later" on a suggested name (#2174): it stays in the list but stops
        holding a recap back. Only a name that is still a suggestion; a repeat does nothing."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO name_later (guild_id, campaign_id, entity_id, created_at)"
                " SELECT guild_id, campaign_id, id, %s FROM memory_entities"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND status = %s"
                " ON CONFLICT DO NOTHING",
                (int(self._clock()), guild_id, campaign_id, entity_id, PROPOSED),
            )

    async def deferred_names(self, guild_id: int, campaign_id: str) -> set[str]:
        """The suggested names the DM said "Later" to."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT entity_id FROM name_later WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            return {str(r["entity_id"]) for r in await cur.fetchall()}

    async def entities(
        self, guild_id: int, campaign_id: str, *, statuses: Sequence[str] = LIVE
    ) -> list[Entity]:
        async with self._read(guild_id, campaign_id) as scope:
            rows = await scope.select(
                ENTITIES, " AND status = ANY(%s) ORDER BY name, id", [list(statuses)]
            )
            found = [_entity(r) for r in rows]
        # Suggestions made before pronouns were kept out ("We", "I'll") are still stored; they
        # are not offered again (#2358). Later ones passed the gate when they were made (a naming
        # cue let "We" through), so only the old ones, marked by the database, are looked at
        # again. A name the DM added or confirmed is never hidden.
        return [e for e in found if not hidden_old_pronoun(e)]

    async def aliases(
        self,
        guild_id: int,
        campaign_id: str,
        *,
        entity_id: str | None = None,
        include_secret: bool = False,
    ) -> list[Alias]:
        """Aliases of live entries, not rejected. Secret ones (DM-only identities) only
        when asked for."""
        async with self._read(guild_id, campaign_id, snapshot=True) as scope:
            rows = await scope.select(
                ALIASES,
                " AND status <> 'rejected' AND (%s::text IS NULL OR entity_id = %s)"
                " AND (%s OR NOT secret) ORDER BY key, id",
                [entity_id, entity_id, include_secret],
            )
            live = await _live_ids(scope, None if entity_id is None else [entity_id])
            return [_alias(r) for r in rows if r["entity_id"] in live]

    async def confirmed_name_for(self, guild_id: int, campaign_id: str, key: str) -> str | None:
        """The confirmed entry already called `key` by a name everyone may know (not a
        secret one), if any: its name. Look-ups by key and by ID, never every name of the
        campaign (#580); like `aliases`, the first such name by its ID."""
        # No ORDER BY and one ID at a time: a campaign with no table statistics yet got
        # plans that read every name, or every entry, of the campaign instead (#580 perf).
        # The few rows are sorted here (IDs are lowercase hex, so the same order).
        async with self._read(guild_id, campaign_id, snapshot=True) as scope:
            cur = await scope.conn.execute(
                "SELECT id, entity_id FROM memory_aliases WHERE guild_id = %s"
                " AND campaign_id = %s AND key = %s AND status = 'confirmed' AND NOT secret",
                (*scope.ids, key),
            )
            rows = sorted((str(r["id"]), str(r["entity_id"])) for r in await cur.fetchall())
            for entity_id in dict.fromkeys(e for _, e in rows):
                cur = await scope.conn.execute(
                    "SELECT name FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
                    " AND id = %s AND status = 'confirmed'",
                    (*scope.ids, entity_id),
                )
                if row := await cur.fetchone():
                    return str(row["name"])
        return None

    async def sound_alikes(
        self, guild_id: int, campaign_id: str, codes: Sequence[str], *, limit: int = 500
    ) -> list[tuple[str, str, str]]:
        """Confirmed, non-secret names of confirmed entries that share a sound code with
        `codes`, as (entity ID, name as written, the entry's own name): one small query,
        for matching a suggested name without loading the whole campaign (#394)."""
        if not codes:
            return []
        # Two plain reads, in one snapshot: joined, a campaign with no table statistics
        # yet got a plan comparing every name with every entry, 2.6 s (#598).
        async with self._read(guild_id, campaign_id, snapshot=True) as scope:
            cur = await scope.conn.execute(
                "SELECT entity_id, text FROM memory_aliases"
                " WHERE guild_id = %s AND campaign_id = %s AND status = 'confirmed'"
                " AND NOT secret AND sound_codes && %s::text[] ORDER BY entity_id, id",
                (*scope.ids, list(codes)),
            )
            found = await cur.fetchall()
            cur = await scope.conn.execute(
                "SELECT id, name FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
                " AND status = 'confirmed' AND id = ANY(%s)",
                (*scope.ids, sorted({r["entity_id"] for r in found})),
            )
            names = {str(r["id"]): str(r["name"]) for r in await cur.fetchall()}
        out = [
            (r["entity_id"], r["text"], names[r["entity_id"]])
            for r in found
            if r["entity_id"] in names
        ]
        return out[:limit]

    async def relations(
        self,
        guild_id: int,
        campaign_id: str,
        *,
        entity_id: str | None = None,
        confirmed_only: bool = False,
        include_secret: bool = False,
    ) -> list[Relation]:
        """Facts not rejected, between live entries. Helpers that record story (NPC
        tracker, PlotBot) pass `confirmed_only=True`; secret facts only when asked for."""
        statuses = [CONFIRMED] if confirmed_only else list(LIVE)
        async with self._read(guild_id, campaign_id, snapshot=True) as scope:
            rows = await scope.select(
                RELATIONS,
                " AND status = ANY(%s) AND (%s::text IS NULL OR %s IN (subject_id, object_id))"
                " AND (%s OR (NOT secret AND NOT unrevealed)) ORDER BY created_at, id",
                [statuses, entity_id, entity_id, include_secret],
            )
            ends = {r[end] for r in rows for end in ("subject_id", "object_id")}
            live = await _live_ids(scope, None if entity_id is None else ends)
            return [
                _relation(r) for r in rows if r["subject_id"] in live and r["object_id"] in live
            ]

    async def lookup_data(self, guild_id: int, campaign_id: str) -> LookupData:
        """Everything the in-memory lookup needs, read at one moment (so a write landing
        part-way can't give a half-updated copy)."""
        async with self._db.guild(guild_id, snapshot=True) as conn:
            cur = await conn.execute(
                "SELECT memory_version FROM campaigns WHERE guild_id = %s AND id = %s",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise MemoryRuleError(NOT_HERE)
            scope = Scope(conn, guild_id, campaign_id, int(row["memory_version"]))
            # The table's people are left out of the lookup: a placeholder like "Player
            # 1234" must never be matched in speech or turned into a fix (#1188; their
            # real names come with part 2).
            entities = await scope.select(
                ENTITIES,
                " AND status IN ('proposed', 'confirmed') AND type <> 'person' ORDER BY id",
            )
            # Names and facts of live entries, filtered here against the entries just
            # read (same snapshot): plain reads, never a join that can go quadratic (#598).
            live = {str(r["id"]) for r in entities}
            aliases = [
                r
                for r in await scope.select(ALIASES, " AND status <> 'rejected' ORDER BY id")
                if r["entity_id"] in live
            ]
            # The table's people, apart from the story's entries: only the names the DM
            # confirmed, never the placeholder `Player 1234` (the entry's own name).
            persons = {
                str(r["id"]): r
                for r in await scope.select(
                    ENTITIES, " AND type = 'person' AND status = 'confirmed'"
                )
            }
            people_aliases = [
                r
                for r in await scope.select(
                    ALIASES, " AND status = 'confirmed' AND NOT secret ORDER BY id"
                )
                if r["entity_id"] in persons and r["text"] != persons[r["entity_id"]]["name"]
            ]
            corrections = await scope.select(CORRECTIONS, " ORDER BY id")
            relations = [
                r
                for r in await scope.select(
                    RELATIONS,
                    # a proposed one is shown while it is not secret; a confirmed one when the
                    # players hold it as fact (their belief, #2409)
                    " AND ((status = 'proposed' AND NOT secret AND NOT unrevealed)"
                    f" OR (status = 'confirmed' AND {beliefs.party_knows()})) ORDER BY id",
                )
                if r["subject_id"] in live and r["object_id"] in live
            ]
            # The DM's confirmed secret connections, apart from the rest: only a DM's view of the
            # name dropdown's order reads them (#2321).
            secret_relations = [
                r
                for r in await scope.select(
                    RELATIONS, " AND status = 'confirmed' AND (secret OR unrevealed) ORDER BY id"
                )
                if r["subject_id"] in live and r["object_id"] in live
            ]
            # Counts follow merges (a merged-away name counts for the one it became).
            cur = await conn.execute(
                "SELECT coalesce(e.merged_into, h.entity_id) AS entity_id,"
                " sum(h.times) AS times, max(h.session_started_at) AS last_at"
                " FROM memory_heard h JOIN memory_entities e ON e.guild_id = h.guild_id"
                " AND e.campaign_id = h.campaign_id AND e.id = h.entity_id"
                " WHERE h.guild_id = %s AND h.campaign_id = %s GROUP BY 1",
                scope.ids,
            )
            heard = tuple(
                HeardCount(r["entity_id"], int(r["times"]), row_int(r, "last_at"))
                for r in await cur.fetchall()
            )
            cur = await conn.execute(
                "SELECT DISTINCT session_started_at FROM memory_heard"
                " WHERE guild_id = %s AND campaign_id = %s"
                " ORDER BY session_started_at DESC LIMIT 2",
                scope.ids,
            )
            recent = tuple(int(r["session_started_at"]) for r in await cur.fetchall())
            links = [
                r
                for r in await scope.select(RULE_LINKS, " ORDER BY created_at, id")
                if r["entity_id"] in live
            ]
            return LookupData(
                scope.version,
                tuple(_entity(r) for r in entities),
                tuple(_alias(r) for r in aliases),
                tuple(_correction(r) for r in corrections),
                tuple(_relation(r) for r in relations),
                heard,
                recent,
                tuple(_rule_link(r) for r in links),
                tuple(_alias(r) for r in people_aliases),
                tuple(_relation(r) for r in secret_relations),
            )

    async def corrections(self, guild_id: int, campaign_id: str) -> list[Correction]:
        async with self._read(guild_id, campaign_id) as scope:
            rows = await scope.select(CORRECTIONS, " ORDER BY heard_key, id")
            return [_correction(r) for r in rows]

    async def flags(self, guild_id: int, campaign_id: str, *, open_only: bool = True) -> list[Flag]:
        async with self._read(guild_id, campaign_id) as scope:
            rows = await scope.select(
                FLAGS, " AND (NOT %s OR status = 'open') ORDER BY created_at, id", [open_only]
            )
            return [_flag(r) for r in rows]

    # ---- states and one-of-a-kind items (#1267) ------------------------------------------

    async def states(
        self, guild_id: int, campaign_id: str, entity_ids: Collection[str] | None = None
    ) -> dict[str, State]:
        """The current condition of every entry that has one (or of those asked for)."""
        async with self._read(guild_id, campaign_id) as scope:
            if entity_ids is None:
                rows = await scope.select(STATES, " AND closed_at IS NULL")
                return {r["entity_id"]: states.state_of(r) for r in rows}
            return await states.current_states(scope, entity_ids)

    async def state_history(self, guild_id: int, campaign_id: str, entity_id: str) -> list[State]:
        """Everything an entry has been, oldest first: when it changed and who said so."""
        async with self._read(guild_id, campaign_id) as scope:
            rows = await scope.select(
                STATES, " AND entity_id = %s ORDER BY created_at, id", [entity_id]
            )
            return [states.state_of(r) for r in rows]

    async def set_condition(
        self,
        guild_id: int,
        campaign_id: str,
        entity_id: str,
        condition: str,
        *,
        source: str,
        from_session_at: int | None = None,
    ) -> Written[tuple[State | None, list[Flag]]]:
        """Say what a character, item or place is now (alive, dead, intact, destroyed…); the
        old condition closes and stays as history. The way back from dead or destroyed is the
        DM's alone. A fact that a dead or destroyed entry acts in after it ended is flagged
        for the DM, who can answer "brought back" by setting the condition again."""
        async with self._write(guild_id, campaign_id, source) as w:
            value = await self.set_condition_in(w, entity_id, condition, from_session_at)
            return Written(value, w.batch)

    async def set_condition_in(
        self, w: Changes, entity_id: str, condition: str, from_session_at: int | None = None
    ) -> tuple[State | None, list[Flag]]:
        """`set_condition` inside a write that is already open, so a caller can make other
        changes in the same transaction (#1301): all of them happen, or none. The caller opened
        `w` for the right campaign and source."""
        onto = await _load_ontology(w)
        entity = await _entity_row(w, entity_id)
        new = await states.set_condition(
            w, onto, entity, condition, from_session_at=from_session_at
        )
        if new is None:
            return None, []
        touching = [
            _relation(r)
            for r in await w.select(
                RELATIONS,
                " AND (subject_id = %s OR object_id = %s) AND status <> 'rejected'"
                ' ORDER BY id COLLATE "C"',
                [entity_id, entity_id],
            )
        ]
        await states.close_resolved_after_end(w, onto, touching)
        ended = (
            {entity_id: Ended(new.from_session_at, new.created_at)}
            if new.condition in ENDED
            else {}
        )
        rows = await states.flag_acting_after_end(w, onto, touching, ended)
        return new, [_flag(r) for r in rows]

    async def set_unique(
        self, guild_id: int, campaign_id: str, entity_id: str, unique: bool, *, source: str
    ) -> Written[tuple[Entity, list[Flag]]]:
        """Say whether only one of this item exists (Heartseeker, not a longbow): the DM's
        call. Switching it on flags a unique item that two are already carrying."""
        if source != DM:
            raise MemoryRuleError("Only the DM can say that.")
        async with self._write(guild_id, campaign_id, source) as w:
            onto = await _load_ontology(w)
            current = await _entity_row(w, entity_id)
            if not onto.is_a(current["type"], "item"):
                raise MemoryRuleError(states.ONLY_ITEMS)
            row = await w.update(ENTITIES, entity_id, {"unique_item": unique})
            flags: list[Flag] = []
            if not unique:  # the alerts about this item's holders no longer hold
                held = [
                    r["id"] for r in await w.select(RELATIONS, " AND object_id = %s", [entity_id])
                ]
                for flag in await w.select(
                    FLAGS,
                    " AND kind = %s AND status = 'open' AND relation_id = ANY(%s)",
                    [HELD_TWICE, held],
                ):
                    await w.update(FLAGS, flag["id"], {"status": "resolved"})
            if unique:
                limited = sorted(k for k, t in onto.predicates.items() if t.max_per_object)
                carried = {
                    r.id: r
                    for r in (
                        _relation(r)
                        for r in await w.select(
                            RELATIONS,
                            " AND object_id = %s AND status <> 'rejected'"
                            ' AND predicate = ANY(%s) ORDER BY id COLLATE "C"',
                            [entity_id, limited],
                        )
                    )
                }
                for rel in carried.values():
                    subject = await _entity_row(w, rel.subject_id)
                    for problem in check_relation(
                        onto, rel, subject["type"], current["type"], list(carried.values()),
                        object_unique=True,
                    ):  # fmt: skip
                        other = carried.get(problem.other_id or "")
                        # Each pair once: the later fact is the one flagged.
                        if (
                            problem.kind == HELD_TWICE
                            and other is not None
                            and (rel.created_at, rel.id) > (other.created_at, other.id)
                        ):
                            made = await states.flag_new(w, rel, problem.kind, other.id)
                            if made is not None:
                                flags.append(_flag(made))
            return Written((_entity(row), flags), w.batch)

    # ---- story threads the DM keeps (#1662) ----

    async def is_player_here(self, guild_id: int, campaign_id: str, user_id: int) -> bool:
        """Whether this person is tied to this campaign (#1666): the player of a confirmed
        character in it, or heard saying a known name in one of its sessions. A merged or
        proposed character does not count, nor does the person's own entry that DMbot makes when a
        character is linked. Nothing about what was said, only that they were here."""
        async with self._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "SELECT EXISTS (SELECT 1 FROM memory_entities WHERE guild_id = %s"
                " AND campaign_id = %s AND played_by = %s AND type = 'character'"
                " AND status = 'confirmed')"
                " OR EXISTS (SELECT 1 FROM memory_heard WHERE guild_id = %s"
                " AND campaign_id = %s AND speaker_id = %s) AS here",
                (*scope.ids, user_id, *scope.ids, user_id),
            )
            row = await cur.fetchone()
            return bool(row and row["here"])

    async def threads(
        self, guild_id: int, campaign_id: str, *, stalled_after: int = threads_mod.STALLED_AFTER
    ) -> list[threads_mod.ThreadInfo]:
        """Every confirmed story thread with its state and history, who is involved, what moved it
        and whether it is stalled (worked out here, never stored), read at one moment. Stalled
        threads first, then by name."""
        async with self._read(guild_id, campaign_id, snapshot=True) as scope:
            return await _threads(scope, stalled_after)

    async def new_thread(
        self,
        guild_id: int,
        campaign_id: str,
        name: str,
        subtype: str,
        state: str,
        *,
        source: str,
        from_session_at: int | None = None,
    ) -> Written[Entity]:
        """The DM starts a story thread: its name, what kind of thread (quest, mystery, hook,
        goal, promise) and where it is now, made in one change."""
        if source != DM:
            raise MemoryRuleError("Only the DM can do that.")
        check_named(subtype, name)
        name = clean_text(name)
        async with self._write(guild_id, campaign_id, source) as w:
            onto = await _load_ontology(w)
            if subtype not in onto.types or not onto.is_a(subtype, "thread"):
                raise MemoryRuleError(THREAD_KINDS_ONLY)
            onto.active_type(subtype)
            row = await w.insert(
                ENTITIES,
                {
                    "id": new_id(), "type": subtype, "name": name, "description": "",
                    "status": CONFIRMED, "merged_into": None, "source": source,
                    "created_at": w.now, "played_by": None, "role": None, "needs_look": False,
                    "unique_item": None, "audience_limited": False, "party": False,
                },
            )  # fmt: skip
            await w.insert(ALIASES, _new_alias(w, row["id"], name, "full", CONFIRMED, False, None))
            await states.set_condition(w, onto, row, state, from_session_at=from_session_at)
            return Written(_entity(row), w.batch)

    async def check_thread(
        self,
        guild_id: int,
        campaign_id: str,
        thread_id: str,
        *,
        source: str,
        from_session_at: int | None = None,
    ) -> Written[State]:
        """The DM looked at a thread and it is still going: the same state written again, dated
        (it counts as touched; the history says "checked, still going")."""
        if source != DM:
            raise MemoryRuleError("Only the DM can do that.")
        async with self._write(guild_id, campaign_id, source) as w:
            onto = await _load_ontology(w)
            entity = await _entity_row(w, thread_id)
            made = await states.touch_thread(w, onto, entity, from_session_at=from_session_at)
            return Written(made, w.batch)

    async def involve(
        self,
        guild_id: int,
        campaign_id: str,
        thread_id: str,
        who_id: str,
        role: str,
        *,
        source: str,
        from_session_at: int | None = None,
    ) -> Written[Relation]:
        """The DM says who or what has a part in a thread, and the part (quest giver, target,
        villain, victim, reward, or their own words)."""
        if source != DM:
            raise MemoryRuleError("Only the DM can do that.")
        async with self._write(guild_id, campaign_id, source) as w:
            fact, _flags = await self.add_relation_in(
                w, who_id, "in_thread", thread_id, confidence=1.0, status=CONFIRMED,
                detail=clean_text(role), from_session_at=from_session_at,
            )  # fmt: skip
            return Written(fact, w.batch)

    async def promise_party(
        self,
        guild_id: int,
        campaign_id: str,
        promise_id: str,
        who_id: str,
        *,
        to: bool,
        source: str,
        from_session_at: int | None = None,
    ) -> Written[Relation]:
        """The DM says who made a promise (`to` False) or who it was made to (`to` True): their
        word, one fact each (#1674). More than one may be named."""
        if source != DM:
            raise MemoryRuleError("Only the DM can do that.")
        async with self._write(guild_id, campaign_id, source) as w:
            entity = await _entity_row(w, promise_id)
            if entity["type"] != threads_mod.PROMISE:
                raise MemoryRuleError("That is not a promise.")
            onto = await _load_ontology(w)
            who = await _entity_row(w, who_id)
            allowed = ("character", "faction", "place") if to else ("character", "faction")
            if not any(onto.is_a(who["type"], kind) for kind in allowed):
                # (a wrong kind would only be flagged; this is the DM's own tap, so refuse it)
                raise MemoryRuleError(
                    "Pick a character, a group or a place."
                    if to
                    else "Pick a character or a group."
                )
            fact, _flags = await self.add_relation_in(
                w, who_id, "promised_to" if to else "promised_by", promise_id, confidence=1.0,
                status=CONFIRMED, from_session_at=from_session_at,
            )  # fmt: skip
            return Written(fact, w.batch)

    async def led_to(
        self,
        guild_id: int,
        campaign_id: str,
        from_id: str,
        to_id: str,
        *,
        source: str,
        from_session_at: int | None = None,
    ) -> Written[Relation]:
        """The DM says one thing led to another (#1679): a clue, event or thread led to a thread
        or event. Their word only. A link to itself or one that would close a loop (or run more
        than `CHAIN_DEPTH` steps) is refused, not flagged. The fact is kept secret, so it is
        the DM's alone wherever facts are shown."""
        if source != DM:
            raise MemoryRuleError("Only the DM can do that.")
        async with self._write(guild_id, campaign_id, source) as w:
            onto = await _load_ontology(w)
            a, b = await _entity_row(w, from_id), await _entity_row(w, to_id)
            if a["status"] != CONFIRMED or b["status"] != CONFIRMED:
                raise MemoryRuleError(LED_TO_CONFIRM)
            if not any(onto.is_a(a["type"], k) for k in ("clue", "event", "thread")) or not any(
                onto.is_a(b["type"], k) for k in ("thread", "event")
            ):
                raise MemoryRuleError(LED_TO_KINDS)
            fact, _flags = await self.add_relation_in(
                w, from_id, "led_to", to_id, confidence=1.0, status=CONFIRMED, secret=True,
                from_session_at=from_session_at,
            )  # fmt: skip
            return Written(fact, w.batch)

    async def health_counts(
        self, guild_id: int, campaign_id: str, *, since: int, recent: int
    ) -> Mapping[str, Any]:
        """The one row of the story graph's health counts (#1699, #1701). The SQL and what each
        number means are in `dmbot.memory.health`; this runs it in a read scoped to this
        campaign, so every count is this campaign's and no other's."""
        from dmbot.memory import health  # (it imports this module: loaded late)

        async with self._read(guild_id, campaign_id) as scope:
            g, c = scope.ids
            cur = await scope.conn.execute(
                health.COUNTS, {"g": g, "c": c, "since": since, "recent": recent}
            )
            row = await cur.fetchone()
        if row is None:  # (a SELECT of scalar subqueries always returns one row)
            raise RuntimeError("The health counts returned no row")
        return row

    async def chain(
        self, guild_id: int, campaign_id: str, entity_id: str
    ) -> tuple[list[Entity], list[Entity]]:
        """(what led to this, what this led to) for one entry: its live "led to" links, both
        ends confirmed, names by A to Z. One read (#1679). For a thread's own card the thread
        read carries the same; this is for clues and events."""
        async with self._read(guild_id, campaign_id) as scope:
            rows = await scope.select(
                RELATIONS,
                " AND predicate = 'led_to' AND status = 'confirmed' AND to_session_at IS NULL"
                " AND (subject_id = %s OR object_id = %s)",
                [entity_id, entity_id],
            )
            ends = sorted(
                {r["subject_id"] if r["object_id"] == entity_id else r["object_id"] for r in rows}
            )
            found = {
                r["id"]: _entity(r)
                for r in await scope.select(
                    ENTITIES, " AND id = ANY(%s) AND status = 'confirmed'", [ends]
                )
            }
        came = [
            found[r["subject_id"]]
            for r in rows
            if r["object_id"] == entity_id and r["subject_id"] in found
        ]
        went = [
            found[r["object_id"]]
            for r in rows
            if r["subject_id"] == entity_id and r["object_id"] in found
        ]

        def key(e: Entity) -> tuple[str, str]:
            return (name_key(e.name), e.id)

        return sorted(came, key=key), sorted(went, key=key)

    async def set_due(
        self, guild_id: int, campaign_id: str, promise_id: str, due: str, *, source: str
    ) -> Written[Entity]:
        """The DM's free-text note on when a promise is due ("by the full moon"), kept as the
        entry's description and never parsed into a date. Empty clears it (#1674)."""
        if source != DM:
            raise MemoryRuleError("Only the DM can do that.")
        text = " ".join(due.split())
        if len(text) > DUE_MAX:
            raise MemoryRuleError(f"Keep it to {DUE_MAX} letters or fewer.")
        async with self._write(guild_id, campaign_id, source) as w:
            entity = await _entity_row(w, promise_id)
            if entity["type"] != threads_mod.PROMISE:
                raise MemoryRuleError("That is not a promise.")
            row = await w.update(ENTITIES, promise_id, {"description": text})
            return Written(_entity(row), w.batch)

    async def link_event(
        self,
        guild_id: int,
        campaign_id: str,
        thread_id: str,
        event_id: str,
        *,
        ends_it: bool,
        source: str,
        from_session_at: int | None = None,
    ) -> Written[Relation]:
        """An event moved a thread along (advances) or ended it (resolves). The state does not
        change by itself: the DM says so."""
        if source != DM:
            raise MemoryRuleError("Only the DM can do that.")
        async with self._write(guild_id, campaign_id, source) as w:
            fact, _flags = await self.add_relation_in(
                w, event_id, "resolves" if ends_it else "advances", thread_id, confidence=1.0,
                status=CONFIRMED, from_session_at=from_session_at,
            )  # fmt: skip
            return Written(fact, w.batch)

    # ---- standing the DM sets (#1655) ----

    async def standing_index(
        self,
        guild_id: int,
        campaign_id: str,
        session_numbers: Mapping[int, int] | None = None,
    ) -> standing.Index:
        """One in-memory copy to work standing out from, read at one moment: the confirmed
        entries, the connections the ladders climb and the current impressions and attitudes. A
        later `Index.standing(a, b)` asks the database nothing."""
        async with self._read(guild_id, campaign_id, snapshot=True) as scope:
            return await _standing_index(scope, session_numbers)

    async def party(self, guild_id: int, campaign_id: str) -> Entity | None:
        async with self._read(guild_id, campaign_id) as scope:
            rows = await scope.select(ENTITIES, " AND party AND status = 'confirmed'")
        return _entity(rows[0]) if rows else None

    async def ensure_party(self, guild_id: int, campaign_id: str, *, source: str) -> Entity:
        """The campaign's party, made the first time it is needed: a group named "The party"
        (the DM can rename it) whose members are its confirmed player characters."""
        if source != DM:
            raise MemoryRuleError("Only the DM can do that.")
        async with self._write(guild_id, campaign_id, source) as w:
            found = await w.select(ENTITIES, " AND party")
            if found:
                if found[0]["status"] != CONFIRMED:  # forgotten, then wanted again
                    revived = await w.update(ENTITIES, found[0]["id"], {"status": CONFIRMED})
                    return _entity(revived)
                return _entity(found[0])
            row = await w.insert(
                ENTITIES,
                {
                    "id": new_id(), "type": "faction", "name": PARTY_NAME, "description": "",
                    "status": CONFIRMED, "merged_into": None, "source": source,
                    "created_at": w.now, "played_by": None, "role": None, "needs_look": False,
                    "unique_item": None, "audience_limited": False, "party": True,
                },
            )  # fmt: skip
            await w.insert(
                ALIASES, _new_alias(w, row["id"], PARTY_NAME, "full", CONFIRMED, False, None)
            )
            return _entity(row)

    async def add_deed(
        self,
        guild_id: int,
        campaign_id: str,
        doer_id: str,
        text: str,
        viewer_ids: Sequence[str],
        effect: str,
        *,
        source: str,
        from_session_at: int | None = None,
    ) -> Written[Entity]:
        """The DM records a deed (#1732): something a player character or the party did that
        mattered to up to five NPCs, groups or places, and how it landed (`DEED_EFFECTS`). One
        change, so one Undo takes all of it back. The deed is an entry of its own (the words are
        its description), joined to the doer by `did` and to each one it mattered to by `affected`
        with the effect as the detail. Only the DM, and nothing here reads speech."""
        if source != DM:
            raise MemoryRuleError("Only the DM can record a deed.")
        try:
            words = clean_text(text, DEED_TEXT_MAX)
        except MemoryRuleError:
            raise MemoryRuleError(
                f"Say what happened in at most {DEED_TEXT_MAX} characters."
            ) from None
        if effect not in standing.DEED_SCORE:
            raise MemoryRuleError("Pick how it landed.")
        wanted = list(dict.fromkeys(viewer_ids))
        if not wanted or len(wanted) > DEED_VIEWERS_MAX:
            raise MemoryRuleError(f"Pick one to {DEED_VIEWERS_MAX} it mattered to.")
        _check_span(from_session_at, None)
        async with self._write(guild_id, campaign_id, source) as w:
            doer = await _entity_row(w, doer_id)
            if doer["status"] != CONFIRMED or not (
                doer["party"] or doer["role"] == PLAYER_CHARACTER
            ):
                raise MemoryRuleError(ONLY_PLAYERS_DO)
            for viewer_id in wanted:
                viewer = await _entity_row(w, viewer_id)
                npc = viewer["type"] == "character" and viewer["role"] != PLAYER_CHARACTER
                if (
                    viewer["status"] != CONFIRMED
                    or viewer_id == doer_id
                    or viewer["party"]
                    or not (npc or viewer["type"] in ("faction", "place"))
                ):
                    raise MemoryRuleError(ONLY_THESE_MATTER)
            row = await w.insert(
                ENTITIES,
                {
                    "id": new_id(), "type": DEED, "name": words[:DEED_NAME_MAX],
                    "description": words, "status": CONFIRMED, "merged_into": None,
                    "source": source, "created_at": w.now, "played_by": None, "role": None,
                    "needs_look": False, "unique_item": None, "audience_limited": False,
                    "party": False,
                },
            )  # fmt: skip
            # No alias on purpose: a deed's words are not a name anyone says, so nothing at the
            # table can match it (and no word in it hides a real name, like Brindle).
            if from_session_at is None:
                # No session running: the newest one names were heard in, so a deed recorded
                # between sessions still fades like the rest.
                cur = await w.conn.execute(
                    "SELECT max(session_started_at) AS at FROM memory_heard"
                    " WHERE guild_id = %s AND campaign_id = %s",
                    w.ids,
                )
                newest = await cur.fetchone()
                from_session_at = (
                    None if newest is None or newest["at"] is None else int(newest["at"])
                )
            # Both connections are secret (the DM's alone), on top of the deed being hidden.
            await self.add_relation_in(
                w, doer_id, DID, row["id"], confidence=1.0, status=CONFIRMED, secret=True,
                from_session_at=from_session_at,
            )  # fmt: skip
            for viewer_id in wanted:
                await self.add_relation_in(
                    w, row["id"], AFFECTED, viewer_id, confidence=1.0, status=CONFIRMED,
                    detail=effect, from_session_at=from_session_at, secret=True,
                )  # fmt: skip
            return Written(_entity(row), w.batch)

    async def set_impression(
        self,
        guild_id: int,
        campaign_id: str,
        viewer_id: str,
        subject_id: str,
        step: str,
        *,
        source: str,
        from_session_at: int | None = None,
    ) -> Written[Opinion]:
        """The DM says how a viewer (an NPC, a group or a place) feels about a subject (a
        character, the party, a group or a people): one of the five steps, saved as a dated
        impression. The one before it for the pair is closed, not changed, so the history stays."""
        return await self._set_opinion(
            guild_id, campaign_id, viewer_id, subject_id, step, source, from_session_at, False
        )

    async def set_attitude(
        self,
        guild_id: int,
        campaign_id: str,
        holder_id: str,
        people_id: str,
        step: str,
        *,
        source: str,
        from_session_at: int | None = None,
    ) -> Written[Opinion]:
        """The DM says how a group or place feels about a whole people (Bryn Shander distrusts
        tabaxi): dated, with the history kept."""
        return await self._set_opinion(
            guild_id, campaign_id, holder_id, people_id, step, source, from_session_at, True
        )

    async def _set_opinion(
        self,
        guild_id: int,
        campaign_id: str,
        holder_id: str,
        target_id: str,
        step: str,
        source: str,
        from_session_at: int | None,
        attitude: bool,
    ) -> Written[Opinion]:
        if source != DM:
            raise MemoryRuleError("Only the DM can set that.")
        if step not in standing.SCORE_OF:
            raise MemoryRuleError("Pick one of the five: " + ", ".join(standing.STEPS) + ".")
        if holder_id == target_id:
            raise MemoryRuleError("That needs two different names.")
        table = ATTITUDES if attitude else IMPRESSIONS
        held, aimed = ("holder_id", "toward_id") if attitude else ("viewer_id", "subject_id")
        async with self._write(guild_id, campaign_id, source) as w:
            a = await _entity_row(w, holder_id)
            b = await _entity_row(w, target_id)
            kinds = (a["type"], b["type"])
            if attitude:
                if a["type"] not in ("faction", "place") or b["type"] != "people":
                    raise MemoryRuleError(ATTITUDE_BETWEEN)
            elif a["type"] not in STANDING_VIEWERS or b["type"] not in STANDING_SUBJECTS:
                raise MemoryRuleError(STANDING_BETWEEN)
            if "person" in kinds:
                raise MemoryRuleError(STANDING_BETWEEN)
            score = standing.SCORE_OF[step]
            old = await w.select(
                table, f" AND {held} = %s AND {aimed} = %s AND closed_at IS NULL",
                [holder_id, target_id],
            )  # fmt: skip
            if old and old[0]["score"] == score and old[0]["weight"] == standing.DM_WEIGHT:
                return Written(_opinion(old[0], attitude), w.batch)  # already so
            for row in old:
                await w.update(table, row["id"], {"closed_at": w.now})
            made = await w.insert(
                table,
                {
                    "id": new_id(), held: holder_id, aimed: target_id, "score": score,
                    "weight": standing.DM_WEIGHT, "source": source,
                    "from_session_at": from_session_at, "created_at": w.now, "closed_at": None,
                },
            )  # fmt: skip
            return Written(_opinion(made, attitude), w.batch)

    async def opinion_history(
        self, guild_id: int, campaign_id: str, holder_id: str, target_id: str, *, attitude: bool
    ) -> list[Opinion]:
        """Every impression (or attitude) for a pair, oldest first, the current one last."""
        table = ATTITUDES if attitude else IMPRESSIONS
        held, aimed = ("holder_id", "toward_id") if attitude else ("viewer_id", "subject_id")
        async with self._read(guild_id, campaign_id) as scope:
            rows = await scope.select(
                table,
                f" AND {held} = %s AND {aimed} = %s"
                " ORDER BY created_at, COALESCE(closed_at, 9223372036854775807), id",
                [holder_id, target_id],
            )  # fmt: skip
        return [_opinion(r, attitude) for r in rows]

    async def find_or_add_people(
        self, guild_id: int, campaign_id: str, name: str, *, source: str
    ) -> Entity:
        """The `people` entry called this, made on the spot if there is none (Set attitude…):
        found and made in one write, so two quick presses make one. A waiting one is confirmed,
        since the DM is the one naming it."""
        if source != DM:
            raise MemoryRuleError("Only the DM can do that.")
        check_named("people", name)
        name = clean_text(name)
        async with self._write(guild_id, campaign_id, source) as w:
            key = name_key(name)
            for r in await w.select(
                ENTITIES, " AND type = 'people' AND status IN ('proposed', 'confirmed')"
            ):
                if name_key(r["name"]) == key:
                    if r["status"] != CONFIRMED:
                        r = await w.update(ENTITIES, r["id"], {"status": CONFIRMED})
                    return _entity(r)
            row = await w.insert(
                ENTITIES,
                {
                    "id": new_id(), "type": "people", "name": name, "description": "",
                    "status": CONFIRMED, "merged_into": None, "source": source,
                    "created_at": w.now, "played_by": None, "role": None, "needs_look": False,
                    "unique_item": None, "audience_limited": False, "party": False,
                },
            )  # fmt: skip
            await w.insert(ALIASES, _new_alias(w, row["id"], name, "full", CONFIRMED, False, None))
            return _entity(row)

    # ---- a clue only some people know (#1649) ----

    async def clue_audience(self, guild_id: int, campaign_id: str, clue_id: str) -> list[Entity]:
        """Who knows this clue: the characters with a confirmed `knows` fact to it that is still
        true, by name. A missing fact means DMbot doesn't know, never that they don't."""
        async with self._read(guild_id, campaign_id, snapshot=True) as scope:
            return await _audience(scope, clue_id)

    async def set_hidden_from_players(
        self, guild_id: int, campaign_id: str, entity_id: str, hidden: bool, *, source: str
    ) -> Written[Entity]:
        """The DM's switch "Players don't know this one yet" (#2321), on any entry: on, the entry
        is the DM's alone like a clue only some know; off ("Players know it now"), everyone
        sees it. Not a clue's audience (`set_audience_limited`), which has its own rules."""
        if source != DM:
            raise MemoryRuleError("Only the DM can say that.")
        async with self._write(guild_id, campaign_id, source) as w:
            current = await _entity_row(w, entity_id)
            if hidden and (
                current["type"] == PERSON
                or current["party"]
                or current["role"] == PLAYER_CHARACTER
                or current["played_by"] is not None
            ):  # a player's character is named in the transcript file and the table list
                raise MemoryRuleError("A player's character can't be hidden from the players.")
            row = await w.update(ENTITIES, entity_id, {"hidden_from_players": hidden})
            return Written(_entity(row), w.batch)

    async def set_audience_limited(
        self, guild_id: int, campaign_id: str, clue_id: str, limited: bool, *, source: str
    ) -> Written[Entity]:
        """The DM's switch "Only some people know this". Turned on only with at least one
        person listed, because a limited clue nobody is listed for would warn about everyone."""
        if source != DM:
            raise MemoryRuleError("Only the DM can say that.")
        async with self._write(guild_id, campaign_id, source) as w:
            current = await _entity_row(w, clue_id)
            if current["type"] != CLUE:
                raise MemoryRuleError(ONLY_CLUES)
            if limited and not await _audience(w, clue_id):
                raise MemoryRuleError(NEEDS_SOMEONE)
            row = await w.update(ENTITIES, clue_id, {"audience_limited": limited})
            return Written(_entity(row), w.batch)

    async def add_knower(
        self,
        guild_id: int,
        campaign_id: str,
        clue_id: str,
        character_id: str,
        *,
        source: str,
        from_session_at: int | None = None,
    ) -> Written[Relation]:
        """The DM says a character knows this clue: a confirmed, dated `knows` fact. It works
        for any clue; the switch decides whether anyone is warned."""
        if source != DM:
            raise MemoryRuleError("Only the DM can say that.")
        async with self._write(guild_id, campaign_id, source) as w:
            return Written(
                await self.add_knower_in(w, clue_id, character_id, from_session_at),
                w.batch,
            )

    async def add_knower_in(
        self, w: Changes, clue_id: str, character_id: str, from_session_at: int | None
    ) -> Relation:
        """`add_knower` inside a write that is already open (a continuity answer's, #1649)."""
        clue = await _entity_row(w, clue_id)
        who = await _entity_row(w, character_id)
        if clue["type"] != CLUE:
            raise MemoryRuleError(ONLY_CLUES)
        if who["type"] != "character":
            raise MemoryRuleError(ONLY_CHARACTERS)
        fact, _flags = await self.add_relation_in(
            w, character_id, KNOWS, clue_id, confidence=1.0, status=CONFIRMED,
            from_session_at=from_session_at,
        )  # fmt: skip
        if fact.to_session_at is not None:  # they knew once and it was ended: they know it now
            row = await w.update(RELATIONS, fact.id, {"to_session_at": None})
            return _relation(row)
        return fact

    async def remove_knower(
        self, guild_id: int, campaign_id: str, clue_id: str, character_id: str, *, source: str
    ) -> Written[tuple[Entity, bool]]:
        """Take a character off a clue's list (their `knows` fact is removed, like any
        connection, and Undo works). Returns the clue and whether the switch turned itself off
        because nobody is left."""
        if source != DM:
            raise MemoryRuleError("Only the DM can say that.")
        async with self._write(guild_id, campaign_id, source) as w:
            clue = await _entity_row(w, clue_id)
            facts = await w.select(
                RELATIONS,
                " AND subject_id = %s AND predicate = %s AND object_id = %s"
                " AND status <> 'rejected'",
                [character_id, KNOWS, clue_id],
            )
            if not facts:
                raise MemoryRuleError(NOT_FOUND)
            for fact in facts:
                await w.update(RELATIONS, fact["id"], {"status": REJECTED})
            turned_off = False
            if clue["audience_limited"] and not await _audience(w, clue_id):
                clue = await w.update(ENTITIES, clue_id, {"audience_limited": False})
                turned_off = True
            return Written((_entity(clue), turned_off), w.batch)

    async def resolve(self, guild_id: int, campaign_id: str, entity_id: str) -> Entity | None:
        """The entity an ID stands for now, following merges."""
        async with self._read(guild_id, campaign_id) as scope:
            seen: set[str] = set()
            current: str | None = entity_id
            while current is not None and current not in seen:
                seen.add(current)
                row = await scope.get(ENTITIES, current, lock=False)
                if row is None:
                    return None
                if row["status"] != MERGED:
                    return _entity(row)
                current = row["merged_into"]
            return None

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
        played_by: int | None = None,
        role: str | None = None,
        hidden_from_players: bool = False,
    ) -> Written[Entity]:
        """A new person, place or thing, with its name as the first alias. `role`: for a
        character, player character, NPC or god (the older kinds npc, player_character,
        deity and creature are read as a character with that role). `played_by`: the
        Discord user playing a player character. `hidden_from_players`: the DM's "Players don't
        know this one yet" (#2321), theirs to say."""
        if hidden_from_players and source != DM:
            raise MemoryRuleError("Only the DM can say that.")
        check_named(type, name)
        type, role = resolve_kind(type, role)
        if hidden_from_players and (role == PLAYER_CHARACTER or played_by is not None):
            raise MemoryRuleError("A player's character can't be hidden from the players.")
        if type == PERSON:
            raise MemoryRuleError(PERSON_NOT_LISTED)  # only DMbot makes these
        _check_choice(status, LIVE, "entity status")
        check_status_change(None, status, source)
        name = clean_text(name)
        if len(description) > DESCRIPTION_MAX:
            raise MemoryRuleError(f"That's too long (at most {DESCRIPTION_MAX} characters).")
        if played_by is not None and not 0 < played_by <= INT64_MAX:
            raise ValueError("Bad Discord user ID")
        async with self._write(guild_id, campaign_id, source) as w:
            onto = await _load_ontology(w)
            onto.active_type(type)
            _check_role(onto, type, role, played_by)
            row = await w.insert(
                ENTITIES,
                {
                    "id": new_id(),
                    "type": type,
                    "name": name,
                    "description": description.strip(),
                    "status": status,
                    "merged_into": None,
                    "source": source,
                    "created_at": w.now,
                    "played_by": played_by,
                    "role": role,
                    "needs_look": False,
                    "unique_item": None,
                    "hidden_from_players": hidden_from_players,
                },
            )
            await w.insert(ALIASES, _new_alias(w, row["id"], name, "full", status, False, None))
            if role == PLAYER_CHARACTER and played_by is not None:
                await _sync_plays(w, row["id"])
            return Written(_entity(row), w.batch)

    async def add_names(
        self,
        guild_id: int,
        campaign_id: str,
        names: Sequence[NewName],
        *,
        source: str,
        secret_clashes: bool = True,
        more: Sequence[MoreNames] = (),
        entity_source: str | None = None,
    ) -> Written[list[str | None]]:
        """`_add_names` (see there), then the memory tables' statistics refreshed after a big
        list (#2376): the next read of a campaign that just grew by thousands of names is planned
        from fresh counts."""
        written = await self._add_names(
            guild_id, campaign_id, names, source=source, secret_clashes=secret_clashes,
            more=more, entity_source=entity_source,
        )  # fmt: skip
        if sum(1 for i in written.value if i is not None) >= ANALYZE_AFTER_NAMES:
            await self.analyze(guild_id)
        return written

    async def analyze(self, guild_id: int) -> None:
        """Refresh the planner's statistics for the memory tables, after a big import or a
        restore (#2376): until the database's own ANALYZE has run, a read over thousands of new
        rows is planned from guesses and can take seconds. Best effort: the database role may not
        be allowed to (then nothing happens), and nothing here may fail the import that called
        it. Only table names are run, never anyone's data."""
        try:
            async with self._db.guild(guild_id) as conn:
                await conn.execute(
                    "ANALYZE memory_entities, memory_aliases, memory_relations,"
                    " memory_beliefs, memory_perspectives"
                )
        except Exception:
            log.warning("Couldn't refresh the memory tables' statistics", exc_info=False)

    async def _add_names(
        self,
        guild_id: int,
        campaign_id: str,
        names: Sequence[NewName],
        *,
        source: str,
        secret_clashes: bool = True,
        more: Sequence[MoreNames] = (),
        entity_source: str | None = None,
    ) -> Written[list[str | None]]:
        """Many names at once (📥 Add many), each with its other and secret names, in one
        change: one Undo (`undo_names`) takes the whole list back, and live transcription
        reloads its names once. Only the DM gives lists. Returns the new entries' IDs, in
        order; None for a name that another entry already has by now (two lists saved at
        once: memory writes for a campaign happen one at a time, so this check is exact).
        Other names already used elsewhere are left out the same way. `secret_clashes`:
        whether a secret name counts as used (False for anyone but the campaign's DMs, who
        must never learn one exists). `more`: other names for names DMbot already knows
        (#369), in the same change, so the same Undo takes them back; one whose entry has
        gone, or whose name is used by now or that entry already has (even as a secret or
        a name the DM said isn't it), is left out quietly. The returned IDs then go on with
        one per `more` entry: its entry's ID if any name was added, else None. `entity_source`:
        what the new entries record as their origin (`BACKGROUND_SOURCE` for names read from the
        DM's story; the change itself is still the DM's)."""
        if source != DM:
            raise MemoryRuleError("Only the DM can add a list of names.")
        if entity_source is not None and entity_source not in SOURCES:
            raise ValueError(f"Unknown memory source: {entity_source!r}")
        for n in names:
            _check_choice(n.status, LIVE, "entity status")
        # Each name's spelling, key and sound codes, before the campaign's write lock and
        # off the event loop (about 100 ms for a 2,000-line list, #598).
        raw = [t for n in names for t in (n.name, *n.others, *n.secrets)]
        raw += [t for m in more for t in (*m.others, *m.secrets)]
        prep = await asyncio.to_thread(_Prepared, raw)
        async with self._write(guild_id, campaign_id, source) as w:
            onto = await _load_ontology(w)
            # Names in use, in two plain reads joined here: as one query, a campaign just
            # given a long list (no table statistics yet) got a plan comparing every name
            # with every entry, 1.6 s for 2,000 names under the write lock (#253 review).
            cur = await w.conn.execute(
                "SELECT id FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
                " AND status IN ('proposed', 'confirmed')",
                w.ids,
            )
            live_ids = {str(r["id"]) for r in await cur.fetchall()}
            cur = await w.conn.execute(
                "SELECT entity_id, key FROM memory_aliases WHERE guild_id = %s"
                " AND campaign_id = %s AND status <> 'rejected' AND (%s OR NOT secret)",
                (*w.ids, secret_clashes),
            )
            used = {str(r["key"]) for r in await cur.fetchall() if r["entity_id"] in live_ids}
            folded: list[str | None] = []
            aliases: list[dict[str, Any]] = []  # every new name, written together at the end
            wanted = sorted({m.entity_id for m in more})
            live = {
                str(r["id"]): str(r["status"])
                for r in await w.select(ENTITIES, " AND id = ANY(%s)", [wanted])
                if r["status"] in LIVE
            }
            # Every name they have, secret or turned down too: never a second copy, and
            # never an error that would tell a player a secret name exists.
            has_keys: dict[str, set[str]] = {}
            for r in await w.select(ALIASES, " AND entity_id = ANY(%s)", [sorted(live)]):
                has_keys.setdefault(str(r["entity_id"]), set()).add(str(r["key"]))
            for m in more:
                if m.entity_id not in live:
                    folded.append(None)  # forgotten or joined to another since
                    continue
                status = live[m.entity_id]
                has = has_keys.setdefault(m.entity_id, set())
                extra = [(t, "nickname", False) for t in m.others]
                extra += [(t, "title", True) for t in m.secrets]
                added = False
                for t, kind, secret in extra:
                    text, key, codes = prep[t]
                    if key in used or key in has:
                        continue
                    used.add(key)
                    has.add(key)
                    aliases.append(
                        _new_alias(w, m.entity_id, text, kind, status, secret, None, codes)
                    )
                    added = True
                folded.append(m.entity_id if added else None)
            ids: list[str | None] = []
            entities: list[dict[str, Any]] = []
            for n in names:
                check_named(n.type, n.name)
                kind, role = resolve_kind(n.type, n.role)
                onto.active_type(kind)
                _check_role(onto, kind, role, None)
                if role == PLAYER_CHARACTER:
                    raise MemoryRuleError("A player's character needs its player.")
                name, name_key_, _ = prep[n.name]
                if name_key_ in used:
                    ids.append(None)
                    continue
                entity_id = new_id()
                entities.append(
                    {
                        "id": entity_id, "type": kind, "name": name,
                        "description": n.note[:DESCRIPTION_MAX],
                        "status": n.status, "merged_into": None,
                        "source": entity_source or source,
                        "created_at": w.now, "played_by": None, "role": role,
                        "needs_look": False,
                        "unique_item": None,
                        # Names from a list, or read from the DM's story, start unrevealed
                        # (#2404): the table's first mention of one shows it to the players.
                        "hidden_from_players": True,
                    }
                )  # fmt: skip
                seen = {name_key_}
                texts = [(n.name, "full", False)]
                texts += [(t, "nickname", False) for t in n.others]
                texts += [(t, "title", True) for t in n.secrets]
                for t, kind, secret in texts:
                    text, key, codes = prep[t]
                    if (key in seen or key in used) and kind != "full":
                        continue
                    seen.add(key)
                    aliases.append(
                        _new_alias(w, entity_id, text, kind, n.status, secret, None, codes)
                    )
                used |= seen
                ids.append(entity_id)
            # Two statements however long the list (#253): the entries, then all their
            # names, so the campaign's write lock is held for a moment, not seconds.
            await w.insert_many(ENTITIES, entities)
            await w.insert_many(ALIASES, aliases)
            return Written(ids + folded, w.batch)

    async def undo_names(self, guild_id: int, campaign_id: str, batch: int) -> Written[None]:
        """Take back a whole list from `add_names`. Refused unless that batch only added
        names (from the DM), and, like any undo, if any of them changed or anything links
        to them since (a connection, a mention, a DM fix): nothing is deleted silently."""
        try:
            return await self._undo_names(guild_id, campaign_id, batch)
        except TooLateToUndo:
            raise TooLateToUndo(self._too_late()) from None

    async def _undo_names(self, guild_id: int, campaign_id: str, batch: int) -> Written[None]:
        async with self._write(guild_id, campaign_id, "undo", undoes=batch) as w:
            cur = await w.conn.execute(
                "SELECT bool_and(op = 'insert' AND source = 'dm' AND table_name IN"
                " ('memory_entities', 'memory_aliases')) AS names_only, count(*) AS n"
                " FROM memory_changes WHERE guild_id = %s AND campaign_id = %s AND batch = %s",
                (*w.ids, batch),
            )
            row = await cur.fetchone()
            if row is None or not row["n"]:
                raise TooLateToUndo(NOT_FOUND)
            if not row["names_only"]:
                raise MemoryRuleError(NOT_FOUND)
            cur = await w.conn.execute(
                "SELECT 1 FROM memory_heard h JOIN memory_changes c"
                " ON c.guild_id = h.guild_id AND c.campaign_id = h.campaign_id"
                " AND c.row_id = h.entity_id AND c.table_name = 'memory_entities'"
                " WHERE c.guild_id = %s AND c.campaign_id = %s AND c.batch = %s LIMIT 1",
                (*w.ids, batch),
            )
            if await cur.fetchone() is not None:  # heard in a session since: keep them
                raise MemoryRuleError(CHANGED_SINCE)
            await undo_batch(w, batch)
            return Written(None, w.batch)

    async def set_entity_type(
        self,
        guild_id: int,
        campaign_id: str,
        entity_id: str,
        type: str,
        *,
        source: str,
        role: str | None = None,
    ) -> Written[Entity]:
        """What kind of thing it is (a character, a place…) and, for a character, its role
        (an NPC, a god…): the DM's call. Looking at it clears "needs a look"."""
        if source != DM:
            raise MemoryRuleError("Only the DM can say what something is.")
        asked = type
        type, role = resolve_kind(type, role)
        if type == PERSON:
            raise MemoryRuleError(PERSON_NOT_LISTED)
        async with self._write(guild_id, campaign_id, source) as w:
            onto = await _load_ontology(w)
            onto.active_type(type)
            current = await _entity_row(w, entity_id)
            if current["type"] == PERSON:
                raise MemoryRuleError(PERSON_NOT_LISTED)
            check_named(asked, current["name"])
            if role is None and type == current["type"]:
                role = current["role"]  # same kind, no new role said: it keeps its role
            _check_role(onto, type, role, None)
            changes: dict[str, Any] = {"type": type, "role": role, "needs_look": False}
            if current["played_by"] is not None and role != PLAYER_CHARACTER:
                changes["played_by"] = None
            row = await w.update(ENTITIES, entity_id, changes)
            await states.settle_kind(w, onto, [entity_id], type)
            row = await _entity_row(w, entity_id)
            await _sync_plays(w, entity_id)
            return Written(_entity(row), w.batch)

    async def rule_links(
        self, guild_id: int, campaign_id: str, entity_ids: Collection[str] | None = None
    ) -> list[RuleLink]:
        """A campaign's characters' links to the rules (species, creature type, stat
        block, class, background), or those of `entity_ids`."""
        async with self._read(guild_id, campaign_id) as scope:
            if entity_ids is None:
                rows = await scope.select(RULE_LINKS, " ORDER BY created_at, id")
            else:
                rows = await scope.select(
                    RULE_LINKS,
                    " AND entity_id = ANY(%s) ORDER BY created_at, id",
                    [sorted(entity_ids)],
                )
            return [_rule_link(r) for r in rows]

    async def set_rule_link(
        self,
        guild_id: int,
        campaign_id: str,
        entity_id: str,
        kind: str,
        name: str,
        *,
        source: str,
        rules_source: str = "",
        edition: str | None = None,
        known: bool = False,
    ) -> Written[RuleLink]:
        """Link a character to a rules entry by source and name (its species, creature
        type, stat block, class or background). A name the rules data doesn't have keeps its
        plain words with `known` false ("not in DMbot's rules"). A character has one species
        and one creature type: a new one replaces the old; classes and the rest add up.
        The DM's call (there is no screen for it yet; links come from the DM's sheet flow)."""
        if source != DM:
            raise MemoryRuleError("Only the DM can link a character to the rules.")
        _check_choice(kind, LINK_KINDS, "link kind")
        name = clean_text(name, LINK_NAME_MAX)
        if known and not rules_source:
            raise MemoryRuleError("A link to a rules entry needs its source.")
        rules_source = rules_source if known else ""
        if len(rules_source) > LINK_SOURCE_MAX:
            raise MemoryRuleError("That source name is too long.")
        if edition is not None and edition not in LINK_EDITIONS:
            raise MemoryRuleError("The edition is 2014 or 2024.")
        async with self._write(guild_id, campaign_id, source) as w:
            onto = await _load_ontology(w)
            entity = await _entity_row(w, entity_id)
            if entity["status"] not in LIVE or not onto.is_a(entity["type"], "character"):
                raise MemoryRuleError("Only a character is linked to the rules.")
            if kind in (SPECIES, CREATURE_TYPE):
                for old in await w.select(
                    RULE_LINKS, " AND entity_id = %s AND kind = %s", [entity_id, kind]
                ):
                    await w.delete(RULE_LINKS, old["id"])
            else:
                for old in await w.select(
                    RULE_LINKS,
                    " AND entity_id = %s AND kind = %s AND rules_source = %s AND name = %s",
                    [entity_id, kind, rules_source, name],
                ):
                    return Written(_rule_link(old), w.batch)  # already linked
            row = await w.insert(
                RULE_LINKS,
                {
                    "id": new_id(),
                    "entity_id": entity_id,
                    "kind": kind,
                    "rules_source": rules_source,
                    "name": name,
                    "edition": edition,
                    "known": known,
                    "created_at": w.now,
                },
            )
            return Written(_rule_link(row), w.batch)

    async def remove_rule_link(
        self, guild_id: int, campaign_id: str, link_id: str, *, source: str
    ) -> Written[None]:
        """Take a link away (the DM's call)."""
        if source != DM:
            raise MemoryRuleError("Only the DM can change that.")
        async with self._write(guild_id, campaign_id, source) as w:
            await w.delete(RULE_LINKS, link_id)
            return Written(None, w.batch)

    async def rename_entity(
        self, guild_id: int, campaign_id: str, entity_id: str, name: str, *, source: str
    ) -> Written[Entity]:
        """Fix how a name is spelled (the DM's call): the entry's name and the name it's
        listened for change together, in one change (one undo)."""
        if source != DM:
            raise MemoryRuleError("Only the DM can change a name.")
        name = clean_text(name)
        key = lookup_key(name)
        async with self._write(guild_id, campaign_id, source) as w:
            current = await _entity_row(w, entity_id)
            if current["type"] != PERSON:
                check_named(current["type"], name)
            old_key = lookup_key(current["name"])
            own = await w.select(ALIASES, " AND entity_id = %s AND key = %s", [entity_id, old_key])
            clash = await w.select(ALIASES, " AND entity_id = %s AND key = %s", [entity_id, key])
            if clash and clash[0]["secret"]:
                # Never turn a secret name into the name everyone hears.
                raise MemoryRuleError("Pick another spelling.")
            row = await w.update(ENTITIES, entity_id, {"name": name})
            if own and (not clash or clash[0]["id"] == own[0]["id"]):
                # The same name, spelled right (listened for, and never secret).
                await w.update(
                    ALIASES,
                    own[0]["id"],
                    {
                        "text": name,
                        "key": key,
                        "sound_codes": list(sound_codes(name)),
                        "status": CONFIRMED,
                        "secret": False,
                    },
                )
            elif clash:
                # The right spelling was already one of its other names: that becomes
                # its name, and the misspelling stops being listened for.
                # kind too: it may have been an "old spelling" (a rename back, #2170).
                await w.update(
                    ALIASES, clash[0]["id"], {"text": name, "status": CONFIRMED, "kind": "full"}
                )
                if own:
                    await w.update(ALIASES, own[0]["id"], {"status": REJECTED})
            else:
                await w.insert(
                    ALIASES, _new_alias(w, entity_id, name, "full", CONFIRMED, False, None)
                )
            await self._keep_old_spelling(w, entity_id, str(current["name"]), old_key, key)
            return Written(_entity(row), w.batch)

    async def _keep_old_spelling(
        self, w: Changes, entity_id: str, old_name: str, old_key: str, new_key: str
    ) -> None:
        """The spelling a fixed name had becomes an "old spelling" other name (#2170): matched, so
        a line that says it is fixed to the new spelling and a search finds the entry, but never
        listened for. Dropped, as the old spelling always was, when it is only the new one
        written differently, when another entry has it as a name (the cleaner would turn that
        entry's lines into this one), when any secret name has it, or when it is a word the DM
        screen uses."""
        if old_key == new_key or is_dm_word(old_name):
            return
        others = await w.select(ALIASES, " AND key = %s AND entity_id <> %s", [old_key, entity_id])
        if any(o["status"] in LIVE or o["secret"] for o in others):
            return
        mine = await w.select(ALIASES, " AND entity_id = %s AND key = %s", [entity_id, old_key])
        if mine:  # it was rejected on the way (the new spelling was already another name)
            await w.update(ALIASES, mine[0]["id"], {"kind": "old", "status": CONFIRMED})
            return
        await w.insert(ALIASES, _new_alias(w, entity_id, old_name, "old", CONFIRMED, False, None))

    async def set_main_name(
        self, guild_id: int, campaign_id: str, entity_id: str, alias_id: str, *, source: str
    ) -> Written[Entity]:
        """Make one of an entry's other names its main name (the DM's call, ⭐ on the
        name card). The old main name stays one of its other names. Never a secret name:
        the main name is the one everyone sees."""
        if source != DM:
            raise MemoryRuleError("Only the DM can change a name.")
        async with self._write(guild_id, campaign_id, source) as w:
            await _entity_row(w, entity_id)
            alias = await w.get(ALIASES, alias_id)
            if alias is None or alias["entity_id"] != entity_id or alias["status"] != CONFIRMED:
                raise MemoryRuleError(NOT_FOUND)
            if alias["secret"]:
                raise MemoryRuleError("A secret name can't be the main name.")
            old = await w.get(ENTITIES, entity_id)
            assert old is not None
            kept = await w.select(
                ALIASES, " AND entity_id = %s AND key = %s", [entity_id, lookup_key(old["name"])]
            )
            if not kept:  # the old main name stays one of its other names
                await w.insert(
                    ALIASES, _new_alias(w, entity_id, old["name"], "full", CONFIRMED, False, None)
                )
            elif kept[0]["status"] != CONFIRMED:
                await w.update(ALIASES, kept[0]["id"], {"status": CONFIRMED})
            if alias["kind"] == "old":  # an old spelling made the name is listened for again
                await w.update(ALIASES, alias_id, {"kind": "full"})
            row = await w.update(ENTITIES, entity_id, {"name": alias["text"]})
            return Written(_entity(row), w.batch)

    async def set_note(
        self,
        guild_id: int,
        campaign_id: str,
        entity_id: str,
        note: str,
        *,
        source: str,
        limit: int = 200,
    ) -> Written[Entity]:
        """The note shown with a suggestion (how it was heard, or spelled out at the table, or
        what the DM wrote about it on the recap page: `limit` letters). Only a suggestion still
        waiting for the DM has one that can change."""
        note = " ".join(note.split())[:limit]
        async with self._write(guild_id, campaign_id, source) as w:
            current = await _entity_row(w, entity_id)
            if current["status"] != PROPOSED:
                raise MemoryRuleError(NOT_FOUND)
            row = await w.update(ENTITIES, entity_id, {"description": note})
            return Written(_entity(row), w.batch)

    async def confirm_entity(
        self,
        guild_id: int,
        campaign_id: str,
        entity_id: str,
        type: str,
        *,
        source: str,
        played_by: int | None = None,
        role: str | None = None,
    ) -> Written[Entity]:
        """The DM says a suggested name is real: what it is (and, for a character, its
        role), confirmed with all its names, in one change (one undo, one reload)."""
        if source != DM:
            raise MemoryRuleError("Only the DM can confirm that.")
        if played_by is not None and not 0 < played_by <= INT64_MAX:
            raise ValueError("Bad Discord user ID")
        asked = type
        type, role = resolve_kind(type, role)
        if type == PERSON:
            raise MemoryRuleError(PERSON_NOT_LISTED)
        async with self._write(guild_id, campaign_id, source) as w:
            onto = await _load_ontology(w)
            onto.active_type(type)
            _check_role(onto, type, role, played_by)
            before = await _entity_row(w, entity_id)
            check_named(asked, before["name"])
            row = await w.update(
                ENTITIES,
                entity_id,
                {
                    "type": type,
                    "role": role,
                    "status": CONFIRMED,
                    "played_by": played_by,
                    "needs_look": False,
                    "unique_item": None,
                    **_note_cleared(before),
                },
            )
            for alias in await w.select(
                ALIASES, " AND entity_id = %s AND status = 'proposed'", [entity_id]
            ):
                await w.update(ALIASES, alias["id"], {"status": CONFIRMED})
            await states.settle_kind(w, onto, [entity_id], type)
            await _forget_quotes(
                w, [entity_id] if before.get("source") == BACKGROUND_SOURCE else []
            )
            row = await _entity_row(w, entity_id)
            await _sync_plays(w, entity_id)
            return Written(_entity(row), w.batch)

    async def confirm_kinds(
        self,
        guild_id: int,
        campaign_id: str,
        entity_ids: Sequence[str],
        type: str,
        *,
        source: str,
        role: str | None = None,
    ) -> Written[int]:
        """The DM says what a whole group of suggested names is (📥 Add many: "every
        'wizard' is an NPC"): each still waiting is confirmed as that kind, with its names,
        in one change. Returns how many were confirmed."""
        if source != DM:
            raise MemoryRuleError("Only the DM can confirm that.")
        asked = type
        type, role = resolve_kind(type, role)
        async with self._write(guild_id, campaign_id, source) as w:
            onto = await _load_ontology(w)
            onto.active_type(type)
            _check_role(onto, type, role, None)
            if asked == "creature":
                for row in await w.select(ENTITIES, " AND id = ANY(%s)", [sorted(set(entity_ids))]):
                    check_named(asked, row["name"])
            if role == PLAYER_CHARACTER:
                raise MemoryRuleError("A player's character needs its player.")
            # A few statements for the whole group, not a chain per name (#580). Names
            # checked or removed since aren't proposed any more, so they're left alone.
            # Names read from the DM's story lose their quote with the decision (#2167). One
            # change per entry (a second change to the same entry in one batch could not be
            # undone), so the two groups are changed apart.
            wanted = sorted(set(entity_ids))
            quoted_ids = [
                str(r["id"])
                for r in await w.select(
                    ENTITIES,
                    " AND status = 'proposed' AND source = %s AND id = ANY(%s)",
                    [BACKGROUND_SOURCE, wanted],
                )
            ]
            quoted_set = set(quoted_ids)
            plain = [i for i in wanted if i not in quoted_set]
            fields = {"type": type, "role": role, "status": CONFIRMED, "needs_look": False}
            confirmed = []
            for group, extra in ((quoted_ids, {"description": ""}), (plain, {})):
                confirmed += await w.update_where(
                    ENTITIES,
                    {**fields, **extra},
                    " AND status = 'proposed' AND id = ANY(%s)",
                    [group],
                )
            ids = [row["id"] for row in confirmed]
            await states.settle_kind(w, onto, ids, type)
            await _forget_quotes(w, [r["id"] for r in confirmed if r["id"] in quoted_set])
            if ids:
                await w.update_where(
                    ALIASES,
                    {"status": CONFIRMED},
                    " AND status = 'proposed' AND entity_id = ANY(%s)",
                    [ids],
                )
            return Written(len(ids), w.batch)

    async def known_keys(self, guild_id: int, campaign_id: str) -> set[str]:
        """Every name and word DMbot already has an answer for in this campaign, whatever
        the answer: names (rejected ones too, so "Not a name" sticks) and "keep as heard"
        and "change to" rules. The after-session scan never suggests these again."""
        async with self._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "SELECT key FROM memory_aliases WHERE guild_id = %s AND campaign_id = %s"
                " UNION SELECT heard_key FROM memory_corrections"
                " WHERE guild_id = %s AND campaign_id = %s",
                (*scope.ids, *scope.ids),
            )
            return {str(r["key"]) for r in await cur.fetchall()}

    async def set_entity_status(
        self, guild_id: int, campaign_id: str, entity_id: str, status: str, *, source: str
    ) -> Written[Entity]:
        _check_choice(status, FACT_STATUSES, "entity status")
        async with self._write(guild_id, campaign_id, source) as w:
            current = await _entity_row(w, entity_id, allow_rejected=True)
            check_status_change(current["status"], status, source)
            row = await w.update(
                ENTITIES,
                entity_id,
                {"status": status, **(_note_cleared(current) if status != PROPOSED else {})},
            )
            await _forget_quotes(
                w, [entity_id] if status != PROPOSED and _note_cleared(current) else []
            )
            if status == REJECTED:
                # A rejected entry is not a name at the table any more, so the DM's word that it is
                # a book entry (#1715) goes, freeing that book entry for the right one; Undo of
                # this change brings it back.
                for link in await w.select(PLAN_SAME_AS, " AND entity_id = %s", [entity_id]):
                    await w.delete(PLAN_SAME_AS, link["id"])
            if current["role"] == PLAYER_CHARACTER:
                await _sync_plays(w, entity_id)  # a rejected character has no player
            return Written(_entity(row), w.batch)

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
        """Another way this entity is said or written. If it's already known, only a
        stronger status (from the DM) or secret=True is applied."""
        _check_choice(kind, ALIAS_KINDS, "alias kind")
        _check_choice(status, LIVE, "alias status")
        check_status_change(None, status, source)
        text = clean_text(text)
        key = lookup_key(text)
        async with self._write(guild_id, campaign_id, source) as w:
            owner = await _entity_row(w, entity_id)
            if owner["type"] != PERSON and is_dm_word(text):
                raise MemoryRuleError(DM_WORD_REFUSED)
            if used_by is not None:
                await _entity_row(w, used_by)
            existing = await w.select(ALIASES, " AND entity_id = %s AND key = %s", [entity_id, key])
            if existing:
                old = existing[0]
                upgrade = _upgrade(old, status, secret, source)
                if old["kind"] == "old" and kind != "old":
                    upgrade = {**upgrade, "kind": kind}  # asked for by name: listened for again
                row = await w.update(ALIASES, old["id"], upgrade) if upgrade else old
                return Written(_alias(row), w.batch)
            row = await w.insert(
                ALIASES, _new_alias(w, entity_id, text, kind, status, secret, used_by)
            )
            return Written(_alias(row), w.batch)

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
        """Confirm or reject an alias, or mark it DM-only ([🤫 Keep secret from players]).
        Only the DM confirms, or makes a secret visible again."""
        async with self._write(guild_id, campaign_id, source) as w:
            return Written(await self._change_alias(w, alias_id, source, status, secret), w.batch)

    async def make_name_not_secret(
        self, guild_id: int, campaign_id: str, alias_id: str, *, source: str
    ) -> Written[list[Alias]]:
        """The secret-said-aloud question's "Not secret any more" (#1429): this name stops being
        secret, and so does every other confirmed secret name of this campaign that is the same
        spelling (the question was asked once for all of them). One batch, so one undo puts every
        one of them back. The names are read inside the write, so none is missed."""
        async with self._write(guild_id, campaign_id, source) as w:
            current = await w.get(ALIASES, alias_id)
            if current is None:
                raise MemoryRuleError(NOT_FOUND)
            cur = await w.conn.execute(
                "SELECT a.id, a.text FROM memory_aliases a JOIN memory_entities e"
                " ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id"
                " AND e.id = a.entity_id WHERE a.guild_id = %s AND a.campaign_id = %s"
                " AND a.secret AND a.status = 'confirmed' AND e.status = 'confirmed'"
                " AND e.type <> 'person' ORDER BY a.id",
                w.ids,
            )
            wanted = spelling_key(str(current["text"]))
            ids = [alias_id]
            for r in await cur.fetchall():
                if str(r["id"]) != alias_id and spelling_key(str(r["text"])) == wanted:
                    ids.append(str(r["id"]))
            done = [await self._change_alias(w, i, source, None, False) for i in ids]
            return Written(done, w.batch)

    async def _change_alias(
        self, w: Changes, alias_id: str, source: str, status: str | None, secret: bool | None
    ) -> Alias:
        current = await w.get(ALIASES, alias_id)
        if current is None:
            raise MemoryRuleError(NOT_FOUND)
        owner = await w.get(ENTITIES, current["entity_id"])
        if (
            owner is not None
            and current["key"] == lookup_key(owner["name"])
            and (secret or status == REJECTED)
        ):
            # The main name is the one everyone sees and DMbot listens for.
            raise MemoryRuleError("That's the main name. Make another name the main one first.")
        changes: dict[str, Any] = {}
        if status is not None:
            _check_choice(status, FACT_STATUSES, "alias status")
            check_status_change(current["status"], status, source)
            changes["status"] = status
        if secret is not None:
            if current["secret"] and not secret and source != DM:
                raise MemoryRuleError("Only the DM can stop keeping that secret.")
            changes["secret"] = secret
            if secret:
                changes["said_as"] = None  # a secret name is never sent to the narrator
        return _alias(await w.update(ALIASES, alias_id, changes))

    async def said_as(self, guild_id: int, campaign_id: str, entity_id: str) -> str | None:
        """How the narrator says an entry's main name, if the DM gave a spelling (#1419)."""
        async with self._read(guild_id, campaign_id) as scope:
            entity = (await scope.select(ENTITIES, " AND id = %s", [entity_id]))[:1]
            if not entity:
                return None
            rows = await scope.select(
                ALIASES,
                " AND entity_id = %s AND key = %s",
                [entity_id, lookup_key(entity[0]["name"])],
            )
        return None if not rows or rows[0]["secret"] else rows[0]["said_as"]

    async def set_said_as(
        self,
        guild_id: int,
        campaign_id: str,
        entity_id: str,
        spelling: str | None,
        *,
        source: str,
    ) -> Written[Alias]:
        """The DM's spelling of an entry's main name for the recap's narrator ("Kah-zeth" for
        Ka'zeth), or none to clear it. Changed through the usual path, so it can be undone.
        Refused on a secret name: the narrator's dictionary goes to the voice company."""
        if source != DM:
            raise MemoryRuleError("Only the DM can change how recaps say a name.")
        clean = " ".join((spelling or "").split()) or None
        if clean is not None and (len(clean) > SAID_AS_MAX or not clean.isprintable()):
            raise MemoryRuleError(f"Use up to {SAID_AS_MAX} letters, with no odd characters.")
        async with self._write(guild_id, campaign_id, source) as w:
            return await self._change_said_as(w, entity_id, clean)

    @staticmethod
    async def _change_said_as(w: Changes, entity_id: str, clean: str | None) -> Written[Alias]:
        """Set (or clear) the main name's "said as" inside a write that is already open."""
        owner = await w.get(ENTITIES, entity_id)
        if owner is None:
            raise MemoryRuleError(NOT_FOUND)
        rows = await w.select(
            ALIASES, " AND entity_id = %s AND key = %s", [entity_id, lookup_key(owner["name"])]
        )
        if not rows:
            raise MemoryRuleError(NOT_FOUND)
        if rows[0]["secret"] and clean is not None:
            raise MemoryRuleError(
                "That name is secret, so recaps never say it. "
                "Make it public first, or leave this empty."
            )
        row = await w.update(ALIASES, rows[0]["id"], {"said_as": clean})
        return Written(_alias(row), w.batch)

    async def add_said_as_suggestions(
        self, guild_id: int, campaign_id: str, found: Sequence[tuple[str, str, str, int]]
    ) -> int:
        """Keep (entity ID, the spelling it is heard as, the name it was fixed to, how often) as
        "say it this way?" offers. Only for a confirmed name whose main name is public and has no
        "said as" yet, only when the fixes were to that main name (a fix to an alias says nothing
        about the main name), never for a person at the table (how a real person's name was
        misheard is of no use to the narrator), and not one the DM already turned down or was
        already asked about. An observation, not an edit: no change log and no reload. Returns how
        many were kept."""
        async with self._read(guild_id, campaign_id) as scope:
            entities = {
                str(r["id"]): r for r in await scope.select(ENTITIES, " AND status = 'confirmed'")
            }
            mains = {(str(a["entity_id"]), a["key"]): a for a in await scope.select(ALIASES)}
        rows = []
        for entity_id, heard, written, times in found:
            entity = entities.get(entity_id)
            heard = " ".join(heard.split())
            if entity is None or not heard or len(heard) > SAID_AS_MAX or times <= 0:
                continue
            if entity["type"] == "person" or lookup_key(written) != lookup_key(entity["name"]):
                continue
            main = mains.get((entity_id, lookup_key(entity["name"])))
            if main is None or main["secret"] or main["said_as"]:
                continue  # a secret name is never sent to the narrator; one with a spelling has it
            if lookup_key(heard) == lookup_key(entity["name"]):
                continue
            rows.append((entity_id, heard, times))
        if not rows:
            return 0
        kept = 0
        async with self._db.guild(guild_id) as conn:
            for entity_id, heard, times in rows:
                cur = await conn.execute(
                    "INSERT INTO said_as_suggestions"
                    " (guild_id, campaign_id, entity_id, heard, times, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (guild_id, campaign_id, entity_id) DO NOTHING",
                    (guild_id, campaign_id, entity_id, heard, times, int(self._clock())),
                )
                kept += cur.rowcount
        return kept

    async def said_as_suggestions(self, guild_id: int, campaign_id: str) -> list[SaidAsSuggestion]:
        """The offers still waiting, oldest first. One whose name has changed since (secret now,
        or given a "said as" some other way) is not offered."""
        async with self._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "SELECT entity_id, heard, times FROM said_as_suggestions"
                " WHERE guild_id = %s AND campaign_id = %s AND state = 'waiting'"
                " ORDER BY created_at, entity_id",
                scope.ids,
            )
            waiting = await cur.fetchall()
            entities = {
                str(r["id"]): r for r in await scope.select(ENTITIES, " AND status = 'confirmed'")
            }
            mains = {(str(a["entity_id"]), a["key"]): a for a in await scope.select(ALIASES)}
        out = []
        for r in waiting:
            entity = entities.get(str(r["entity_id"]))
            if entity is None:
                continue
            main = mains.get((str(r["entity_id"]), lookup_key(entity["name"])))
            if main is None or main["secret"] or main["said_as"]:
                continue
            out.append(
                SaidAsSuggestion(str(r["entity_id"]), entity["name"], r["heard"], r["times"])
            )
        return out

    async def answer_said_as(
        self, guild_id: int, campaign_id: str, entity_id: str, spelling: str | None
    ) -> None:
        """The DM's answer to an offer: a spelling (the offered one or one they typed) sets the
        name's "said as" and ends the offer; None is a "no" that is remembered, so the same offer
        never comes back. Only the DM's answer reaches here (the caller checks)."""
        # One transaction: the offer is claimed and the spelling set together, so a failure (the
        # name made secret meanwhile) leaves the offer waiting, and two DMs answering at once
        # cannot both win.
        if spelling is None:
            async with self._db.guild(guild_id) as conn:
                cur = await conn.execute(
                    "UPDATE said_as_suggestions SET state = 'no' WHERE guild_id = %s"
                    " AND campaign_id = %s AND entity_id = %s AND state = 'waiting'"
                    " RETURNING entity_id",
                    (guild_id, campaign_id, entity_id),
                )
                if await cur.fetchone() is None:
                    raise MemoryRuleError(NOT_FOUND)
            return
        clean = " ".join(spelling.split()) or None
        if clean is not None and (len(clean) > SAID_AS_MAX or not clean.isprintable()):
            raise MemoryRuleError(f"Use up to {SAID_AS_MAX} letters, with no odd characters.")
        async with self._write(guild_id, campaign_id, DM) as w:
            cur = await w.conn.execute(
                "DELETE FROM said_as_suggestions WHERE guild_id = %s AND campaign_id = %s"
                " AND entity_id = %s AND state = 'waiting' RETURNING entity_id",
                (guild_id, campaign_id, entity_id),
            )
            if await cur.fetchone() is None:
                raise MemoryRuleError(NOT_FOUND)
            await self._change_said_as(w, entity_id, clean)

    async def merge(
        self,
        guild_id: int,
        campaign_id: str,
        keep_id: str,
        gone_id: str,
        *,
        source: str,
        dm_said_same: bool = False,
        confirm_keys: Collection[str] = (),
    ) -> Written[Entity]:
        """Two entries are the same person or thing: move everything onto `keep_id`.

        Two proposed entries of the same kind may merge on strong evidence. If either is
        confirmed, or their kinds differ, only the DM can say they're the same. Facts
        moved over are checked again (duplicates folded, problems flagged). Undo splits
        them again. `confirm_keys`: names the DM just said mean `keep_id` (from a
        suggestion), confirmed in the same change, so one undo takes it all back.
        """
        if keep_id == gone_id:
            raise MemoryRuleError("That's the same entry.")
        if dm_said_same and source != DM:
            raise ValueError("Only a DM source can say two entries are the same")
        async with self._write(guild_id, campaign_id, source) as w:
            await self._merge_in(
                w,
                keep_id,
                gone_id,
                source=source,
                dm_said_same=dm_said_same,
                confirm_keys=confirm_keys,
            )
            final = await w.get(ENTITIES, keep_id)
            assert final is not None
            return Written(_entity(final), w.batch)

    async def merge_many(
        self,
        guild_id: int,
        campaign_id: str,
        keep_id: str,
        other_ids: Sequence[str],
        *,
        source: str,
    ) -> Written[Entity]:
        """Several entries are one: merge each of `other_ids` into `keep_id` in one write.

        The same rules as `merge` for each name (the DM said they are the same, so kinds may
        differ), in one change-log batch so one Undo takes the whole thing back, and all or
        nothing: a name the rules refuse fails the lot. Nothing is deleted. (#2426)
        """
        if source != DM:
            raise ValueError("Only a DM source can say entries are the same")
        gone = list(dict.fromkeys(other_ids))
        if not gone or keep_id in gone:
            raise MemoryRuleError("Pick the other names to merge into the kept one.")
        if len(gone) > MERGE_MANY_MAX:
            raise MemoryRuleError(f"Merge up to {MERGE_MANY_MAX} names at a time.")
        async with self._write(guild_id, campaign_id, source) as w:
            for gone_id in gone:
                await self._merge_in(w, keep_id, gone_id, source=source, dm_said_same=True)
            final = await w.get(ENTITIES, keep_id)
            assert final is not None
            return Written(_entity(final), w.batch)

    async def look_alikes(
        self, guild_id: int, campaign_id: str, *, only: Collection[str] | None = None
    ) -> list[list[LookAlikeName]]:
        """Groups of confirmed names that may be one name written several ways, most heard
        name first in each (the finder is `memory/lookalikes.py`). Pairs the DM said are
        "Not the same" are left apart. `only`: just the groups holding one of these entries
        (a card shows the look-alikes of its own name). For the DM; the caller checks."""
        async with self._read(guild_id, campaign_id, snapshot=True) as scope:
            cur = await scope.conn.execute(
                "SELECT memory_version FROM campaigns WHERE guild_id = %s AND id = %s",
                scope.ids,
            )
            row = await cur.fetchone()
            version = int(row["memory_version"]) if row else -1
            cur = await scope.conn.execute(
                "SELECT e.id, e.name, (SELECT count(*) FROM memory_mentions m"
                " WHERE m.guild_id = e.guild_id AND m.campaign_id = e.campaign_id"
                " AND m.entity_id = e.id) AS heard FROM memory_entities e"
                " WHERE e.guild_id = %s AND e.campaign_id = %s AND e.status = 'confirmed'"
                " AND e.type <> %s",
                (*scope.ids, PERSON),
            )
            names = [
                LookAlikeName(str(r["id"]), str(r["name"]), int(r["heard"]))
                for r in await cur.fetchall()
            ]
            cur = await scope.conn.execute(
                "SELECT a_id, b_id FROM name_not_same WHERE guild_id = %s AND campaign_id = %s",
                scope.ids,
            )
            apart = {(str(r["a_id"]), str(r["b_id"])) for r in await cur.fetchall()}
        key = (guild_id, campaign_id)
        stamp = (version, len(names), len(apart))
        cached = self._look_alikes.get(key)
        if cached is not None and cached[0] == stamp:
            found = cached[1]
        else:
            # Off the event loop: a campaign of thousands of names takes a moment, and the
            # bot's other work (voice, consent) must not wait for it.
            found = await asyncio.to_thread(lambda: find_look_alikes(names, apart=apart))
            if len(self._look_alikes) > 64:
                self._look_alikes.clear()
            self._look_alikes[key] = (stamp, found)
        if only is not None:
            wanted = set(only)
            found = [g for g in found if wanted & {n.id for n in g}]
        return found

    async def not_the_same(
        self, guild_id: int, campaign_id: str, ids: Sequence[str], *, user_id: int
    ) -> int:
        """The DM says these names are not the same: every pair among them is remembered so
        the look-alike finder never offers it again. Returns how many pairs were new. Not in the
        change log (there is nothing to undo: the DM can still merge them by hand)."""
        keep = list(dict.fromkeys(ids))
        if len(keep) > MERGE_MANY_MAX + 1:
            raise MemoryRuleError(f"Pick up to {MERGE_MANY_MAX + 1} names at a time.")
        self._look_alikes.pop((guild_id, campaign_id), None)
        pairs = [not_same_pair(a, b) for i, a in enumerate(keep) for b in keep[i + 1 :]]
        if not pairs:
            return 0
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
                " AND status = 'confirmed' AND type <> %s AND id = ANY(%s)",
                (guild_id, campaign_id, PERSON, keep),
            )
            row = await cur.fetchone()
            if row is None or int(row["n"]) != len(keep):
                raise MemoryRuleError(NOT_FOUND)
            added = 0
            for a, b in pairs:
                cur = await conn.execute(
                    "INSERT INTO name_not_same (guild_id, campaign_id, a_id, b_id, decided_by,"
                    " created_at) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (guild_id, campaign_id, a, b, user_id, int(self._clock())),
                )
                added += cur.rowcount
            return added

    async def _merge_in(
        self,
        w: Changes,
        keep_id: str,
        gone_id: str,
        *,
        source: str,
        dm_said_same: bool,
        confirm_keys: Collection[str] = (),
    ) -> None:
        """One merge inside an open write (see `merge`)."""
        onto = await _load_ontology(w)
        keep = await _entity_row(w, keep_id)
        gone = await _entity_row(w, gone_id)
        if (keep["type"] == PERSON) != (gone["type"] == PERSON):
            raise MemoryRuleError("A player or the DM can't be the same as a character.")
        needs_dm = CONFIRMED in (keep["status"], gone["status"]) or (
            keep["type"],
            keep["role"],
        ) != (gone["type"], gone["role"])
        if needs_dm and not dm_said_same:
            raise MemoryRuleError("Only the DM can say these two are the same.")
        players = {keep["played_by"], gone["played_by"]} - {None}
        if len(players) > 1:
            raise MemoryRuleError("Those are two different players' characters.")
        if gone["played_by"] is not None and keep["played_by"] is None:
            # A player's character stays one, whichever name is kept.
            await w.update(
                ENTITIES,
                keep_id,
                {"type": gone["type"], "role": gone["role"], "played_by": gone["played_by"]},
            )
            await states.settle_kind(w, onto, [keep_id], gone["type"])
        kept = await _entity_row(w, keep_id)
        if kept["type"] == gone["type"]:  # one role, one look: neither is lost or invented
            merged = {
                "role": kept["role"] or gone["role"],
                "needs_look": kept["needs_look"] and gone["needs_look"],
                # The kept entry's word on one of a kind, else the merged one's.
                "unique_item": (
                    kept["unique_item"] if kept["unique_item"] is not None else gone["unique_item"]
                ),
                # A clue only some people know stays so if either was (#1649).
                "audience_limited": kept["audience_limited"] or gone["audience_limited"],
                # And so does an entry the players don't know yet (#2321).
                "hidden_from_players": (kept["hidden_from_players"] or gone["hidden_from_players"]),
            }
            if merged != {k: kept[k] for k in merged}:
                await w.update(ENTITIES, keep_id, merged)
        before_moves = await merge_restore.snapshot(
            w.conn, w.ids, keep_id, gone_id, kept["played_by"]
        )
        await _move_rule_links(w, keep_id, gone_id)
        await _move_plan_links(w, keep_id, gone_id)
        await states.move_states(w, onto, keep_id, kept["type"], gone_id)
        if confirm_keys and source != DM:
            raise ValueError("Only the DM confirms names")
        await _move_aliases(w, keep_id, gone_id, frozenset(confirm_keys))
        await merge_restore.move_sheets(w.conn, w.ids, keep_id, gone_id)
        own = await w.select(
            ALIASES, " AND entity_id = %s AND key = %s", [keep_id, lookup_key(keep["name"])]
        )
        if own and own[0]["secret"]:
            # The other entry's name was a secret one: the main name everyone sees
            # must never be.
            raise MemoryRuleError("A secret name can't be the main name.")
        await _move_relations(w, keep_id, gone_id)
        await _move_standing(w, keep_id, gone_id)
        await merge_restore.move_notes(w.conn, w.ids, keep_id, gone_id)
        await beliefs.fold_perspective(w, keep_id, gone_id)
        for table in (MENTIONS, CORRECTIONS):  # one statement each (#164)
            await w.update_where(table, {"entity_id": keep_id}, " AND entity_id = %s", [gone_id])
        await w.update(ENTITIES, keep_id, {"status": _stronger(keep["status"], gone["status"])})
        # Older merges pointing at gone_id now chain to keep_id; `resolve` follows it.
        await w.update(
            ENTITIES,
            gone_id,
            {"status": MERGED, "merged_into": keep_id, **_note_cleared(gone)},
        )
        await _forget_quotes(w, [gone_id] if _note_cleared(gone) else [])
        await _sync_plays(w, keep_id)
        await _sync_plays(w, gone_id)
        # The merged entry's status change above always logs (it was not merged yet), so the
        # batch is known.
        assert w.batch is not None
        await merge_restore.save(w.conn, w.ids, w.batch, keep_id, gone_id, before_moves)

    async def check_people(
        self, guild_id: int, campaign_id: str, dm_ids: Collection[int]
    ) -> Written[int]:
        """After a session (#1188): make every played character have its person and
        `plays` link (a backup from before people were entries has none), then mark a
        person who plays nothing and isn't a DM as "needs a look", so the DM can say who
        they are. Returns how many are marked. A person already looked at is left alone."""
        # DMbot's own check, written as the scan: it must not look like something the DM did
        # (Stop recording me keeps what the DM touched).
        async with self._write(guild_id, campaign_id, "scan") as w:
            for row in await w.select(
                ENTITIES,
                " AND role = %s AND played_by IS NOT NULL AND status IN ('proposed', 'confirmed')",
                [PLAYER_CHARACTER],
            ):
                await _sync_plays(w, row["id"])
            playing = {
                r["subject_id"] for r in await w.select(RELATIONS, " AND predicate = %s", [PLAYS])
            }
            marked = 0
            for row in await w.select(
                ENTITIES,
                " AND type = 'person' AND status IN ('proposed', 'confirmed')",
                [],
            ):
                if row["id"] in playing or row["needs_look"] or row["played_by"] in dm_ids:
                    continue
                await w.update(ENTITIES, row["id"], {"needs_look": True})
                marked += 1
            return Written(marked, w.batch)

    # ---- people learned from "a name, then an answer" (#1188 part 2) ---------------------

    async def learn_people(
        self,
        guild_id: int,
        campaign_id: str,
        answered: Sequence[tuple[str, int, int]],
        *,
        limit: int,
    ) -> Written[list[PeopleSuggestion]]:
        """After a session: `answered` is (name said, Discord user who answered, times).
        A name nothing knows becomes a waiting name on that person; a character nobody
        plays becomes a waiting "plays" link. Anything else (a character someone else
        plays, a name that is already known) changes nothing: it is only counted by the
        caller. At most `limit`, nothing is true until the DM says so, and a name or link
        the DM already turned down is never asked again."""
        made: list[PeopleSuggestion] = []
        async with self._write(guild_id, campaign_id, "scan") as w:
            known = {str(r["key"]) for r in await w.select(ALIASES, "")} | {
                str(r["heard_key"]) for r in await w.select(CORRECTIONS, "")
            }
            live = {
                str(r["id"]): r
                for r in await w.select(ENTITIES, " AND status IN ('proposed', 'confirmed')")
            }
            by_key: dict[str, set[str]] = {}
            for r in await w.select(ALIASES, " AND status <> 'rejected' AND NOT secret"):
                if str(r["entity_id"]) in live:
                    by_key.setdefault(str(r["key"]), set()).add(str(r["entity_id"]))
            played = {
                str(r["object_id"])
                for r in await w.select(RELATIONS, " AND predicate = %s", [PLAYS])
            }
            asked: set[tuple[str, str]] = set()  # one question for each pair
            # Questions still waiting from earlier sessions count toward the cap.
            waiting = len(
                await w.select(ALIASES, " AND status = 'proposed' AND source = 'scan'")
            ) + len(
                await w.select(
                    RELATIONS,
                    " AND predicate = %s AND status = 'proposed' AND source = 'scan'",
                    [PLAYS],
                )
            )
            for name, user_id, times in answered:
                if len(made) + waiting >= limit:
                    break
                try:
                    key = lookup_key(name)
                    name = clean_text(name)
                except MemoryRuleError:
                    continue
                if is_dm_word(name) or (key, str(user_id)) in asked:
                    continue
                asked.add((key, str(user_id)))
                owners = by_key.get(key, set())
                if key not in known:
                    person = await _ensure_person(w, user_id)
                    row = await w.insert(
                        ALIASES, _new_alias(w, person, name, "nickname", PROPOSED, False, None)
                    )
                    made.append(PeopleSuggestion("name", user_id, name, row["id"], None, times))
                    known.add(key)
                elif len(owners) == 1:
                    (character,) = owners
                    entity = live[character]
                    if entity["type"] == PERSON or character in played:
                        continue
                    if entity["played_by"] is not None:
                        continue  # someone plays it already
                    if not _is_character(entity):
                        continue
                    person = await _ensure_person(w, user_id)
                    link = Relation(
                        new_id(), person, PLAYS, character, "", 0.5, PROPOSED, w.source,
                        (), None, None, None, None, False, w.now, PLAYS_VERSION,
                    )  # fmt: skip
                    await w.insert(RELATIONS, _relation_row(link))
                    played.add(character)
                    made.append(
                        PeopleSuggestion(
                            "plays", user_id, entity["name"], link.id, character, times
                        )
                    )
            return Written(made, w.batch)

    async def people_suggestions(self, guild_id: int, campaign_id: str) -> list[PeopleSuggestion]:
        """The questions still waiting, oldest first (Later keeps one waiting)."""
        async with self._read(guild_id, campaign_id) as scope:
            people = {
                str(r["id"]): r
                for r in await scope.select(
                    ENTITIES, " AND type = 'person' AND status IN ('proposed', 'confirmed')"
                )
            }
            out: list[PeopleSuggestion] = []
            for a in await scope.select(
                ALIASES, " AND status = 'proposed' AND source = 'scan' ORDER BY created_at, id"
            ):
                person = people.get(str(a["entity_id"]))
                if person is not None and person["played_by"] is not None:
                    out.append(
                        PeopleSuggestion(
                            "name",
                            int(person["played_by"]),
                            a["text"],
                            a["id"],
                            None,
                            0,
                            person["name"],
                        )
                    )
            entities = {
                str(r["id"]): r for r in await scope.select(ENTITIES, " AND status <> 'merged'")
            }
            for r in await scope.select(
                RELATIONS,
                " AND predicate = %s AND status = 'proposed' AND source = 'scan'"
                " ORDER BY created_at, id",
                [PLAYS],
            ):
                person = people.get(str(r["subject_id"]))
                character = entities.get(str(r["object_id"]))
                if person is not None and person["played_by"] is not None and character is not None:
                    out.append(
                        PeopleSuggestion(
                            "plays", int(person["played_by"]), character["name"], r["id"],
                            character["id"], 0, person["name"],
                        )
                    )  # fmt: skip
            return out

    async def table_people(
        self, guild_id: int, campaign_id: str
    ) -> list[tuple[int, list[str], list[str]]]:
        """Who is who at this table (#1188): for each person, their Discord user, the
        characters they play (the confirmed `plays` links) and the other names the DM
        confirmed for them. Names only, no secret names. Not the person's own (placeholder)
        name: the caller shows the Discord name. Kept per campaign until its memory changes
        (one cheap version read decides), so a busy table doesn't rebuild it for every question."""
        key = (guild_id, campaign_id)
        cached = self._people.get(key)
        if cached is not None and cached[0] == await self.version(guild_id, campaign_id):
            return [(u, list(c), list(a)) for u, c, a in cached[1]]
        async with self._read(guild_id, campaign_id, snapshot=True) as scope:
            # Two people made in the same second keep a fixed order: ties fall to the Discord id,
            # never to a random entry id (#1241).
            people = {
                str(r["id"]): r
                for r in await scope.select(
                    ENTITIES,
                    " AND type = 'person' AND status = 'confirmed'"
                    " ORDER BY created_at, played_by, id",
                )
            }
            if not people:
                self._keep_people(key, scope.version, [])
                return []
            live = await scope.select(
                ENTITIES, " AND status IN ('proposed', 'confirmed') AND type <> 'person'"
            )
            characters = {str(r["id"]): str(r["name"]) for r in live}
            plays: dict[str, list[str]] = {}
            for r in await scope.select(
                RELATIONS,
                " AND predicate = %s AND status = 'confirmed' AND NOT secret ORDER BY created_at",
                [PLAYS],
            ):
                name = characters.get(str(r["object_id"]))
                if name is not None and str(r["subject_id"]) in people:
                    plays.setdefault(str(r["subject_id"]), []).append(name)
            # The player kept on the character itself (the mirror) counts too, so a character
            # linked only that way is not missed; each name once.
            by_user = {int(r["played_by"]): pid for pid, r in people.items() if r["played_by"]}
            for r in live:
                pid = by_user.get(int(r["played_by"])) if r["played_by"] is not None else None
                if pid is not None and r["role"] == PLAYER_CHARACTER:
                    names = plays.setdefault(pid, [])
                    if str(r["name"]) not in names:
                        names.append(str(r["name"]))
            also: dict[str, list[str]] = {}
            for a in await scope.select(
                ALIASES, " AND status = 'confirmed' AND NOT secret ORDER BY created_at, id"
            ):
                owner = people.get(str(a["entity_id"]))
                if owner is not None and a["text"] != owner["name"]:
                    also.setdefault(str(a["entity_id"]), []).append(str(a["text"]))
            result = [
                (int(r["played_by"]), plays.get(pid, []), also.get(pid, []))
                for pid, r in people.items()
                if r["played_by"] is not None
            ]
            self._keep_people(key, scope.version, result)
            return result

    def _keep_people(
        self, key: tuple[int, str], version: int, result: list[tuple[int, list[str], list[str]]]
    ) -> None:
        """Remember who is who at the version of the snapshot just read (so a change after it
        is never hidden). Past the limit the oldest entry goes, not the whole cache."""
        self._people.pop(key, None)
        if len(self._people) >= PEOPLE_CACHE_MAX:
            del self._people[next(iter(self._people))]
        self._people[key] = (version, [(u, list(c), list(a)) for u, c, a in result])

    async def add_person_names(
        self,
        guild_id: int,
        campaign_id: str,
        user_id: int,
        names: Sequence[str],
        plays: Sequence[str] = (),
    ) -> Written[PersonAdded]:
        """A campaign DM's list says what the table calls a person and which characters they
        play (#1559). The person is made if there is none. Each name is added as one the DM
        confirmed, never replacing, never twice, and never the `Player 1234` placeholder. A
        character that exists and has no player (or is already theirs) is linked as theirs;
        any other (played by someone else, or not known) is left alone and the person is marked
        "needs a look", as the learner does. This is a change of its own per person: the list's Undo
        does not take it back."""
        async with self._write(guild_id, campaign_id, DM) as w:
            person = await _ensure_person(w, user_id)
            entry = await w.get(ENTITIES, person)
            if entry is not None and entry["status"] != CONFIRMED:  # DMbot only suggested it
                await w.update(ENTITIES, person, {"status": CONFIRMED})
            seen = {
                str(r["key"]): r for r in await w.select(ALIASES, " AND entity_id = %s", [person])
            }
            seen[lookup_key(_placeholder_name(user_id))] = {"status": CONFIRMED}
            added = 0
            for text in names:
                key = lookup_key(text)
                old = seen.get(key)
                if old is None:
                    seen[key] = {"status": CONFIRMED}
                    await w.insert(
                        ALIASES, _new_alias(w, person, text, "nickname", CONFIRMED, False, None)
                    )
                    added += 1
                elif old["status"] == PROPOSED:  # DMbot only guessed it: the DM has said it now
                    await w.update(ALIASES, str(old["id"]), {"status": CONFIRMED, "source": DM})
                    old["status"] = CONFIRMED
                    added += 1
            characters = {
                lookup_key(str(r["name"])): r
                for r in await w.select(
                    ENTITIES, " AND type = 'character' AND status IN ('proposed', 'confirmed')", []
                )
            }
            linked: list[str] = []
            flagged: list[str] = []
            for text in plays:
                row = characters.get(lookup_key(text))
                # An NPC or a god is not turned into a player character by a list: it is left
                # alone and flagged like any other character that can't be linked.
                if (
                    row is not None
                    and row["played_by"] in (None, user_id)
                    and row["role"] in (None, PLAYER_CHARACTER)
                ):
                    if row["played_by"] is None or row["role"] != PLAYER_CHARACTER:
                        await w.update(
                            ENTITIES, row["id"], {"role": PLAYER_CHARACTER, "played_by": user_id}
                        )
                    await _sync_plays(w, str(row["id"]))
                    linked.append(str(row["name"]))
                else:
                    flagged.append(text)
            if flagged:
                await w.update(ENTITIES, person, {"needs_look": True})
            return Written(PersonAdded(added, tuple(linked), tuple(flagged)), w.batch)

    async def accept_person_name(
        self, guild_id: int, campaign_id: str, alias_id: str, *, user_id: int | None = None
    ) -> Written[None]:
        """The DM says "yes, that's them" (or, with `user_id`, "it's someone else": the
        name moves to that person). Now it is the DM's own: Stop recording me no longer
        removes it."""
        async with self._write(guild_id, campaign_id, DM) as w:
            alias = await w.get(ALIASES, alias_id)
            if alias is None or alias["status"] != PROPOSED or not await _on_person(w, alias):
                raise MemoryRuleError(NOT_FOUND)
            if user_id is None:
                await w.update(ALIASES, alias_id, {"status": CONFIRMED, "source": DM})
                return Written(None, w.batch)
            person = await _ensure_person(w, user_id)
            await w.delete(ALIASES, alias_id)
            if not await w.select(
                ALIASES, " AND entity_id = %s AND key = %s", [person, alias["key"]]
            ):
                await w.insert(
                    ALIASES,
                    _new_alias(w, person, alias["text"], "nickname", CONFIRMED, False, None),
                )
            return Written(None, w.batch)

    async def reject_person_name(
        self, guild_id: int, campaign_id: str, alias_id: str
    ) -> Written[None]:
        """ "Not a player": the name goes, and is never asked again (a keep-as-heard rule)."""
        async with self._write(guild_id, campaign_id, DM) as w:
            alias = await w.get(ALIASES, alias_id)
            if alias is None or alias["status"] != PROPOSED or not await _on_person(w, alias):
                raise MemoryRuleError(NOT_FOUND)
            await w.delete(ALIASES, alias_id)
            await w.insert(
                CORRECTIONS,
                {
                    "id": new_id(),
                    "heard": alias["text"],
                    "heard_key": alias["key"],
                    "entity_id": None,
                    "action": KEEP,
                    "source": DM,
                    "created_at": w.now,
                },
            )
            return Written(None, w.batch)

    async def decide_plays(
        self, guild_id: int, campaign_id: str, relation_id: str, *, yes: bool
    ) -> Written[None]:
        """ "Does Mia play Cerric?" Yes: the character becomes a player character played by
        that person, set by the DM. No: the link is kept as turned down, so it isn't asked
        again."""
        async with self._write(guild_id, campaign_id, DM) as w:
            link = await w.get(RELATIONS, relation_id)
            if link is None or link["predicate"] != PLAYS or link["status"] != PROPOSED:
                raise MemoryRuleError(NOT_FOUND)
            if not yes:
                await w.update(RELATIONS, relation_id, {"status": "rejected", "source": DM})
                return Written(None, w.batch)
            person = await _entity_row(w, link["subject_id"])
            character = await _entity_row(w, link["object_id"])
            if character["played_by"] is not None:
                raise MemoryRuleError(CHANGED_SINCE)  # the DM gave it to someone meanwhile
            await w.update(
                ENTITIES,
                link["object_id"],
                {"role": PLAYER_CHARACTER, "played_by": person["played_by"], "needs_look": False},
            )
            await w.delete(RELATIONS, relation_id)  # _sync_plays makes the DM's own link
            await _sync_plays(w, link["object_id"])
            return Written(None, w.batch)

    async def apply_import_plays(
        self, guild_id: int, campaign_id: str, character_id: str, user_id: int
    ) -> Written[bool]:
        """The DM's "plays which character" choice from the import page (#2135), made a link when
        the bot gets to it. The DM may have changed things since, so it is judged again here,
        in one write: the character must still be a confirmed character that nobody plays, that
        everyone may know, whose own name is a confirmed, non-secret one (the transcript-export
        rule). Anything else leaves it alone and returns False. It sets the role and the player
        only: names waiting for the DM stay waiting."""
        if not 0 < user_id <= INT64_MAX:
            raise ValueError("Bad Discord user ID")
        async with self._write(guild_id, campaign_id, DM) as w:
            rows = await w.select(ENTITIES, " AND id = %s", [character_id])
            if not rows:
                return Written(False, w.batch)
            row = rows[0]
            if (
                row["type"] != "character"
                or row["status"] != CONFIRMED
                or row["played_by"] is not None
                or row["audience_limited"]
            ):
                return Written(False, w.batch)
            named = sorted(
                await w.select(
                    ALIASES,
                    " AND entity_id = %s AND text = %s AND status <> 'rejected'",
                    [character_id, row["name"]],
                ),
                key=lambda a: str(a["id"]),
            )
            own = named[0]["key"] if named else None
            if not any(
                a["status"] == CONFIRMED and not a["secret"] and a["key"] == own for a in named
            ):
                return Written(False, w.batch)
            await w.update(
                ENTITIES,
                character_id,
                {"role": PLAYER_CHARACTER, "played_by": user_id, "needs_look": False},
            )
            await _sync_plays(w, character_id)
            return Written(True, w.batch)

    async def forget_learned(self, guild_id: int, user_id: int) -> int:
        """Stop recording me (#1188): in every campaign of the server, remove the names
        and links DMbot *learned* for this person (not yet accepted), also from the
        change log so an Undo can't bring them back. What the DM set by hand stays."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT DISTINCT campaign_id FROM memory_entities WHERE guild_id = %s"
                " AND type = 'person' AND played_by = %s",
                (guild_id, user_id),
            )
            campaigns = [str(r["campaign_id"]) for r in await cur.fetchall()]
        removed = 0
        for campaign_id in campaigns:
            async with self._write(guild_id, campaign_id, "scan") as w:
                people = [
                    str(r["id"])
                    for r in await w.select(
                        ENTITIES, " AND type = 'person' AND played_by = %s", [user_id]
                    )
                ]
                if not people:
                    continue
                aliases = [
                    str(r["id"])
                    for r in await w.select(
                        ALIASES,
                        " AND entity_id = ANY(%s) AND source = 'scan' AND status = 'proposed'",
                        [people],
                    )
                ]
                links = [
                    str(r["id"])
                    for r in await w.select(
                        RELATIONS,
                        " AND subject_id = ANY(%s) AND predicate = %s AND source = 'scan'"
                        " AND status = 'proposed'",
                        [people, PLAYS],
                    )
                ]
                if aliases:
                    await w.conn.execute(
                        "DELETE FROM memory_aliases WHERE guild_id = %s AND campaign_id = %s"
                        " AND id = ANY(%s)",
                        (*w.ids, aliases),
                    )
                if links:
                    await w.conn.execute(
                        "DELETE FROM memory_relations WHERE guild_id = %s AND campaign_id = %s"
                        " AND id = ANY(%s)",
                        (*w.ids, links),
                    )
                for table_name, ids in (("memory_aliases", aliases), ("memory_relations", links)):
                    if ids:
                        await w.conn.execute(
                            "DELETE FROM memory_changes WHERE guild_id = %s AND campaign_id = %s"
                            " AND table_name = %s AND row_id = ANY(%s)",
                            (*w.ids, table_name, ids),
                        )
                removed += len(aliases) + len(links)
                # The placeholder entry DMbot made for them (`Player 1234`), when nothing
                # else hangs on it, goes too: a stopped speaker leaves no entry behind.
                for person_id in people:
                    if await _unused_placeholder(w, person_id, user_id):
                        await _drop_person(w, person_id)
                        removed += 1
        return removed

    async def forget_alias_user(self, guild_id: int, user_id: int) -> int:
        """Delete my past transcripts (#1532): in every campaign of the server, take this person
        out of any alias's "who uses it" field (the aliases whose user is one of the player
        characters they play). The alias and its name stay: the name is the campaign's. Goes
        through the write path, so the version moves and running copies reload, and the edit is
        dropped from the undo log so an Undo can't put the person back. Returns how many
        aliases changed."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT DISTINCT campaign_id FROM memory_entities WHERE guild_id = %s"
                " AND played_by = %s",
                (guild_id, user_id),
            )
            campaigns = [str(r["campaign_id"]) for r in await cur.fetchall()]
        changed = 0
        for campaign_id in campaigns:
            async with self._write(guild_id, campaign_id, "scan") as w:
                people = [
                    str(r["id"]) for r in await w.select(ENTITIES, " AND played_by = %s", [user_id])
                ]
                aliases = [
                    str(r["id"])
                    for r in await w.select(ALIASES, " AND used_by = ANY(%s)", [people])
                ]
                for alias_id in aliases:
                    await w.update(ALIASES, alias_id, {"used_by": None})
                if aliases:
                    await w.conn.execute(
                        "DELETE FROM memory_changes WHERE guild_id = %s AND campaign_id = %s"
                        " AND batch = %s",
                        (*w.ids, w.batch),
                    )
                changed += len(aliases)
        return changed

    async def recheck_definitions(self, guild_id: int, campaign_id: str) -> Written[int]:
        """After a session (#1189): facts written under an older version of a relationship
        whose meaning has changed are checked against the definition they are held to now. One
        that no longer fits becomes a flag for the DM, never a silent fix; the fact then
        carries the current version so it is looked at once. Returns how many facts were
        looked at."""
        async with self._write(guild_id, campaign_id, "scan") as w:
            onto = await _load_ontology(w)
            # A campaign's own relationship the core has since taken the name of (a custom
            # `sworn_to`): the core one replaces it. The campaign's row is kept as deprecated
            # ("replaced by" the core key, version 1 = not looked at yet) and its facts are
            # looked at against the core meaning below; version 2 marks that it was done.
            core_keys = {p.key for p in CORE_PREDICATES}
            replaced: set[str] = set()
            for r in await w.select(PREDICATES):
                key = str(r["key"])
                if key not in core_keys:
                    continue
                if r["status"] == ACTIVE:
                    await w.update(
                        PREDICATES, key, {"status": DEPRECATED, "replaced_by": key, "version": 1}
                    )
                    replaced.add(key)
                elif r["replaced_by"] == key and int(r["version"]) == 1:
                    replaced.add(key)
            behind = [
                r
                for r in await w.select(RELATIONS, " AND status IN ('proposed', 'confirmed')")
                if r["predicate"] in onto.predicates
                and (
                    r["predicate"] in replaced
                    or int(r["definition_version"]) < onto.predicates[r["predicate"]].version
                )
            ]
            if not behind and not replaced:
                return Written(0, None)
            types = {
                str(r["id"]): str(r["type"])
                for r in await w.select(ENTITIES, " AND status IN ('proposed', 'confirmed')")
            }
            for row in behind:
                rel = _relation(row)
                if rel.subject_id in types and rel.object_id in types:
                    existing = [
                        e for e in await _relations_touching(w, rel.subject_id, rel.object_id)
                        if e.id != rel.id
                    ]  # fmt: skip
                    await _flag_problems(
                        w, onto, rel, types[rel.subject_id], types[rel.object_id], existing
                    )
                await w.update(
                    RELATIONS,
                    rel.id,
                    {"definition_version": onto.predicates[rel.predicate].version},
                )
            for key in replaced:
                await w.update(PREDICATES, key, {"version": 2})  # looked at: not again
            return Written(len(behind), w.batch)

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
        unrevealed: bool = False,
    ) -> Written[tuple[Relation, list[Flag]]]:
        """Store a fact, checked against the rules. Problems become flags for the DM to
        review; the fact is stored as given either way. A fact already known is only
        upgraded (the DM's confirmation, or secret=True); one the DM rejected stays
        rejected unless the DM says it again. `unrevealed`: the story holds it back from the
        players until the table has said it (#2404), the DM's to say."""
        async with self._write(guild_id, campaign_id, source) as w:
            value = await self.add_relation_in(
                w, subject_id, predicate, object_id, confidence=confidence, status=status,
                detail=detail, from_session_at=from_session_at, to_session_at=to_session_at,
                secret=secret, mention_ids=mention_ids, unrevealed=unrevealed,
            )  # fmt: skip
            return Written(value, w.batch)

    async def add_relation_in(
        self,
        w: Changes,
        subject_id: str,
        predicate: str,
        object_id: str,
        *,
        confidence: float,
        status: str = PROPOSED,
        detail: str = "",
        from_session_at: int | None = None,
        to_session_at: int | None = None,
        secret: bool = False,
        mention_ids: Sequence[str] = (),
        unrevealed: bool = False,
    ) -> tuple[Relation, list[Flag]]:
        """`add_relation` inside a write that is already open (#1301), with the same checks;
        the write's own source is the fact's source. The caller opened `w` for the right
        campaign."""
        source = w.source
        if unrevealed and source != DM:
            raise MemoryRuleError("Only the DM can say that.")
        _check_choice(status, LIVE, "relation status")
        check_status_change(None, status, source)
        _check_confidence(confidence)
        _check_span(from_session_at, to_session_at)
        _check_list(mention_ids, "mentions")
        if len(detail) > DETAIL_MAX:
            raise MemoryRuleError(f"That's too long (at most {DETAIL_MAX} characters).")
        if not all(is_id(m) for m in mention_ids):
            raise ValueError("Bad mention ID")
        if subject_id == object_id:
            raise MemoryRuleError("A relationship needs two different entries.")
        onto = await _load_ontology(w)
        pred = onto.active_predicate(predicate)
        if pred.key == PLAYS:
            # Who plays a character is set on the character, then kept here (#1188).
            raise MemoryRuleError("Say who plays a character on the character itself.")
        if pred.key == threads_mod.LED_TO:
            # Whatever the path (extractor, "Connect to…"), a "led to" link is the DM's secret
            # and may not close a loop (#1679).
            secret = True
            for end in (subject_id, object_id):
                if (await _entity_row(w, end))["status"] != CONFIRMED:
                    raise MemoryRuleError(LED_TO_CONFIRM)
            live = await w.select(
                RELATIONS,
                " AND predicate = 'led_to' AND status = 'confirmed' AND to_session_at IS NULL",
                [],
            )
            if threads_mod.would_loop(
                [(r["subject_id"], r["object_id"]) for r in live], subject_id, object_id
            ):
                raise MemoryRuleError(LED_TO_LOOP)
        types = {
            subject_id: (await _entity_row(w, subject_id))["type"],
            object_id: (await _entity_row(w, object_id))["type"],
        }
        if mention_ids:
            found = await w.select(MENTIONS, " AND id = ANY(%s)", [list(mention_ids)])
            if len(found) != len(set(mention_ids)):
                raise MemoryRuleError(NOT_FOUND)
        s, o = ordered(pred, subject_id, object_id)
        new = Relation(
            new_id(), s, predicate, o, " ".join(detail.split()), confidence, status, source,
            tuple(mention_ids), from_session_at, to_session_at, None, None, secret, w.now,
            pred.version, unrevealed,
        )  # fmt: skip
        existing = await _relations_touching(w, s, o)
        same = duplicate_of(new, existing)
        if same is not None:
            upgrade = _upgrade(_relation_row(same), status, secret, source)
            if not upgrade:
                return same, []
            row = await w.update(RELATIONS, same.id, upgrade)
            return _relation(row), []
        await w.insert(RELATIONS, _relation_row(new))
        flags = await _flag_problems(w, onto, new, types[s], types[o], existing)
        return new, flags

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
        unrevealed: bool | None = None,
    ) -> Written[Relation]:
        """Confirm or reject a fact, mark it secret, or end it (history is kept: a new
        fact replaces it rather than overwriting it). `unrevealed=False` is the DM's 👁️ Reveal
        to players on a connection (#2404): both its ends must be shown to the players first."""
        async with self._write(guild_id, campaign_id, source) as w:
            current = await w.get(RELATIONS, relation_id)
            if current is None:
                raise MemoryRuleError(NOT_FOUND)
            changes: dict[str, Any] = {}
            if status is not None:
                _check_choice(status, FACT_STATUSES, "relation status")
                check_status_change(current["status"], status, source)
                changes["status"] = status
            if secret is not None:
                if current["predicate"] == threads_mod.LED_TO and not secret:
                    raise MemoryRuleError("A “led to” link stays the DM's alone.")
                if current["secret"] and not secret and source != DM:
                    raise MemoryRuleError("Only the DM can stop keeping that secret.")
                changes["secret"] = secret
            if unrevealed is not None and unrevealed != current["unrevealed"]:
                if source != DM:
                    raise MemoryRuleError("Only the DM can say that.")
                if not unrevealed:
                    ends = await w.select(
                        ENTITIES,
                        " AND id = ANY(%s) AND NOT hidden_from_players AND NOT audience_limited",
                        [[current["subject_id"], current["object_id"]]],
                    )
                    if len(ends) < 2:
                        raise MemoryRuleError(
                            "Show both names to the players first, then this connection."
                        )
                changes["unrevealed"] = unrevealed
            if to_session_at is not None:
                _check_span(current["from_session_at"], to_session_at)
                changes["to_session_at"] = to_session_at
            row = await w.update(RELATIONS, relation_id, changes)
            return Written(_relation(row), w.batch)

    # ---- beliefs: who believes a connection (#2409; docs/STORY_MEMORY.md, section 9) -----------

    async def perspectives(self, guild_id: int, campaign_id: str) -> list[Perspective]:
        """Who holds beliefs in this campaign: The truth and The party, then any character's."""
        async with self._read(guild_id, campaign_id, snapshot=True) as scope:
            rows = await scope.select(PERSPECTIVES, " ORDER BY created_at, id")
        return [Perspective(r["id"], r["kind"], r["entity_id"], r["created_at"]) for r in rows]

    async def believe(
        self,
        guild_id: int,
        campaign_id: str,
        perspective_id: str,
        relation_id: str,
        confidence: float,
        status: str,
        *,
        source: str,
        evidence: Sequence[str] = (),
        session_at: int | None = None,
    ) -> Written[Belief]:
        """A perspective comes to believe a connection at `confidence` (0.0 certainly false to 1.0
        certainly true), with a status and the evidence it points at by id. A new row, never an
        edit, so the history stays; logged, so Undo takes it back. Only the DM writes The truth.
        The one way every writer records a belief."""
        async with self._write(guild_id, campaign_id, source) as w:
            await beliefs.perspective_ids(w)  # The truth and The party exist before anything else
            belief = await beliefs.believe_in(
                w, perspective_id, relation_id, confidence, status,
                evidence=evidence, session_at=session_at,
            )  # fmt: skip
            return Written(belief, w.batch)

    async def party_view(
        self, guild_id: int, campaign_id: str, *, known_only: bool = True
    ) -> list[Belief]:
        """What The party believes of the confirmed connections: the current belief for each. With
        `known_only` (what players are shown today) just those held as fact, at 1.0; without, the
        party's theories too. A connection with no party belief is not in it (a secret one)."""
        return await self._view(guild_id, campaign_id, "party", known_only)

    async def truth_view(self, guild_id: int, campaign_id: str) -> list[Belief]:
        """What The truth holds of the confirmed connections, secret ones included: the current
        belief for each, the DM's view."""
        return await self._view(guild_id, campaign_id, "truth", False)

    async def _view(
        self, guild_id: int, campaign_id: str, kind: str, known_only: bool
    ) -> list[Belief]:
        async with self._read(guild_id, campaign_id, snapshot=True) as scope:
            cur = await scope.conn.execute(
                "SELECT b.id, b.perspective_id, b.relation_id, b.confidence, b.status,"
                " b.session_at, b.evidence_ids, b.seq, b.recorded_at FROM ("
                "  SELECT DISTINCT ON (b.relation_id) b.*, r.secret AS relation_secret"
                "    FROM memory_beliefs b"
                "    JOIN memory_perspectives p ON p.guild_id = b.guild_id"
                "     AND p.campaign_id = b.campaign_id AND p.id = b.perspective_id"
                "    JOIN memory_relations r ON r.guild_id = b.guild_id"
                "     AND r.campaign_id = b.campaign_id AND r.id = b.relation_id"
                "   WHERE b.guild_id = %s AND b.campaign_id = %s AND p.kind = %s"
                "     AND r.status = 'confirmed'"
                "   ORDER BY b.relation_id, b.seq DESC) b"
                " WHERE (NOT %s OR b.confidence >= 1.0) AND b.confidence > 0"
                # defence in depth: the players never hold a secret connection as fact
                " AND (%s <> 'party' OR NOT (b.confidence >= 1.0 AND b.relation_secret))"
                " ORDER BY b.relation_id",
                (*scope.ids, kind, known_only, kind),
            )
            return [beliefs.belief_of(r) for r in await cur.fetchall()]

    async def relation(self, guild_id: int, campaign_id: str, relation_id: str) -> Relation | None:
        """One connection of any status (rejected ones too), or None. For a retried job to tell
        "already done" from "never there"; nothing else lists a rejected connection."""
        async with self._read(guild_id, campaign_id, snapshot=True) as scope:
            rows = await scope.select(RELATIONS, " AND id = %s", [relation_id])
            return _relation(rows[0]) if rows else None

    async def change_relation_words(
        self,
        guild_id: int,
        campaign_id: str,
        relation_id: str,
        predicate: str,
        detail: str,
        *,
        source: str,
    ) -> Written[Relation]:
        """New words between the same two names, in ONE change, so one Undo puts the old words
        back whole. The new connection is added (keeping who sees it) and the old one ended in the
        same write, so a refusal leaves the old as it was. Words that only differ in capitals are
        the same connection (the duplicate rule): nothing is ended, and the connection comes back
        as it is. Words that already exist between the two names reuse that connection, and a
        secret one never becomes public."""
        async with self._write(guild_id, campaign_id, source) as w:
            current = await w.get(RELATIONS, relation_id)
            if current is None or current["status"] != CONFIRMED:
                raise MemoryRuleError(NOT_FOUND)
            before = {
                r.id: r.status
                for r in await _relations_touching(w, current["subject_id"], current["object_id"])
            }
            new, _ = await self.add_relation_in(
                w, current["subject_id"], predicate, current["object_id"], confidence=1.0,
                status=CONFIRMED, detail=detail, secret=bool(current["secret"]),
                unrevealed=bool(current["unrevealed"]),
            )  # fmt: skip
            if new.id == relation_id:
                return Written(new, w.batch)
            # The words are new, or match a duplicate only proposed so far (the upgrade just made it
            # the DM's, with no beliefs yet): the new connection takes over (#2409)
            if before.get(new.id) != CONFIRMED:
                await beliefs.carry(w, current, new.id)
                moved = await w.get(RELATIONS, new.id)
                assert moved is not None
                new = _relation(moved)
            if current["secret"] and not new.secret:
                new = _relation(await w.update(RELATIONS, new.id, {"secret": True}))
            check_status_change(current["status"], REJECTED, source)
            await w.update(RELATIONS, relation_id, {"status": REJECTED})
            return Written(new, w.batch)

    async def resolve_flag(
        self, guild_id: int, campaign_id: str, flag_id: str, *, source: str
    ) -> Written[Flag]:
        async with self._write(guild_id, campaign_id, source) as w:
            row = await w.update(FLAGS, flag_id, {"status": "resolved"})
            return Written(_flag(row), w.batch)

    async def prune_changes(self, guild_id: int, campaign_id: str) -> int:
        """Delete this campaign's change-log rows older than `keep_days` (#164), so the
        log doesn't outgrow the memory. Whole batches go together (a batch's rows share
        one time), and an undo is never older than what it undid, so "already undone"
        stays true; Undo on a pruned batch says it's too late (`TooLateToUndo`). Runs only
        after a session, so Undo works for at least `keep_days`. Returns how many rows
        went."""
        cutoff = int(self._clock()) - self.keep_days * DAY
        # A plain server-scoped transaction, no campaign lock: it touches only rows too old
        # for any write in progress, and an undo reading them sees all of a batch or none.
        async with self._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "DELETE FROM memory_changes"
                " WHERE guild_id = %s AND campaign_id = %s AND made_at < %s",
                (*scope.ids, cutoff),
            )
            pruned = cur.rowcount
            # what a merge moved goes with the batch it belongs to (#2438)
            await scope.conn.execute(
                "DELETE FROM merge_restores r WHERE r.guild_id = %s AND r.campaign_id = %s"
                " AND NOT EXISTS (SELECT 1 FROM memory_changes c WHERE c.guild_id = r.guild_id"
                " AND c.campaign_id = r.campaign_id AND c.batch = r.batch)",
                scope.ids,
            )
            return pruned

    async def resolve_stale_flags(self, guild_id: int, campaign_id: str) -> Written[list[Flag]]:
        """Close the open flags whose problem is gone (#164): an undo, a merge, an edit
        or a rejected fact can end a clash without touching the flag. Each flagged fact
        is checked again exactly as when it was flagged; a flag whose problem is still
        found stays open. Logged like any change (as EntityBot's upkeep), so it can be
        undone. A few statements however many flags are open. Skipped when the memory
        hasn't changed since the last check (#347); the same flag found twice (a fact
        moved by a merge is checked again) is closed but for the oldest. The closed flags
        come back in id order."""
        checked = (guild_id, campaign_id)
        async with self._read(guild_id, campaign_id) as scope:  # no lock if nothing's open
            if self._flags_checked.get(checked) == scope.version:
                return Written([], None)
            if not await scope.select(FLAGS, " AND status = 'open' LIMIT 1"):
                self._flags_checked[checked] = scope.version
                return Written([], None)
        async with self._write(guild_id, campaign_id, "entitybot") as w:
            closed = await _close_stale_flags(w)
        self._flags_checked[checked] = w.version  # after its own closes, if any
        return Written(closed, w.batch)

    # ---- mentions and corrections -------------------------------------------------------

    async def add_session_heard(
        self, guild_id: int, campaign_id: str, session_started_at: int, heard: Sequence[Heard]
    ) -> int:
        """Keep how often names were said in a session, written once at its end; how
        many rows were written.

        Observations, not edits: no change log (nothing to undo) and no version change,
        so running copies don't reload (the caller drops its copy for next time). A name
        merged meanwhile counts for the one it was merged into; removed names are
        skipped. The caller has already left out people who stopped being recorded.
        """
        _check_time(session_started_at)
        rows = [
            h
            for h in heard
            if is_id(h.entity_id) and 0 < h.times <= 2**31 - 1 and 0 < h.speaker_id <= INT64_MAX
        ]
        if not rows:
            return 0
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "INSERT INTO memory_heard"
                " (guild_id, campaign_id, entity_id, session_started_at, speaker_id, times)"
                " SELECT %s, %s, coalesce(e.merged_into, e.id), %s, h.speaker_id,"
                " sum(h.times)"
                " FROM unnest(%s::text[], %s::bigint[], %s::int[])"
                " AS h(entity_id, speaker_id, times)"
                " JOIN memory_entities e ON e.guild_id = %s AND e.campaign_id = %s"
                " AND e.id = h.entity_id AND e.status IN ('proposed', 'confirmed', 'merged')"
                " GROUP BY 3, 5"
                " ON CONFLICT (guild_id, campaign_id, session_started_at, entity_id, speaker_id)"
                " DO UPDATE SET times = memory_heard.times + EXCLUDED.times",
                (
                    guild_id,
                    campaign_id,
                    session_started_at,
                    [h.entity_id for h in rows],
                    [h.speaker_id for h in rows],
                    [h.times for h in rows],
                    guild_id,
                    campaign_id,
                ),
            )
            return cur.rowcount

    # `memory_mentions` holds pointers, never words (#2329): the after-session name scan keeps
    # where a suggested name was said (a line reference and the place in it, `memory/places.py`),
    # and the review card reads the words from the session's own transcript when it is drawn.
    # (Until then nothing wrote it, #1740; "was this name heard in a session?" is still
    # `memory_heard`.)
    async def mentions(
        self, guild_id: int, campaign_id: str, entity_id: str
    ) -> list[tuple[str, int | None]]:
        """The pointers kept for one entry: (line reference, the session's start), earliest
        session first."""
        async with self._read(guild_id, campaign_id, snapshot=True) as scope:
            rows = await scope.select(
                MENTIONS,
                " AND entity_id = %s ORDER BY session_started_at NULLS LAST, created_at, id",
                [entity_id],
            )
            return [
                (
                    str(r["line_ref"]),
                    None if r["session_started_at"] is None else int(r["session_started_at"]),
                )
                for r in rows
            ]

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
        async with self._write(guild_id, campaign_id, source) as w:
            await _entity_row(w, entity_id)
            row = await w.insert(
                MENTIONS,
                {
                    "id": new_id(),
                    "entity_id": entity_id,
                    "session_started_at": session_started_at,
                    "line_ref": line_ref,
                    "span_start": span[0],
                    "span_end": span[1],
                    "confidence": confidence,
                    "method": method,
                    "created_at": w.now,
                },
            )
            return Written(str(row["id"]), w.batch)

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
        (`keep`: from an Undo or "Keep as heard"). Already known: nothing changes.
        When both exist for the same words, `keep` wins (a wrong fix is worse than a
        missed one); the Transcript Cleaner applies that."""
        _check_choice(action, (FIX, KEEP), "correction action")
        if (action == FIX) != (entity_id is not None):
            raise ValueError("A fix needs an entity; a keep must not have one")
        heard = clean_text(heard)
        key = lookup_key(heard)
        async with self._write(guild_id, campaign_id, source) as w:
            if entity_id is not None:
                await _entity_row(w, entity_id)
            for row in await w.select(CORRECTIONS, " AND heard_key = %s", [key]):
                if (row["action"], row["entity_id"]) == (action, entity_id):
                    return Written(_correction(row), w.batch)
            row = await w.insert(
                CORRECTIONS,
                {
                    "id": new_id(),
                    "heard": heard,
                    "heard_key": key,
                    "entity_id": entity_id,
                    "action": action,
                    "source": source,
                    "created_at": w.now,
                },
            )
            return Written(_correction(row), w.batch)

    async def add_typed_name(
        self, guild_id: int, campaign_id: str, heard: str, name: str
    ) -> Written[Entity]:
        """A name the DM typed for words heard, that DMbot didn't know (#503): a new
        name to check in 📝 Check new names, and the rule that those words are written
        that way, in one change (one Undo takes back both)."""
        name, heard = clean_text(name), clean_text(heard)
        key = lookup_key(heard)
        typed_key = lookup_key(name)  # too long: refused before anything is written
        async with self._write(guild_id, campaign_id, DM) as w:
            onto = await _load_ontology(w)
            onto.active_type("concept")
            # Checked here, not only in the session's copy of the names: never a second
            # copy of a name, and never a secret one written as a new public name.
            cur = await w.conn.execute(
                "SELECT 1 FROM memory_aliases WHERE guild_id = %s AND campaign_id = %s"
                " AND key = %s AND status <> 'rejected' AND entity_id IN"
                + _LIVE_ENTITY_IDS
                + " LIMIT 1",
                (*w.ids, typed_key, *w.ids),
            )
            if await cur.fetchone() is not None:
                raise MemoryRuleError("That name is already in this campaign.")
            row = await w.insert(
                ENTITIES,
                {
                    "id": new_id(),
                    "type": "concept",
                    "name": name,
                    "description": "Typed by the DM for a misheard word",
                    "status": PROPOSED,
                    "merged_into": None,
                    "source": DM,
                    "created_at": w.now,
                    "played_by": None,
                    "role": None,
                    "needs_look": False,
                    "unique_item": None,
                },
            )
            await w.insert(ALIASES, _new_alias(w, row["id"], name, "full", PROPOSED, False, None))
            await w.insert(
                CORRECTIONS,
                {
                    "id": new_id(),
                    "heard": heard,
                    "heard_key": key,
                    "entity_id": row["id"],
                    "action": FIX,
                    "source": DM,
                    "created_at": w.now,
                },
            )
            return Written(_entity(row), w.batch)

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
        _check_list(examples, "examples")
        async with self._write(guild_id, campaign_id, source) as w:
            onto = await _load_ontology(w)
            onto.check_new_term(term.key, term.label, term.description, examples, reason)
            onto.check_new_type(term)
            await _check_growth(w, session_started_at)
            row = await w.insert(
                TYPES,
                {
                    "key": term.key,
                    "parent": term.parent,
                    "label": term.label.strip(),
                    "description": term.description.strip(),
                    "examples": [e.strip() for e in examples],
                    "reason": reason.strip(),
                    "status": ACTIVE,
                    "replaced_by": None,
                    "created_at": w.now,
                },
            )
            return Written(_type_term(row), w.batch)

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
        _check_list(examples, "examples")
        async with self._write(guild_id, campaign_id, source) as w:
            onto = await _load_ontology(w)
            onto.check_new_term(term.key, term.label, term.description, examples, reason)
            onto.check_new_predicate(term)
            await _check_growth(w, session_started_at)
            row = await w.insert(
                PREDICATES,
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
                    "created_at": w.now,
                    "covers": list(term.covers),
                    "not_this": encode_not_this(term.not_this),
                    "inverse_label": term.inverse_label,
                    "synonyms": list(term.synonyms),
                    "version": 1,
                },
            )
            return Written(_predicate_term(row), w.batch)

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
        async with self._write(guild_id, campaign_id, source) as w:
            onto = await _load_ontology(w)
            term: TypeTerm | PredicateTerm | None = onto.types.get(key) or onto.predicates.get(key)
            if term is None or term.core:
                raise MemoryRuleError(f"{key} can't be retired.")
            borrowed = await w.conn.execute(
                "SELECT 1 FROM memory_predicates WHERE guild_id = %s AND campaign_id = %s"
                " AND key = %s AND from_set IS NOT NULL",
                (*w.ids, key),
            )
            if await borrowed.fetchone() is not None:
                raise MemoryRuleError(
                    f"{term.label} comes from a shared set, so only the set can change it. "
                    "Unfollow the set to be rid of it."
                )
            if isinstance(term, TypeTerm):
                if replaced_by is not None:
                    onto.active_type(replaced_by)
                await w.update(TYPES, key, {"status": DEPRECATED, "replaced_by": replaced_by})
            else:
                if replaced_by is not None:
                    onto.active_predicate(replaced_by)
                await w.update(PREDICATES, key, {"status": DEPRECATED, "replaced_by": replaced_by})
            return Written(None, w.batch)

    # ---- undo -------------------------------------------------------------------------

    async def undo(
        self, guild_id: int, campaign_id: str, batch: int, *, source: str = "undo"
    ) -> Written[None]:
        """Reverse one operation (see `undo_batch`). Returns the undo's own batch, which
        can be undone in turn to redo."""
        try:
            async with self._write(guild_id, campaign_id, source, undoes=batch) as w:
                await undo_batch(w, batch)
                return Written(None, w.batch)
        except TooLateToUndo:
            raise TooLateToUndo(self._too_late()) from None
        except (errors.UniqueViolation, errors.ForeignKeyViolation):
            # What the undo would put back clashes with something done since (a book entry taken
            # by another name), or what it points to is gone: not a crash, "changed since".
            raise MemoryRuleError(CHANGED_SINCE) from None

    async def undo_connection(
        self, guild_id: int, campaign_id: str, batch: int, *, within: int, secrets: bool
    ) -> Written[Relation]:
        """Put back a connection the DM removed or reworded (#1566, #2323): reverses that one
        batch, which must hold only changes to connections made by the DM, one of them ending a
        connection, no more than `within` seconds ago. A reworded one is the new connection added
        and the old one ended in that one batch, so both go back together. The ended row comes
        back exactly as it was (secret flag, source, status, detail). A secret one is only put
        back for someone who may see secrets (`secrets`), so a manager who isn't a DM can't touch
        it. Refused, with nothing written, if the connection changed since."""
        async with self._write(guild_id, campaign_id, "undo", undoes=batch) as w:
            cur = await w.conn.execute(
                "SELECT row_id, op, source, made_at, before, after FROM memory_changes"
                " WHERE guild_id = %s AND campaign_id = %s AND batch = %s AND table_name = %s",
                (*w.ids, batch, RELATIONS.name),
            )
            rows = await cur.fetchall()
            cur = await w.conn.execute(
                "SELECT table_name, op FROM memory_changes"
                " WHERE guild_id = %s AND campaign_id = %s AND batch = %s AND table_name <> %s",
                (*w.ids, batch, RELATIONS.name),
            )
            # (new words can raise a check's flag in the same change: it goes back with them, and
            # so do the beliefs written for the connections it added or ended, #2409)
            others = await cur.fetchall()
            if not rows or any(
                o["table_name"] not in (FLAGS.name, BELIEFS.name) or o["op"] != "insert"
                for o in others
            ):
                raise TooLateToUndo(self._too_late())
            ended = [
                r
                for r in rows
                if r["op"] == "update" and (r["after"] or {}).get("status") == REJECTED
            ]
            if not ended and _is_a_reveal(rows):  # 👁️ Show to players on a held-back one (#2404)
                ended = rows
            if len(ended) != 1 or any(r["source"] != DM for r in rows):
                raise MemoryRuleError(NOT_FOUND)  # only a removal, rewording or reveal by the DM
            for r in rows:  # a secret is not even mentioned, old or not
                secret = (r["before"] or {}).get("secret") or (r["after"] or {}).get("secret")
                if secret and not secrets:
                    raise MemoryRuleError(NOT_FOUND)
            if any(w.now - r["made_at"] > within for r in rows):
                raise TooLateToUndo(self._too_late())
            await undo_batch(w, batch)
            back = await w.get(RELATIONS, ended[0]["row_id"])
            if back is None:
                raise MemoryRuleError(NOT_FOUND)
            return Written(_relation(back), w.batch)

    def _too_late(self) -> str:
        # Also true after a backup replaced the campaign: that starts a fresh log.
        return (
            f"Too late to undo: Undo works for {days(self.keep_days)}, and not from before a "
            "backup was loaded."
        )


async def _close_stale_flags(w: Changes) -> list[Flag]:
    """The cleanup's work, inside its write: see MemoryStore.resolve_stale_flags."""
    by_fact: dict[str, list[dict[str, Any]]] = {}
    for flag in await w.select(FLAGS, " AND status = 'open' ORDER BY created_at, id"):
        by_fact.setdefault(flag["relation_id"], []).append(flag)
    if not by_fact:
        return []
    onto = await _load_ontology(w)
    facts = [
        _relation(r) for r in await w.select(RELATIONS, " AND id = ANY(%s)", [sorted(by_fact)])
    ]
    ends = sorted({e for f in facts for e in (f.subject_id, f.object_id)})
    types = {e["id"]: e["type"] for e in await w.select(ENTITIES, " AND id = ANY(%s)", [ends])}
    # Only the facts a check can use (#347 perf-qa): the same pair (contradictions) and,
    # for a "how many" rule, the same end with the same term. Not every fact on both
    # ends, which made a busy name cost flags times facts.
    by_end: dict[tuple[str, str], list[Relation]] = {}
    by_pair: dict[frozenset[str], list[Relation]] = {}
    for r in await _relations_touching(w, *ends):
        for e in {r.subject_id, r.object_id}:
            by_end.setdefault((e, r.predicate), []).append(r)
        by_pair.setdefault(frozenset((r.subject_id, r.object_id)), []).append(r)
    extra = await states.load_extras(w, onto, [f for f in facts if f.status != REJECTED])
    stale: list[str] = []
    mark = time.perf_counter()
    for fact in facts:
        if time.perf_counter() - mark > YIELD_AFTER_S:
            await asyncio.sleep(0)  # a long check lets voice and other servers in
            mark = time.perf_counter()
        still: set[tuple[str, str | None]] = set()
        if fact.predicate not in onto.predicates:  # a term DMbot can't check any more:
            still = {(f["kind"], f["other_id"]) for f in by_fact[fact.id]}  # the DM's call
        elif fact.status != REJECTED:  # a rejected fact clashes with nothing
            term = onto.predicates[fact.predicate]
            others = {
                r.id: r for r in by_pair.get(frozenset((fact.subject_id, fact.object_id)), [])
            }
            if term.max_per_subject is not None:
                for end in (
                    (fact.subject_id, fact.object_id) if term.symmetric else (fact.subject_id,)
                ):
                    others.update((r.id, r) for r in by_end.get((end, term.key), []))
            if term.max_per_object is not None:
                others.update((r.id, r) for r in by_end.get((fact.object_id, term.key), []))
            others.pop(fact.id, None)
            problems = check_relation(
                onto,
                fact,
                types[fact.subject_id],
                types[fact.object_id],
                list(others.values()),
                object_unique=fact.object_id in extra.unique,
                nesting=extra.nesting,
                ended=extra.ended,
            )
            still = {(p.kind, p.other_id) for p in problems}
        seen: set[tuple[str, str | None]] = set()
        for f in by_fact[fact.id]:  # oldest first
            problem = (f["kind"], f["other_id"])
            if problem not in still or problem in seen:  # gone, or found twice
                stale.append(f["id"])
            seen.add(problem)
    rows = await w.update_rows(FLAGS, {flag_id: {"status": "resolved"} for flag_id in stale})
    return [_flag(r) for r in rows]


# ---- helpers (inside a write) ----------------------------------------------------------


async def _move_rule_links(w: Changes, keep_id: str, gone_id: str) -> None:
    """A merged entry's links to the rules go with it, unless the kept entry already has
    that species or creature type, or the very same link (then they stay behind, unseen)."""
    mine = await w.select(RULE_LINKS, " AND entity_id = %s", [keep_id])
    has_one = {r["kind"] for r in mine if r["kind"] in (SPECIES, CREATURE_TYPE)}
    has_same = {(r["kind"], r["rules_source"], r["name"]) for r in mine}
    for link in await w.select(RULE_LINKS, " AND entity_id = %s", [gone_id]):
        if (
            link["kind"] in has_one
            or (link["kind"], link["rules_source"], link["name"]) in has_same
        ):
            continue
        await w.update(RULE_LINKS, link["id"], {"entity_id": keep_id})
        if link["kind"] in (SPECIES, CREATURE_TYPE):
            has_one.add(link["kind"])
        has_same.add((link["kind"], link["rules_source"], link["name"]))


async def _move_plan_links(w: Changes, keep_id: str, gone_id: str) -> None:
    """The DM's word that a merged entry is a book entry (#1715) goes with it. If the kept entry is
    already linked in that file, or already has an answer for that book entry, the merged entry's
    link is deleted instead (a Same beats a Not the same). Logged, so Undo of the merge restores
    it."""
    for link in await w.select(PLAN_SAME_AS, " AND entity_id = %s", [gone_id]):
        linked_docs = {
            r["document_id"]
            for r in await w.select(
                PLAN_SAME_AS, " AND entity_id = %s AND status = 'same'", [keep_id]
            )
        }
        have = await w.select(
            PLAN_SAME_AS, " AND entity_id = %s AND plan_id = %s", [keep_id, link["plan_id"]]
        )
        if link["status"] == "same" and link["document_id"] in linked_docs:
            await w.delete(PLAN_SAME_AS, link["id"])
        elif have:
            await w.delete(PLAN_SAME_AS, link["id"])
            if link["status"] == "same" and have[0]["status"] != "same":
                await w.update(PLAN_SAME_AS, have[0]["id"], {"status": "same"})
        else:
            await w.update(PLAN_SAME_AS, link["id"], {"entity_id": keep_id})


async def _on_person(w: Changes, alias: dict[str, Any]) -> bool:
    """Only a name waiting on a person can be answered as one."""
    owner = await w.get(ENTITIES, alias["entity_id"])
    return owner is not None and bool(owner["type"] == PERSON)


def _is_character(row: dict[str, Any]) -> bool:
    """A story character (any role), not a place, thing or person."""
    return bool(row["type"] == "character")


async def _ensure_person(w: Changes, user_id: int) -> str:
    """The campaign's entry for this Discord user, made if there is none. A person made
    here is named `Player 1234` (the last four digits of their ID, never their Discord
    name: that is read only in the live session) until a better name is known (#1188)."""
    found = await w.select(
        ENTITIES,
        " AND type = 'person' AND played_by = %s AND status IN ('proposed', 'confirmed')",
        [user_id],
    )
    if found:
        return str(found[0]["id"])
    name = _placeholder_name(user_id)
    row = await w.insert(
        ENTITIES,
        {
            "id": new_id(),
            "type": PERSON,
            "name": name,
            "description": "",
            "status": CONFIRMED,
            "merged_into": None,
            "source": w.source,
            "created_at": w.now,
            "played_by": user_id,
            "role": None,
            "needs_look": False,
            "unique_item": None,
        },
    )
    await w.insert(ALIASES, _new_alias(w, row["id"], name, "full", CONFIRMED, False, None))
    return str(row["id"])


def _placeholder_name(user_id: int) -> str:
    return f"Player {str(user_id)[-4:]}"


placeholder_name = _placeholder_name  # for a names download (#1559)


async def _unused_placeholder(w: Changes, person_id: str, user_id: int) -> bool:
    """A person entry that is only the `Player 1234` placeholder: still that name, no other
    names, nothing written on it (description, role), nothing logged for it by anyone but the
    scan, and nothing linking to it: the same list a delete is refused on (so the delete below
    can't fail halfway), plus the rows that would cascade without a log (a character sheet,
    heard counts), which keep it. An entry the DM touched stays."""
    row = await w.get(ENTITIES, person_id)
    if row is None or row["type"] != PERSON or row["name"] != _placeholder_name(user_id):
        return False
    if row["description"] or row["role"] is not None:
        return False  # the DM wrote on it (needs_look is DMbot's own mark, not the DM's)
    names = await w.select(ALIASES, " AND entity_id = %s", [person_id])
    if any(a["text"] != row["name"] for a in names):
        return False
    # Anything logged for it by someone other than DMbot's own scan: the DM touched it.
    cur = await w.conn.execute(
        "SELECT 1 FROM memory_changes WHERE guild_id = %s AND campaign_id = %s"
        " AND table_name = 'memory_entities' AND row_id = %s AND source <> 'scan' LIMIT 1",
        (*w.ids, person_id),
    )
    if await cur.fetchone() is not None:
        return False
    # Rows that would go with it without a log: kept, never silently deleted.
    for table_name in ("character_sheets", "memory_heard"):
        cur = await w.conn.execute(
            sql.SQL(
                "SELECT 1 FROM {} WHERE guild_id = %s AND campaign_id = %s"
                " AND entity_id = %s LIMIT 1"
            ).format(sql.Identifier(table_name)),
            (*w.ids, person_id),
        )
        if await cur.fetchone() is not None:
            return False
    for dep_table, column, is_list in DEPENDENTS["memory_entities"]:
        if (dep_table, column) == ("memory_aliases", "entity_id"):
            continue  # its own placeholder name, removed with it
        test = "%s = ANY({})" if is_list else "{} = %s"
        query = sql.SQL(
            "SELECT 1 FROM {} WHERE guild_id = %s AND campaign_id = %s AND " + test + " LIMIT 1"
        ).format(sql.Identifier(dep_table), sql.Identifier(column))
        cur = await w.conn.execute(query, (*w.ids, person_id))
        if await cur.fetchone() is not None:
            return False
    return True


async def _drop_person(w: Changes, person_id: str) -> None:
    """Remove a person entry and its placeholder name for good, also from the change log so
    an Undo can't bring it back."""
    alias_ids = [str(a["id"]) for a in await w.select(ALIASES, " AND entity_id = %s", [person_id])]
    if alias_ids:
        await w.conn.execute(
            "DELETE FROM memory_aliases WHERE guild_id = %s AND campaign_id = %s AND id = ANY(%s)",
            (*w.ids, alias_ids),
        )
    # Also moves the campaign's version on. (Any raw DELETE of something table_people shows
    # must bump memory_version too, or its cache would go stale.)
    await w.delete(ENTITIES, person_id)
    for table_name, ids in (("memory_aliases", alias_ids), ("memory_entities", [person_id])):
        if ids:
            await w.conn.execute(
                "DELETE FROM memory_changes WHERE guild_id = %s AND campaign_id = %s"
                " AND table_name = %s AND row_id = ANY(%s)",
                (*w.ids, table_name, ids),
            )


async def _sync_plays(w: Changes, character_id: str) -> None:
    """Make the `plays` link match the character's `played_by` (the mirror kept so the
    sheets, fights and the live session read it as before): exactly one confirmed link,
    from the person who plays it, or none when nobody does. Written in the caller's
    change, so one Undo takes it back with the rest."""
    row = await w.get(ENTITIES, character_id)
    edges = await w.select(
        RELATIONS, " AND predicate = %s AND object_id = %s", [PLAYS, character_id]
    )
    person: str | None = None
    if (
        row is not None
        and row["status"] in LIVE
        and row["role"] == PLAYER_CHARACTER
        and row["played_by"] is not None
    ):
        person = await _ensure_person(w, int(row["played_by"]))
    kept = False
    for edge in edges:
        if person is not None and edge["subject_id"] == person and not kept:
            kept = True
            if edge["status"] != CONFIRMED or edge["to_session_at"] is not None:
                await w.update(RELATIONS, edge["id"], {"status": CONFIRMED, "to_session_at": None})
        else:
            await w.delete(RELATIONS, edge["id"])
    if person is not None and not kept:
        link = Relation(
            new_id(), person, PLAYS, character_id, "", 1.0, CONFIRMED, w.source,
            (), None, None, None, None, False, w.now, PLAYS_VERSION,
        )  # fmt: skip
        await w.insert(RELATIONS, _relation_row(link))


def _check_role(onto: Ontology, type_key: str, role: str | None, played_by: int | None) -> None:
    """A role belongs to a character, one at a time; only a player character is played by
    someone (#1034)."""
    if role is not None:
        if role not in ROLES:
            raise MemoryRuleError("A character is a player character, an NPC or a god.")
        if not onto.is_a(type_key, "character"):
            raise MemoryRuleError("Only a character has a role.")
    if played_by is not None and role != PLAYER_CHARACTER and type_key != PERSON:
        raise MemoryRuleError("Only a player character is played by someone.")


class _Prepared:
    """Each typed name's clean spelling, lookup key and sound codes, worked out ahead
    (pure CPU work). A name that can't be used raises only where it's used, as before."""

    def __init__(self, raw: Sequence[str]) -> None:
        self._done: dict[str, tuple[str, str, tuple[str, ...]] | MemoryRuleError] = {}
        for t in raw:
            if t not in self._done:
                try:
                    text = clean_text(t)
                    self._done[t] = (text, lookup_key(text), tuple(sound_codes(text)))
                except MemoryRuleError as exc:
                    self._done[t] = exc

    def __getitem__(self, t: str) -> tuple[str, str, list[str]]:
        found = self._done[t]
        if isinstance(found, MemoryRuleError):
            raise found
        text, key, codes = found
        return text, key, list(codes)


def _new_alias(
    w: Changes,
    entity_id: str,
    text: str,
    kind: str,
    status: str,
    secret: bool,
    used_by: str | None,
    codes: list[str] | None = None,  # worked out already (_Prepared)
) -> dict[str, Any]:
    return {
        "id": new_id(),
        "entity_id": entity_id,
        "text": text,
        "key": lookup_key(text),
        "kind": kind,
        "used_by": used_by,
        "secret": secret,
        "status": status,
        "sound_codes": list(sound_codes(text)) if codes is None else codes,
        "source": w.source,
        "created_at": w.now,
    }


def _note_cleared(row: dict[str, Any]) -> dict[str, Any]:
    """A suggestion read from the DM's story carries a quote of it until the DM decides; any way
    out of "waiting" (confirm, reject, merge) takes it away, so the DM's text is not kept
    (#2167)."""
    waiting = row.get("source") == BACKGROUND_SOURCE and row.get("status") == PROPOSED
    return {"description": ""} if waiting else {}  # (a decided entry's later note is the DM's)


async def _forget_quotes(w: Changes, entity_ids: Sequence[str]) -> None:
    """Blank the quote in the change log too (`before` and `after` of those entries), so an
    entry decided on keeps nothing of the DM's text there either. An Undo of the decision then
    brings the suggestion back without its quote. Called after the update that cleared it."""
    if not entity_ids:
        return
    await w.conn.execute(
        "UPDATE memory_changes SET"
        " before = CASE WHEN before ->> 'source' = %s AND before ->> 'status' = 'proposed'"
        " THEN before || jsonb_build_object('description', ''::text) ELSE before END,"
        " after = CASE WHEN after ->> 'source' = %s AND after ->> 'status' = 'proposed'"
        " THEN after || jsonb_build_object('description', ''::text) ELSE after END"
        " WHERE guild_id = %s AND campaign_id = %s AND table_name = 'memory_entities'"
        " AND row_id = ANY(%s)",
        (BACKGROUND_SOURCE, BACKGROUND_SOURCE, *w.ids, list(entity_ids)),
    )


def _upgrade(old: dict[str, Any], status: str, secret: bool, source: str) -> dict[str, Any]:
    """What saying something already known again changes: a stronger status (only the
    DM confirms or un-rejects) and secret=True. Never weakens anything."""
    changes: dict[str, Any] = {}
    if secret and not old["secret"]:
        changes["secret"] = True
        if "said_as" in old:  # a name (relations are upgraded here too)
            changes["said_as"] = None  # a secret name is never sent to the narrator (#1419)
    if old["status"] == REJECTED:
        if source == DM and status != REJECTED:
            changes["status"] = status
    elif _stronger(old["status"], status) != old["status"]:
        changes["status"] = status
    return changes


def _is_a_reveal(rows: list[dict[str, Any]]) -> bool:
    """One connection changed in place, and only in who may see it (secret or unrevealed): the
    DM showing a held-back connection to the players."""
    if len(rows) != 1 or rows[0]["op"] != "update":
        return False
    before, after = rows[0]["before"] or {}, rows[0]["after"] or {}
    changed_keys = {k for k in {*before, *after} if before.get(k) != after.get(k)}
    return bool(changed_keys) and changed_keys <= {"secret", "unrevealed"}


async def _entity_row(w: Scope, entity_id: str, *, allow_rejected: bool = False) -> dict[str, Any]:
    row = await w.get(ENTITIES, entity_id)
    allowed = (*LIVE, REJECTED) if allow_rejected else LIVE
    if row is None or row["status"] not in allowed:
        raise MemoryRuleError(NOT_FOUND)
    return row


async def _threads(scope: Scope, stalled_after: int) -> list[threads_mod.ThreadInfo]:
    """The read model of every confirmed thread, from a handful of reads (not one per thread)."""
    onto = await _load_ontology(scope)
    kinds = sorted(k for k in onto.types if onto.is_a(k, "thread"))
    rows = await scope.select(ENTITIES, " AND type = ANY(%s) AND status = 'confirmed'", [kinds])
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    by_id: dict[str, list[State]] = {i: [] for i in ids}
    for r in await scope.select(
        STATES,
        " AND entity_id = ANY(%s)"
        " ORDER BY created_at, COALESCE(closed_at, 9223372036854775807), id",
        [ids],
    ):
        by_id[r["entity_id"]].append(states.state_of(r))
    facts = await scope.select(
        RELATIONS,
        " AND ((object_id = ANY(%s) AND predicate = ANY(%s))"
        "   OR (predicate = 'led_to' AND (object_id = ANY(%s) OR subject_id = ANY(%s))))"
        " AND status = 'confirmed' AND to_session_at IS NULL ORDER BY created_at, id",
        [ids, ["in_thread", "advances", "resolves", "promised_by", "promised_to"], ids, ids],
    )
    others = {
        r["id"]: _entity(r)
        for r in await scope.select(
            ENTITIES,
            " AND id = ANY(%s) AND status = 'confirmed'",
            [sorted({f["subject_id"] for f in facts} | {f["object_id"] for f in facts})],
        )
    }
    cur = await scope.conn.execute(
        "SELECT entity_id, session_started_at FROM memory_heard"
        " WHERE guild_id = %s AND campaign_id = %s",
        scope.ids,
    )
    heard: dict[str, set[int]] = {}
    sessions: set[int] = set()
    for h in await cur.fetchall():
        sessions.add(int(h["session_started_at"]))
        if h["entity_id"] in by_id:
            heard.setdefault(h["entity_id"], set()).add(int(h["session_started_at"]))
    known = sorted(sessions)
    out: list[threads_mod.ThreadInfo] = []
    for r in sorted(rows, key=lambda e: (name_key(e["name"]), e["id"])):
        history = tuple(by_id[r["id"]])
        mine = [
            f
            for f in facts
            if f["object_id"] == r["id"]
            and f["subject_id"] in others
            and f["predicate"] != "led_to"  # a link in a chain is not the thread moving
        ]
        came = tuple(
            others[f["subject_id"]]
            for f in facts
            if f["predicate"] == "led_to"
            and f["object_id"] == r["id"]
            and f["subject_id"] in others
        )
        went = tuple(
            others[f["object_id"]]
            for f in facts
            if f["predicate"] == "led_to"
            and f["subject_id"] == r["id"]
            and f["object_id"] in others
        )
        involved = tuple(
            threads_mod.Involved(others[f["subject_id"]], f["detail"])
            for f in mine
            if f["predicate"] == "in_thread"
        )
        movers = tuple(
            threads_mod.Mover(
                others[f["subject_id"]], f["predicate"] == "resolves", f["from_session_at"]
            )
            for f in mine
            if f["predicate"] in ("advances", "resolves")
        )
        by = tuple(others[f["subject_id"]] for f in mine if f["predicate"] == "promised_by")
        to = tuple(others[f["subject_id"]] for f in mine if f["predicate"] == "promised_to")
        touch = threads_mod.last_touch(
            r["created_at"],
            history,
            [f["from_session_at"] for f in mine],
            [f["created_at"] for f in mine],
            heard.get(r["id"], ()),
        )
        quiet = threads_mod.sessions_since(touch, known)
        state = next((s for s in reversed(history) if s.closed_at is None), None)
        now = state.condition if state is not None else None
        out.append(
            threads_mod.ThreadInfo(
                _entity(r), state, history, involved, movers, touch, quiet,
                threads_mod.is_stalled(now, quiet, stalled_after),
                by, to, r["description"] if r["type"] == threads_mod.PROMISE else "",
                came, went,
            )
        )  # fmt: skip
    out.sort(key=lambda t: (not t.stalled, name_key(t.thread.name), t.thread.id))
    return out


async def _move_standing(w: Changes, keep_id: str, gone_id: str) -> None:
    """A merged entry's standing goes with it (#1655): its current impressions and attitudes
    move onto the kept entry unless the kept one already has its own for that pair (then the
    kept one wins and the other is closed), and the party flag moves if the kept entry is a
    group. Closed rows are history and stay as they are."""
    gone = await w.get(ENTITIES, gone_id)
    kept = await w.get(ENTITIES, keep_id)
    if gone is not None and kept is not None and gone["party"]:
        await w.update(ENTITIES, gone_id, {"party": False})
        if kept["type"] == "faction" and not kept["party"]:
            await w.update(ENTITIES, keep_id, {"party": True})
    for table, held, aimed in (
        (IMPRESSIONS, "viewer_id", "subject_id"),
        (ATTITUDES, "holder_id", "toward_id"),
    ):
        rows = await w.select(
            table, f" AND closed_at IS NULL AND ({held} = %s OR {aimed} = %s)", [gone_id, gone_id]
        )
        for r in rows:
            a = keep_id if r[held] == gone_id else r[held]
            b = keep_id if r[aimed] == gone_id else r[aimed]
            taken = await w.select(
                table, f" AND closed_at IS NULL AND {held} = %s AND {aimed} = %s", [a, b]
            )
            if a == b or taken:
                await w.update(table, r["id"], {"closed_at": w.now})
            else:
                await w.update(table, r["id"], {held: a, aimed: b})


ONLY_PLAYERS_DO = "Only a player character or the party does a deed."
ONLY_THESE_MATTER = "A deed can matter to an NPC, a group or a place, not to the doer."


async def _standing_index(
    scope: Scope, session_numbers: Mapping[int, int] | None = None
) -> standing.Index:
    """The confirmed entries, the three connections the ladders climb and the current impressions
    and attitudes, as `standing.Index` wants them."""
    entities = await scope.select(ENTITIES, " AND status = 'confirmed' AND type <> 'person'")
    links = await scope.select(
        RELATIONS,
        " AND status = 'confirmed' AND to_session_at IS NULL AND predicate = ANY(%s)",
        [[standing.MEMBER_OF, standing.LOCATED_IN, standing.OF_PEOPLE]],
    )
    impressions = await scope.select(IMPRESSIONS, " AND closed_at IS NULL")
    attitudes = await scope.select(ATTITUDES, " AND closed_at IS NULL")
    deeds = await _deed_rows(scope, {e["id"]: e for e in entities})
    return standing.Index(
        (
            standing.Node(
                e["id"], e["name"], e["type"], bool(e["party"]), e["role"] == PLAYER_CHARACTER
            )
            for e in entities
        ),
        (standing.Link(r["subject_id"], r["predicate"], r["object_id"]) for r in links),
        (
            *(
                standing.Row(r["viewer_id"], r["subject_id"], r["score"], r["weight"], r["source"])
                for r in impressions
            ),
            *(
                standing.Row(
                    r["holder_id"], r["toward_id"], r["score"], r["weight"], r["source"], True
                )
                for r in attitudes
            ),
            *deeds,
        ),
        session_numbers,
    )


async def _deed_rows(scope: Scope, entities: dict[str, dict[str, Any]]) -> list[standing.Row]:
    """The deeds the DM recorded (#1732) as evidence: for each person, group or place a deed
    mattered to, one row toward the one who did it. A deed older than `DEED_FADES_AFTER` sessions
    counts at half weight; the sessions counted are the ones names were heard in and the ones
    deeds were recorded for."""
    rows = await scope.select(
        RELATIONS,
        " AND status = 'confirmed' AND to_session_at IS NULL AND predicate = ANY(%s)",
        [[DID, AFFECTED]],
    )
    doer = {
        r["object_id"]: r["subject_id"]
        for r in rows
        if r["predicate"] == DID
        and (
            entities.get(r["subject_id"], {}).get("party")
            or entities.get(r["subject_id"], {}).get("role") == PLAYER_CHARACTER
        )
    }  # (only a player character or the party does a deed, whatever else was written)
    affected = [r for r in rows if r["predicate"] == AFFECTED and r["subject_id"] in doer]
    if not affected:
        return []
    cur = await scope.conn.execute(
        "SELECT DISTINCT session_started_at FROM memory_heard WHERE guild_id = %s"
        " AND campaign_id = %s AND session_started_at IS NOT NULL"
        " ORDER BY session_started_at DESC LIMIT %s",
        (*scope.ids, standing.DEED_FADES_AFTER),
    )
    sessions = {int(r["session_started_at"]) for r in await cur.fetchall()}
    sessions |= {int(r["from_session_at"]) for r in affected if r["from_session_at"] is not None}
    newest = sorted(sessions, reverse=True)
    cutoff = (
        newest[standing.DEED_FADES_AFTER - 1] if len(newest) >= standing.DEED_FADES_AFTER else 0
    )
    out = []
    for r in affected:
        deed = entities.get(r["subject_id"])
        score = standing.DEED_SCORE.get(r["detail"])
        if deed is None or score is None or r["object_id"] not in entities:
            continue
        old = r["from_session_at"] is not None and int(r["from_session_at"]) < cutoff
        out.append(
            standing.Row(
                r["object_id"], doer[r["subject_id"]], score,
                standing.DEED_WEIGHT // 2 if old else standing.DEED_WEIGHT,
                standing.STORY_SOURCE, False, deed["description"] or deed["name"], r["detail"],
                None if r["from_session_at"] is None else int(r["from_session_at"]),
            )
        )  # fmt: skip
    return out


def _opinion(r: dict[str, Any], attitude: bool) -> Opinion:
    holder, target = ("holder_id", "toward_id") if attitude else ("viewer_id", "subject_id")
    return Opinion(
        r["id"], r[holder], r[target], r["score"], r["weight"], r["source"],
        r["from_session_at"], r["created_at"], r["closed_at"],
    )  # fmt: skip


async def _audience(scope: Scope, clue_id: str) -> list[Entity]:
    """The live characters with a confirmed, still-true `knows` fact to the clue, by name."""
    rows = await scope.select(
        RELATIONS,
        " AND object_id = %s AND predicate = %s AND status = 'confirmed' AND to_session_at IS NULL",
        [clue_id, KNOWS],
    )
    ids = sorted({r["subject_id"] for r in rows})
    if not ids:
        return []
    entities = await scope.select(
        ENTITIES, " AND id = ANY(%s) AND status IN ('proposed', 'confirmed')", [ids]
    )
    return sorted((_entity(e) for e in entities), key=lambda e: (name_key(e.name), e.id))


async def _load_ontology(scope: Scope) -> Ontology:
    types = [_type_term(r) for r in await scope.select(TYPES)]
    predicates = [_predicate_term(r) for r in await scope.select(PREDICATES)]
    return Ontology.build(types, predicates)


async def _relations_touching(w: Scope, *entity_ids: str) -> list[Relation]:
    # In id order: the facts a check finds become flags in this order, so the same memory
    # always gets the same flags, whatever order the rows sit in on disk (#476).
    rows = await w.select(
        RELATIONS,
        # Byte order (ids are lowercase hex): the same everywhere, and cheaper than the
        # database's locale-aware sort.
        ' AND (subject_id = ANY(%s) OR object_id = ANY(%s)) ORDER BY id COLLATE "C"',
        [list(entity_ids), list(entity_ids)],
    )
    return [_relation(r) for r in rows]


async def _flag_problems(
    w: Changes,
    onto: Ontology,
    rel: Relation,
    subject_type: str,
    object_type: str,
    existing: Sequence[Relation],
    extra: states.Extras | None = None,
) -> list[Flag]:
    """Flag what `check_relation` finds. `extra` is what the checks need besides the facts;
    a caller checking many facts in one write reads it once for all of them (#1267)."""
    flags = []
    if extra is None:
        extra = await states.load_extras(w, onto, [rel])
    for problem in check_relation(
        onto, rel, subject_type, object_type, existing,
        object_unique=rel.object_id in extra.unique, nesting=extra.nesting, ended=extra.ended,
    ):  # fmt: skip
        row = await w.insert(
            FLAGS,
            {
                "id": new_id(),
                "kind": problem.kind,
                "relation_id": rel.id,
                "other_id": problem.other_id,
                "status": "open",
                "created_at": w.now,
            },
        )
        flags.append(_flag(row))
    return flags


async def _delete_relation(w: Changes, relation_id: str) -> None:
    for flag in await w.select(
        FLAGS, " AND (relation_id = %s OR other_id = %s)", [relation_id, relation_id]
    ):
        await w.delete(FLAGS, flag["id"])
    await w.delete(RELATIONS, relation_id)


async def _move_aliases(
    w: Changes, keep_id: str, gone_id: str, confirm: frozenset[str] = frozenset()
) -> None:
    """The other entry's names move over in one statement (#164); a name both have is
    kept once, with the stronger status and any secret mark. Names whose key is in
    `confirm` (the DM just said they mean `keep_id`) are confirmed as they move, in the
    same row change, so one undo puts them back exactly."""
    keep_aliases = {a["key"]: a for a in await w.select(ALIASES, " AND entity_id = %s", [keep_id])}
    moving: list[str] = []
    confirming: list[str] = []
    for alias in await w.select(ALIASES, " AND entity_id = %s", [gone_id]):
        said = alias["key"] in confirm
        twin = keep_aliases.get(alias["key"])
        if twin is None:
            (confirming if said and alias["status"] != CONFIRMED else moving).append(alias["id"])
            continue
        status = CONFIRMED if said else alias["status"]
        upgrade = _upgrade(twin, status, alias["secret"], DM)
        await w.delete(ALIASES, alias["id"])
        if upgrade:
            await w.update(ALIASES, twin["id"], upgrade)
    if moving:
        await w.update_where(ALIASES, {"entity_id": keep_id}, " AND id = ANY(%s)", [moving])
    if confirming:
        await w.update_where(
            ALIASES, {"entity_id": keep_id, "status": CONFIRMED}, " AND id = ANY(%s)", [confirming]
        )
    await w.update_where(ALIASES, {"used_by": keep_id}, " AND used_by = %s", [gone_id])


async def _move_relations(w: Changes, keep_id: str, gone_id: str) -> None:
    """The other entry's facts move over. They're checked one by one, in the same order
    as before, against an in-memory copy of every fact they could clash with (so the
    same duplicates are folded and the same problems flagged); the moves themselves are
    then written in one statement (#164)."""
    onto = await _load_ontology(w)
    rows = await w.select(  # oldest first, so the same merge always folds the same way
        RELATIONS,
        " AND (subject_id = %s OR object_id = %s) ORDER BY created_at, id",
        [gone_id, gone_id],
    )
    if not rows:
        return
    ends = {keep_id, gone_id} | {r["subject_id"] for r in rows} | {r["object_id"] for r in rows}
    state = {r.id: r for r in await _relations_touching(w, *ends)}  # as the walk sees them
    # Which facts touch each entry, kept up to date as facts move or go, so each fact
    # is checked against its neighbours only (not every fact in `state`), in `state`'s
    # order so the same twin is found as before (#342 review).
    order = {fact_id: n for n, fact_id in enumerate(state)}
    touching: dict[str, set[str]] = {}
    for fact in state.values():
        for end in (fact.subject_id, fact.object_id):
            touching.setdefault(end, set()).add(fact.id)

    def forget(fact: Relation) -> None:
        for end in (fact.subject_id, fact.object_id):
            touching.get(end, set()).discard(fact.id)

    entities = {e["id"]: e for e in await w.select(ENTITIES, " AND id = ANY(%s)", [sorted(ends)])}
    moves: dict[str, dict[str, Any]] = {}
    gone_rows: list[str] = []  # removed: said nothing, or the same as another fact
    upgrades: list[tuple[str, dict[str, Any]]] = []
    to_check: list[tuple[Relation, list[Relation]]] = []
    for row in rows:
        subject = keep_id if row["subject_id"] == gone_id else row["subject_id"]
        obj = keep_id if row["object_id"] == gone_id else row["object_id"]
        if subject == obj:  # "Bell is an ally of Belleros" says nothing any more
            gone_rows.append(row["id"])
            dropped = state.pop(row["id"], None)
            if dropped is not None:
                forget(dropped)
            continue
        pred = onto.predicates.get(row["predicate"])
        if pred is not None:
            subject, obj = ordered(pred, subject, obj)
        moves[row["id"]] = {"subject_id": subject, "object_id": obj}
        forget(state[row["id"]])
        moved = dataclasses.replace(state[row["id"]], subject_id=subject, object_id=obj)
        state[row["id"]] = moved
        for end in (subject, obj):
            touching.setdefault(end, set()).add(moved.id)
        near = (touching.get(subject, set()) | touching.get(obj, set())) - {moved.id}
        others = [state[fact_id] for fact_id in sorted(near, key=order.__getitem__)]
        twin = duplicate_of(moved, others)
        if twin is not None:  # both said the same: keep one, with the stronger status
            upgrade = _upgrade(_relation_row(twin), moved.status, moved.secret, DM)
            gone_rows.append(moved.id)
            forget(moved)
            del state[moved.id]
            if upgrade:
                upgrades.append((twin.id, upgrade))
                state[twin.id] = _relation({**_relation_row(twin), **upgrade})
            continue
        if pred is not None and moved.status != REJECTED:
            to_check.append((moved, others))
    await w.update_rows(RELATIONS, moves)
    # Flags before removals, as the old walk did: a flag can point at a fact removed
    # later in the walk, and removing that fact removes its flags too.
    extra = await states.load_extras(w, onto, [moved for moved, _ in to_check])
    for moved, others in to_check:
        subject, obj = (entities.get(e) for e in (moved.subject_id, moved.object_id))
        if subject is None or obj is None or {subject["status"], obj["status"]} - set(LIVE):
            raise MemoryRuleError(NOT_FOUND)  # the far end was rejected or merged away
        await _flag_problems(w, onto, moved, subject["type"], obj["type"], others, extra)
    for relation_id in gone_rows:
        await _delete_relation(w, relation_id)
    for twin_id, upgrade in upgrades:
        await w.update(RELATIONS, twin_id, upgrade)


async def _check_growth(w: Changes, session_started_at: int | None) -> None:
    if session_started_at is None:
        return
    cur = await w.conn.execute(
        "SELECT (SELECT count(*) FROM memory_types WHERE guild_id = %s AND campaign_id = %s"
        " AND created_at >= %s) + (SELECT count(*) FROM memory_predicates"
        " WHERE guild_id = %s AND campaign_id = %s AND created_at >= %s) AS n",
        (*w.ids, session_started_at, *w.ids, session_started_at),
    )
    row = await cur.fetchone()
    if row is not None and row["n"] >= MAX_NEW_TERMS_PER_SESSION:
        raise MemoryRuleError("Enough new kinds of things for one session; review them first.")
