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

import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from dmbot.db import Database, row_int
from dmbot.memory import notify
from dmbot.memory._changes import (
    ALIASES,
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
    NewName,
    Relation,
    Written,
    check_status_change,
    clean_text,
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
_LIVE_ENTITY_IDS = (
    " (SELECT id FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
    " AND status IN ('proposed', 'confirmed'))"
)


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


class MemoryStore:
    """Reads and writes one campaign's memory at a time. See the module docstring."""

    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

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
    async def _read(self, guild_id: int, campaign_id: str) -> AsyncIterator[Scope]:
        async with self._db.guild(guild_id) as conn:
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
        async with self._read(guild_id, campaign_id) as scope:
            rows = await scope.select(
                ALIASES,
                " AND status <> 'rejected' AND (%s::text IS NULL OR entity_id = %s)"
                " AND (%s OR NOT secret) AND entity_id IN" + _LIVE_ENTITY_IDS + " ORDER BY key, id",
                [entity_id, entity_id, include_secret, *scope.ids],
            )
            return [_alias(r) for r in rows]

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
        async with self._read(guild_id, campaign_id) as scope:
            rows = await scope.select(
                RELATIONS,
                " AND status = ANY(%s) AND (%s::text IS NULL OR %s IN (subject_id, object_id))"
                " AND (%s OR NOT secret) AND subject_id IN"
                + _LIVE_ENTITY_IDS
                + " AND object_id IN"
                + _LIVE_ENTITY_IDS
                + " ORDER BY created_at, id",
                [statuses, entity_id, entity_id, include_secret, *scope.ids, *scope.ids],
            )
            return [_relation(r) for r in rows]

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
            aliases = await scope.select(
                ALIASES,
                " AND status <> 'rejected' AND entity_id IN" + _LIVE_ENTITY_IDS + " ORDER BY id",
                scope.ids,
            )
            corrections = await scope.select(CORRECTIONS, " ORDER BY id")
            relations = await scope.select(
                RELATIONS,
                " AND status IN ('proposed', 'confirmed') AND NOT secret AND subject_id IN"
                + _LIVE_ENTITY_IDS
                + " AND object_id IN"
                + _LIVE_ENTITY_IDS
                + " ORDER BY id",
                [*scope.ids, *scope.ids],
            )
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
        self, guild_id: int, campaign_id: str, names: Sequence[NewName], *, source: str
    ) -> Written[list[str]]:
        """Many names at once (📥 Add many), each with its other and secret names, in one
        change: one Undo takes the whole list back, and live transcription reloads its
        names once. Only the DM gives lists. Returns the new entries' IDs, in order."""
        if source != DM:
            raise MemoryRuleError("Only the DM can add a list of names.")
        for n in names:
            _check_choice(n.status, LIVE, "entity status")
        async with self._write(guild_id, campaign_id, source) as w:
            onto = await _load_ontology(w)
            ids: list[str] = []
            for n in names:
                onto.active_type(n.type)
                if onto_is_pc(onto, n.type):
                    raise MemoryRuleError("A player's character needs its player.")
                name = clean_text(n.name)
                row = await w.insert(
                    ENTITIES,
                    {
                        "id": new_id(), "type": n.type, "name": name, "description": "",
                        "status": n.status, "merged_into": None, "source": source,
                        "created_at": w.now, "played_by": None,
                    },
                )  # fmt: skip
                seen = {lookup_key(name)}
                aliases = [(name, "full", False)]
                aliases += [(clean_text(t), "nickname", False) for t in n.others]
                aliases += [(clean_text(t), "title", True) for t in n.secrets]
                for text, kind, secret in aliases:
                    key = lookup_key(text)
                    if key in seen and kind != "full":
                        continue
                    seen.add(key)
                    await w.insert(
                        ALIASES, _new_alias(w, row["id"], text, kind, n.status, secret, None)
                    )
                ids.append(row["id"])
            return Written(ids, w.batch)

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
    ) -> Written[Entity]:
        """Two entries are the same person or thing: move everything onto `keep_id`.

        Two proposed entries of the same kind may merge on strong evidence. If either is
        confirmed, or their kinds differ, only the DM can say they're the same. Facts
        moved over are checked again (duplicates folded, problems flagged). Undo splits
        them again.
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
            await _move_aliases(w, keep_id, gone_id)
            own = await w.select(
                ALIASES, " AND entity_id = %s AND key = %s", [keep_id, lookup_key(keep["name"])]
            )
            if own and own[0]["secret"]:
                # The other entry's name was a secret one: the main name everyone sees
                # must never be.
                raise MemoryRuleError("A secret name can't be the main name.")
            await _move_relations(w, keep_id, gone_id)
            for table in (MENTIONS, CORRECTIONS):
                for row in await w.select(table, " AND entity_id = %s", [gone_id]):
                    await w.update(table, row["id"], {"entity_id": keep_id})
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
        async with self._write(guild_id, campaign_id, source, undoes=batch) as w:
            await undo_batch(w, batch)
            return Written(None, w.batch)


# ---- helpers (inside a write) ----------------------------------------------------------


def onto_is_pc(onto: Ontology, type_key: str) -> bool:
    return onto.is_a(type_key, "player_character")


def _new_alias(
    w: Changes,
    entity_id: str,
    text: str,
    kind: str,
    status: str,
    secret: bool,
    used_by: str | None,
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
        "sound_codes": list(sound_codes(text)),
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
    rows = await w.select(
        RELATIONS,
        " AND (subject_id = ANY(%s) OR object_id = ANY(%s))",
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


async def _move_aliases(w: Changes, keep_id: str, gone_id: str) -> None:
    keep_aliases = {a["key"]: a for a in await w.select(ALIASES, " AND entity_id = %s", [keep_id])}
    for alias in await w.select(ALIASES, " AND entity_id = %s", [gone_id]):
        twin = keep_aliases.get(alias["key"])
        if twin is None:
            await w.update(ALIASES, alias["id"], {"entity_id": keep_id})
            continue
        # Both have it: keep one, with the stronger status and any secret mark.
        upgrade = _upgrade(twin, alias["status"], alias["secret"], DM)
        await w.delete(ALIASES, alias["id"])
        if upgrade:
            await w.update(ALIASES, twin["id"], upgrade)
    for alias in await w.select(ALIASES, " AND used_by = %s", [gone_id]):
        await w.update(ALIASES, alias["id"], {"used_by": keep_id})


async def _move_relations(w: Changes, keep_id: str, gone_id: str) -> None:
    onto = await _load_ontology(w)
    for row in await w.select(
        RELATIONS, " AND (subject_id = %s OR object_id = %s)", [gone_id, gone_id]
    ):
        subject = keep_id if row["subject_id"] == gone_id else row["subject_id"]
        obj = keep_id if row["object_id"] == gone_id else row["object_id"]
        if subject == obj:  # "Bell is an ally of Belleros" says nothing any more
            await _delete_relation(w, row["id"])
            continue
        pred = onto.predicates.get(row["predicate"])
        if pred is not None:
            subject, obj = ordered(pred, subject, obj)
        moved = _relation(
            await w.update(RELATIONS, row["id"], {"subject_id": subject, "object_id": obj})
        )
        others = [r for r in await _relations_touching(w, subject, obj) if r.id != moved.id]
        twin = duplicate_of(moved, others)
        if twin is not None:  # both said the same: keep one, with the stronger status
            upgrade = _upgrade(_relation_row(twin), moved.status, moved.secret, DM)
            await _delete_relation(w, moved.id)
            if upgrade:
                await w.update(RELATIONS, twin.id, upgrade)
            continue
        if pred is not None and moved.status != REJECTED:
            types = {e: (await _entity_row(w, e))["type"] for e in (subject, obj)}
            await _flag_problems(w, onto, moved, types[subject], types[obj], others)


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
