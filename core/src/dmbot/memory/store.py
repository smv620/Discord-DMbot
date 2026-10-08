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
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Collection, Sequence
from contextlib import asynccontextmanager
from typing import Any

from dmbot.db import Database, row_int
from dmbot.memory import notify
from dmbot.memory._changes import (
    ALIASES,
    CHANGED_SINCE,
    CORRECTIONS,
    ENTITIES,
    FLAGS,
    MENTIONS,
    NOT_FOUND,
    PREDICATES,
    RELATIONS,
    TYPES,
    Changes,
    Scope,
    undo_batch,
)
from dmbot.memory.checks import check_relation, duplicate_of, ordered
from dmbot.memory.lookup import LookupData
from dmbot.memory.models import (
    ALIAS_KINDS,
    CONFIRMED,
    DESCRIPTION_MAX,
    DETAIL_MAX,
    DM,
    FACT_STATUSES,
    FIX,
    KEEP,
    LINE_REF_MAX,
    LIST_MAX,
    LIVE,
    MENTION_METHODS,
    MERGED,
    PROPOSED,
    REJECTED,
    SOURCES,
    Alias,
    Correction,
    Entity,
    Flag,
    Heard,
    HeardCount,
    MemoryRuleError,
    MoreNames,
    NewName,
    Relation,
    TooLateToUndo,
    Written,
    check_status_change,
    clean_text,
    days,
    is_id,
    lookup_key,
    new_id,
)
from dmbot.memory.ontology import (
    ACTIVE,
    DEPRECATED,
    MAX_NEW_TERMS_PER_SESSION,
    Ontology,
    PredicateTerm,
    TypeTerm,
)
from dmbot.memory.sounds import sound_codes

NOT_HERE = "That campaign doesn't exist in this server."
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


# ---- conversions ---------------------------------------------------------------------


def _entity(r: dict[str, Any]) -> Entity:
    return Entity(
        r["id"], r["type"], r["name"], r["description"], r["status"], r["merged_into"],
        r["source"], r["created_at"], r["played_by"],
    )  # fmt: skip


def _alias(r: dict[str, Any]) -> Alias:
    return Alias(
        r["id"], r["entity_id"], r["text"], r["key"], r["kind"], r["used_by"], r["secret"],
        r["status"], tuple(r["sound_codes"]), r["source"], r["created_at"],
    )  # fmt: skip


def _relation(r: dict[str, Any]) -> Relation:
    return Relation(
        r["id"], r["subject_id"], r["predicate"], r["object_id"], r["detail"],
        float(r["confidence"]), r["status"], r["source"], tuple(r["mention_ids"]),
        r["from_session_at"], r["to_session_at"], r["from_game_time"], r["to_game_time"],
        r["secret"], r["created_at"],
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
YIELD_AFTER_S = 0.015  # the flag check gives the event loop a turn this often


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
                await notify.send(conn, campaign_id, changes.version, names_changed)

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

    async def entities(
        self, guild_id: int, campaign_id: str, *, statuses: Sequence[str] = LIVE
    ) -> list[Entity]:
        async with self._read(guild_id, campaign_id) as scope:
            rows = await scope.select(
                ENTITIES, " AND status = ANY(%s) ORDER BY name, id", [list(statuses)]
            )
            return [_entity(r) for r in rows]

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
                " AND (%s OR NOT secret) ORDER BY created_at, id",
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
            entities = await scope.select(
                ENTITIES, " AND status IN ('proposed', 'confirmed') ORDER BY id"
            )
            # Names and facts of live entries, filtered here against the entries just
            # read (same snapshot): plain reads, never a join that can go quadratic (#598).
            live = {str(r["id"]) for r in entities}
            aliases = [
                r
                for r in await scope.select(ALIASES, " AND status <> 'rejected' ORDER BY id")
                if r["entity_id"] in live
            ]
            corrections = await scope.select(CORRECTIONS, " ORDER BY id")
            relations = [
                r
                for r in await scope.select(
                    RELATIONS,
                    " AND status IN ('proposed', 'confirmed') AND NOT secret ORDER BY id",
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
            return LookupData(
                scope.version,
                tuple(_entity(r) for r in entities),
                tuple(_alias(r) for r in aliases),
                tuple(_correction(r) for r in corrections),
                tuple(_relation(r) for r in relations),
                heard,
                recent,
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
    ) -> Written[Entity]:
        """A new person, place or thing, with its name as the first alias. `played_by`:
        the Discord user playing a player character."""
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
            if played_by is not None and not onto.is_a(type, "player_character"):
                raise MemoryRuleError("Only a player character is played by someone.")
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
                },
            )
            await w.insert(ALIASES, _new_alias(w, row["id"], name, "full", status, False, None))
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
        one per `more` entry: its entry's ID if any name was added, else None."""
        if source != DM:
            raise MemoryRuleError("Only the DM can add a list of names.")
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
                onto.active_type(n.type)
                if onto_is_pc(onto, n.type):
                    raise MemoryRuleError("A player's character needs its player.")
                name, name_key_, _ = prep[n.name]
                if name_key_ in used:
                    ids.append(None)
                    continue
                entity_id = new_id()
                entities.append(
                    {
                        "id": entity_id, "type": n.type, "name": name, "description": "",
                        "status": n.status, "merged_into": None, "source": source,
                        "created_at": w.now, "played_by": None,
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
        self, guild_id: int, campaign_id: str, entity_id: str, type: str, *, source: str
    ) -> Written[Entity]:
        """What kind of thing it is (an NPC, a place…): the DM's call."""
        if source != DM:
            raise MemoryRuleError("Only the DM can say what something is.")
        async with self._write(guild_id, campaign_id, source) as w:
            (await _load_ontology(w)).active_type(type)
            current = await _entity_row(w, entity_id)
            changes: dict[str, Any] = {"type": type}
            if current["played_by"] is not None and not onto_is_pc(await _load_ontology(w), type):
                changes["played_by"] = None
            row = await w.update(ENTITIES, entity_id, changes)
            return Written(_entity(row), w.batch)

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
                await w.update(ALIASES, clash[0]["id"], {"text": name, "status": CONFIRMED})
                if own:
                    await w.update(ALIASES, own[0]["id"], {"status": REJECTED})
            else:
                await w.insert(
                    ALIASES, _new_alias(w, entity_id, name, "full", CONFIRMED, False, None)
                )
            return Written(_entity(row), w.batch)

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
            row = await w.update(ENTITIES, entity_id, {"name": alias["text"]})
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
    ) -> Written[Entity]:
        """The DM says a suggested name is real: what it is, confirmed with all its
        names, in one change (one undo, one reload)."""
        if source != DM:
            raise MemoryRuleError("Only the DM can confirm that.")
        if played_by is not None and not 0 < played_by <= INT64_MAX:
            raise ValueError("Bad Discord user ID")
        async with self._write(guild_id, campaign_id, source) as w:
            onto = await _load_ontology(w)
            onto.active_type(type)
            if played_by is not None and not onto.is_a(type, "player_character"):
                raise MemoryRuleError("Only a player character is played by someone.")
            await _entity_row(w, entity_id)
            row = await w.update(
                ENTITIES, entity_id, {"type": type, "status": CONFIRMED, "played_by": played_by}
            )
            for alias in await w.select(
                ALIASES, " AND entity_id = %s AND status = 'proposed'", [entity_id]
            ):
                await w.update(ALIASES, alias["id"], {"status": CONFIRMED})
            return Written(_entity(row), w.batch)

    async def confirm_kinds(
        self, guild_id: int, campaign_id: str, entity_ids: Sequence[str], type: str, *, source: str
    ) -> Written[int]:
        """The DM says what a whole group of suggested names is (📥 Add many: "every
        'wizard' is an NPC"): each still waiting is confirmed as that kind, with its names,
        in one change. Returns how many were confirmed."""
        if source != DM:
            raise MemoryRuleError("Only the DM can confirm that.")
        async with self._write(guild_id, campaign_id, source) as w:
            onto = await _load_ontology(w)
            onto.active_type(type)
            if onto_is_pc(onto, type):
                raise MemoryRuleError("A player's character needs its player.")
            # A few statements for the whole group, not a chain per name (#580). Names
            # checked or removed since aren't proposed any more, so they're left alone.
            confirmed = await w.update_where(
                ENTITIES,
                {"type": type, "status": CONFIRMED},
                " AND status = 'proposed' AND id = ANY(%s)",
                [sorted(set(entity_ids))],
            )
            ids = [row["id"] for row in confirmed]
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
            row = await w.update(ENTITIES, entity_id, {"status": status})
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
            await _entity_row(w, entity_id)
            if used_by is not None:
                await _entity_row(w, used_by)
            existing = await w.select(ALIASES, " AND entity_id = %s AND key = %s", [entity_id, key])
            if existing:
                old = existing[0]
                upgrade = _upgrade(old, status, secret, source)
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
            row = await w.update(ALIASES, alias_id, changes)
            return Written(_alias(row), w.batch)

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
            keep = await _entity_row(w, keep_id)
            gone = await _entity_row(w, gone_id)
            needs_dm = CONFIRMED in (keep["status"], gone["status"]) or keep["type"] != gone["type"]
            if needs_dm and not dm_said_same:
                raise MemoryRuleError("Only the DM can say these two are the same.")
            players = {keep["played_by"], gone["played_by"]} - {None}
            if len(players) > 1:
                raise MemoryRuleError("Those are two different players' characters.")
            if gone["played_by"] is not None and keep["played_by"] is None:
                # A player's character stays one, whichever name is kept.
                await w.update(
                    ENTITIES, keep_id, {"type": gone["type"], "played_by": gone["played_by"]}
                )
            if confirm_keys and source != DM:
                raise ValueError("Only the DM confirms names")
            await _move_aliases(w, keep_id, gone_id, frozenset(confirm_keys))
            # A character's D&D Beyond sheet (#723) goes with it, unless the kept entry
            # has its own. Not in the change log: undoing the merge leaves it on the kept
            # entry, which is still that player's character.
            await w.conn.execute(
                "UPDATE character_sheets SET entity_id = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND entity_id = %s"
                " AND NOT EXISTS (SELECT 1 FROM character_sheets"
                "  WHERE guild_id = %s AND campaign_id = %s AND entity_id = %s)",
                (keep_id, guild_id, campaign_id, gone_id, guild_id, campaign_id, keep_id),
            )
            own = await w.select(
                ALIASES, " AND entity_id = %s AND key = %s", [keep_id, lookup_key(keep["name"])]
            )
            if own and own[0]["secret"]:
                # The other entry's name was a secret one: the main name everyone sees
                # must never be.
                raise MemoryRuleError("A secret name can't be the main name.")
            await _move_relations(w, keep_id, gone_id)
            for table in (MENTIONS, CORRECTIONS):  # one statement each (#164)
                await w.update_where(
                    table, {"entity_id": keep_id}, " AND entity_id = %s", [gone_id]
                )
            await w.update(ENTITIES, keep_id, {"status": _stronger(keep["status"], gone["status"])})
            # Older merges pointing at gone_id now chain to keep_id; `resolve` follows it.
            await w.update(ENTITIES, gone_id, {"status": MERGED, "merged_into": keep_id})
            kept = await w.get(ENTITIES, keep_id)
            assert kept is not None
            return Written(_entity(kept), w.batch)

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
    ) -> Written[tuple[Relation, list[Flag]]]:
        """Store a fact, checked against the rules. Problems become flags for the DM to
        review; the fact is stored as given either way. A fact already known is only
        upgraded (the DM's confirmation, or secret=True); one the DM rejected stays
        rejected unless the DM says it again."""
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
        async with self._write(guild_id, campaign_id, source) as w:
            onto = await _load_ontology(w)
            pred = onto.active_predicate(predicate)
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
            )  # fmt: skip
            existing = await _relations_touching(w, s, o)
            same = duplicate_of(new, existing)
            if same is not None:
                upgrade = _upgrade(_relation_row(same), status, secret, source)
                if not upgrade:
                    return Written((same, []), w.batch)
                row = await w.update(RELATIONS, same.id, upgrade)
                return Written((_relation(row), []), w.batch)
            await w.insert(RELATIONS, _relation_row(new))
            flags = await _flag_problems(w, onto, new, types[s], types[o], existing)
            return Written((new, flags), w.batch)

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
    ) -> Written[Relation]:
        """Confirm or reject a fact, mark it secret, or end it (history is kept: a new
        fact replaces it rather than overwriting it)."""
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
                if current["secret"] and not secret and source != DM:
                    raise MemoryRuleError("Only the DM can stop keeping that secret.")
                changes["secret"] = secret
            if to_session_at is not None:
                _check_span(current["from_session_at"], to_session_at)
                changes["to_session_at"] = to_session_at
            row = await w.update(RELATIONS, relation_id, changes)
            return Written(_relation(row), w.batch)

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
            return cur.rowcount

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
            others.pop(fact.id, None)
            problems = check_relation(
                onto,
                fact,
                types[fact.subject_id],
                types[fact.object_id],
                list(others.values()),
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


def onto_is_pc(onto: Ontology, type_key: str) -> bool:
    return onto.is_a(type_key, "player_character")


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


def _upgrade(old: dict[str, Any], status: str, secret: bool, source: str) -> dict[str, Any]:
    """What saying something already known again changes: a stronger status (only the
    DM confirms or un-rejects) and secret=True. Never weakens anything."""
    changes: dict[str, Any] = {}
    if secret and not old["secret"]:
        changes["secret"] = True
    if old["status"] == REJECTED:
        if source == DM and status != REJECTED:
            changes["status"] = status
    elif _stronger(old["status"], status) != old["status"]:
        changes["status"] = status
    return changes


async def _entity_row(w: Scope, entity_id: str, *, allow_rejected: bool = False) -> dict[str, Any]:
    row = await w.get(ENTITIES, entity_id)
    allowed = (*LIVE, REJECTED) if allow_rejected else LIVE
    if row is None or row["status"] not in allowed:
        raise MemoryRuleError(NOT_FOUND)
    return row


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
) -> list[Flag]:
    flags = []
    for problem in check_relation(onto, rel, subject_type, object_type, existing):
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
    for moved, others in to_check:
        subject, obj = (entities.get(e) for e in (moved.subject_id, moved.object_id))
        if subject is None or obj is None or {subject["status"], obj["status"]} - set(LIVE):
            raise MemoryRuleError(NOT_FOUND)  # the far end was rejected or merged away
        await _flag_problems(w, onto, moved, subject["type"], obj["type"], others)
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
