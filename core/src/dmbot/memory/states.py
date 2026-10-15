"""State facts and the checks that read them (docs/STORY_MEMORY.md, "State facts"; #1267).

What a character, item or place is right now (alive, dead, intact, destroyed…) lives in its own
table, `memory_states`: one current row for each entry, and a new condition closes it, so the
history is kept with when it changed and who said so. This module is the write side and the
bits of the store's relationship checks that need more than the relationship (which items are
one of a kind, which places are inside which, who is dead or destroyed). The checks themselves
are pure, in `checks`; everything here only gathers what they need, one campaign at a time.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from dmbot.memory._changes import ENTITIES, FLAGS, STATES, Changes, Scope
from dmbot.memory.checks import ACTING, ACTS_AFTER_END, NESTS, Nesting, acts_after_end
from dmbot.memory.models import (
    DM,
    ENDED,
    REJECTED,
    Entity,
    MemoryRuleError,
    Relation,
    State,
    new_id,
)
from dmbot.memory.ontology import Ontology, check_condition_change, conditions_for

NOT_THE_DM = "Only the DM can change that."
ONLY_ITEMS = "Only an item can be one of a kind."
BAD_TIME = "That date isn't one DMbot can use."
INT64_MAX = 2**63 - 1


def state_of(r: Mapping[str, Any]) -> State:
    return State(
        r["id"], r["entity_id"], r["condition"], r["from_session_at"], r["source"],
        r["created_at"], r["closed_at"],
    )  # fmt: skip


@dataclass(frozen=True, slots=True)
class Extras:
    """What the checks need besides the facts themselves, read once for a batch of facts."""

    unique: frozenset[str] = frozenset()  # items of which only one exists
    nesting: Nesting | None = None  # places inside places
    ended: Mapping[str, int] = field(default_factory=dict)  # entry → when it died or was destroyed


async def current_states(w: Scope, entity_ids: Collection[str]) -> dict[str, State]:
    rows = await w.select(
        STATES, " AND closed_at IS NULL AND entity_id = ANY(%s)", [list(entity_ids)]
    )
    return {r["entity_id"]: state_of(r) for r in rows}


async def ended_since(w: Scope, entity_ids: Collection[str]) -> dict[str, int]:
    """When each of these entries died or was destroyed (only those that have)."""
    if not entity_ids:
        return {}
    rows = await w.select(
        STATES,
        " AND closed_at IS NULL AND condition = ANY(%s) AND entity_id = ANY(%s)",
        [list(ENDED), sorted(entity_ids)],
    )
    return {r["entity_id"]: state_of(r).since for r in rows}


async def place_nesting(w: Scope, onto: Ontology) -> Nesting:
    """The places inside places now: live `located_in` facts that haven't ended, between two
    entries of a place kind. One read, only those facts."""
    places = sorted(k for k in onto.types if onto.is_a(k, "place"))
    cur = await w.conn.execute(
        "SELECT r.subject_id, r.object_id FROM memory_relations r"
        " JOIN memory_entities s ON (s.guild_id, s.campaign_id, s.id)"
        "    = (r.guild_id, r.campaign_id, r.subject_id)"
        " JOIN memory_entities o ON (o.guild_id, o.campaign_id, o.id)"
        "    = (r.guild_id, r.campaign_id, r.object_id)"
        " WHERE r.guild_id = %s AND r.campaign_id = %s AND r.predicate = %s"
        " AND r.status <> 'rejected' AND r.to_session_at IS NULL"
        " AND s.type = ANY(%s) AND o.type = ANY(%s)",
        (*w.ids, NESTS, places, places),
    )
    return Nesting((r["subject_id"], r["object_id"]) for r in await cur.fetchall())


async def load_extras(w: Scope, onto: Ontology, facts: Sequence[Relation]) -> Extras:
    """What the checks need for these facts, and only what they need: nothing is read for a
    fact of a relationship with no such rule."""
    limited = {
        f.object_id
        for f in facts
        if (t := onto.predicates.get(f.predicate)) is not None and t.max_per_object is not None
    }
    unique: set[str] = set()
    if limited:
        for row in await w.select(ENTITIES, " AND id = ANY(%s)", [sorted(limited)]):
            if onto.is_a(row["type"], "item") and entity_is_unique(row):
                unique.add(row["id"])
    nesting = await place_nesting(w, onto) if any(f.predicate == NESTS for f in facts) else None
    actors: set[str] = set()
    for f in facts:
        term = onto.predicates.get(f.predicate)
        if term is not None and term.key in ACTING:
            actors.add(f.subject_id)
            if term.symmetric:
                actors.add(f.object_id)
    return Extras(frozenset(unique), nesting, await ended_since(w, actors))


def entity_is_unique(row: Mapping[str, Any]) -> bool:
    """Is this item one of a kind: the DM's word, else whether its name is a proper name."""
    return Entity(
        row["id"], row["type"], row["name"], "", row["status"], None, "", 0,
        unique_item=row["unique_item"],
    ).is_unique  # fmt: skip


def _check_when(when: int | None) -> None:
    if when is not None and (isinstance(when, bool) or not 0 <= when <= INT64_MAX):
        raise MemoryRuleError(BAD_TIME)


async def set_condition(
    w: Changes,
    onto: Ontology,
    entity: Mapping[str, Any],
    condition: str,
    *,
    from_session_at: int | None,
) -> State | None:
    """Give an entry a condition, closing its current one. Returns the new state, or None
    when it already had that condition (nothing changes). Refused: a condition the kind
    can't have, a change the table doesn't allow, the way back from dead or destroyed
    without the DM, and a proposal over what the DM said."""
    _check_when(from_session_at)
    held = (await current_states(w, [entity["id"]])).get(entity["id"])
    old = None if held is None else held.condition
    check_condition_change(onto, entity["type"], entity["name"], old, condition, w.source)
    if held is None and conditions_for(onto, entity["type"]) is None:
        raise MemoryRuleError("DMbot keeps track of this only for characters, items and places.")
    if held is not None and held.condition == condition:
        return None
    if held is not None:
        if held.source == DM and w.source != DM:
            raise MemoryRuleError(NOT_THE_DM)
        await w.update(STATES, held.id, {"closed_at": w.now})
    row = await w.insert(
        STATES,
        {
            "id": new_id(),
            "entity_id": entity["id"],
            "condition": condition,
            "from_session_at": from_session_at,
            "source": w.source,
            "created_at": w.now,
            "closed_at": None,
        },
    )
    return state_of(row)


async def close_for_new_kind(w: Changes, onto: Ontology, entity_id: str, type_key: str) -> None:
    """An entry that becomes another kind keeps no condition its new kind can't have (an item
    made a character is no longer `intact`): the current one is closed, the history stays."""
    held = (await current_states(w, [entity_id])).get(entity_id)
    allowed = conditions_for(onto, type_key)
    if held is not None and (allowed is None or held.condition not in allowed):
        await w.update(STATES, held.id, {"closed_at": w.now})


async def move_states(w: Changes, keep_id: str, gone_id: str) -> None:
    """A merged entry's current condition goes with it, unless the kept entry has its own
    (the one the DM kept wins; the other stays behind as history)."""
    current = await current_states(w, [keep_id, gone_id])
    if gone_id in current and keep_id not in current:
        await w.update(STATES, current[gone_id].id, {"entity_id": keep_id})


async def flag_new(
    w: Changes, rel: Relation, kind: str, other_id: str | None = None
) -> dict[str, Any] | None:
    """Flag a fact for review, unless the same flag is already open on it."""
    for f in await w.select(FLAGS, " AND relation_id = %s AND status = 'open'", [rel.id]):
        if (f["kind"], f["other_id"]) == (kind, other_id):
            return None
    return await w.insert(
        FLAGS,
        {
            "id": new_id(),
            "kind": kind,
            "relation_id": rel.id,
            "other_id": other_id,
            "status": "open",
            "created_at": w.now,
        },
    )


async def flag_acting_after_end(
    w: Changes, onto: Ontology, facts: Sequence[Relation], ended: Mapping[str, int]
) -> list[dict[str, Any]]:
    """Flag each of these facts that a dead or destroyed entry acts in after it ended. The
    flag rows written."""
    out: list[dict[str, Any]] = []
    for rel in facts:
        if rel.status != REJECTED and acts_after_end(onto, rel, ended):
            row = await flag_new(w, rel, ACTS_AFTER_END)
            if row is not None:
                out.append(row)
    return out


async def close_resolved_after_end(w: Changes, onto: Ontology, facts: Sequence[Relation]) -> None:
    """Resolve the open "acts after it ended" flags on these facts that no longer hold (the
    DM said the entry is back, or it ended later than the fact)."""
    if not facts:
        return
    by_id = {f.id: f for f in facts}
    ends = {e for f in facts for e in (f.subject_id, f.object_id)}
    ended = await ended_since(w, ends)
    for flag in await w.select(
        FLAGS, " AND kind = %s AND status = 'open' AND relation_id = ANY(%s)",
        [ACTS_AFTER_END, sorted(by_id)],
    ):  # fmt: skip
        if not acts_after_end(onto, by_id[flag["relation_id"]], ended):
            await w.update(FLAGS, flag["id"], {"status": "resolved"})
