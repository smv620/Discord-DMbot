"""A shared rule set carries relationship definitions (#1456; docs/PLAN.md, "Shared rulesets").

A DM publishing a house-rules set may tick some of the campaign's **own** relationship
definitions to travel with it. Only the definition crosses (its words and the kinds of thing it
connects), never a fact written with it, a name, a note or anything else of memory. A follower
gets them **read-only, marked as the set's** (`memory_predicates.from_set`); they go when the set
is unfollowed.

- A definition the follower already has by the same key, or an official one, is **never
  overwritten**: the follower's wins and the DM is told in one line at Follow time.
- A definition that connects a kind of thing only the publisher has made (a custom type) can't
  travel; only definitions built from the official kinds do. Anything the memory's ordinary
  checks refuse (a rules word as a name, a clashing synonym) is left out with a note, never forced.
- Unfollow (and Cherry pick for the ones not ticked): a definition no fact uses is removed; one
  that facts use is **deprecated**, so old facts still read and no new fact can use it. Cherry
  pick's ticked ones become the campaign's own (the follow ends, as for rules).

Every sync is one memory change batch, so it can be undone as one. Pure shaping and checking is at
the top; the database parts take the `MemoryStore`.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.memory._changes import PREDICATES, TYPES
from dmbot.memory.definition_content import (
    CORE_PREDICATE_KEYS,
    FROM_SET_REASON,
    MAX_DEFINITIONS,
    definition_of,
    shareable,
    tick_line,
    valid_definitions,
)
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.ontology import ACTIVE, DEPRECATED, Ontology, PredicateTerm
from dmbot.memory.store import MemoryStore, _load_ontology, _predicate_term, _type_term

# The pure parts live in definition_content (the rules layer imports them without the store);
# they are re-exported here, where callers and tests have always found them.
__all__ = [
    "CORE_PREDICATE_KEYS",
    "FROM_SET_REASON",
    "MAX_DEFINITIONS",
    "Synced",
    "definition_of",
    "shareable",
    "tick_line",
    "valid_definitions",
]


@dataclass(frozen=True, slots=True)
class Synced:
    added: tuple[str, ...] = ()  # labels
    updated: tuple[str, ...] = ()
    kept_own: tuple[str, ...] = ()  # the follower's own (or an official) one stayed
    refused: tuple[str, ...] = ()  # left out: a memory rule said no
    removed: tuple[str, ...] = ()
    deprecated: tuple[str, ...] = ()
    batch: int | None = None

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed or self.deprecated)


async def own_definitions(
    memory: MemoryStore, guild_id: int, campaign_id: str
) -> list[dict[str, Any]]:
    """The campaign's own relationships that may travel, as definitions, for the tick list.
    Ones that came from a followed set are not its own to share."""
    async with memory._read(guild_id, campaign_id) as scope:
        onto = await _load_ontology(scope)
        cur = await scope.conn.execute(
            "SELECT key FROM memory_predicates WHERE guild_id = %s AND campaign_id = %s"
            " AND from_set IS NOT NULL",
            scope.ids,
        )
        borrowed = {str(r["key"]) for r in await cur.fetchall()}
    terms = [t for t in onto.predicates.values() if shareable(t) and t.key not in borrowed]
    return [definition_of(t) for t in sorted(terms, key=lambda t: t.key)]


async def sync(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    set_id: str,
    set_name: str,
    definitions: Sequence[Mapping[str, Any]],
    *,
    source: str,
    then_own: bool = False,
) -> Synced:
    """Make the campaign's borrowed definitions from this set match `definitions` (the followed
    version's), in one batch: add the new, bring changed ones up to date (one the follower had
    retired because facts use it comes back), and take away those the set no longer carries. A
    definition the campaign already has of its own stays. Every added or changed one passes the
    memory's ordinary checks; one that fails is left out and reported, never forced.
    `then_own` (Cherry pick, which ends the follow): the definitions in `definitions` become the
    campaign's own in the same batch, and any other of the set's go."""
    wanted = valid_definitions(list(definitions))
    added: list[str] = []
    updated: list[str] = []
    kept: list[str] = []
    refused: list[str] = []
    async with memory._write(guild_id, campaign_id, source) as w:
        rows = {str(r["key"]): r for r in await w.select(PREDICATES)}
        reason = FROM_SET_REASON.format(name=set_name)[:300]
        for d in wanted:
            key = d["key"]
            row = rows.get(key)
            if row is not None and row["from_set"] != set_id:
                kept.append(d["label"])  # the campaign's own, an official one, or another set's
                continue
            if row is None and key in CORE_PREDICATE_KEYS:
                kept.append(d["label"])
                continue
            fields = _fields(d)
            if (
                row is not None
                and row["status"] == ACTIVE
                and all(row[k] == v for k, v in fields.items())
            ):
                continue  # already as the set has it
            onto = await _ontology_without(w, key)
            try:
                onto.check_new_term(key, d["label"], d["description"], [_example_of(d)], reason)
                onto.check_new_predicate(_term_of(d))
            except MemoryRuleError:
                refused.append(d["label"])
                continue
            if row is None:
                await w.insert(
                    PREDICATES,
                    fields
                    | {
                        "key": key,
                        "parent": None,
                        "reason": reason,
                        "max_per_subject": None,
                        "conflicts_with": [],
                        "status": ACTIVE,
                        "replaced_by": None,
                        "created_at": w.now,
                        "from_set": set_id,
                    },
                )
                added.append(d["label"])
            else:
                await w.update(PREDICATES, key, fields | {"status": ACTIVE, "replaced_by": None})
                updated.append(d["label"])
        wanted_keys = {d["key"] for d in wanted}
        if then_own:
            gone = await _release(w, set_id, set(), keep=wanted_keys)
        else:
            gone = await _release(w, set_id, wanted_keys, keep=())
    return Synced(
        tuple(added), tuple(updated), tuple(kept), tuple(refused), gone[0], gone[1], w.batch
    )


async def borrowed_set_ids(memory: MemoryStore, guild_id: int, campaign_id: str) -> list[str]:
    """The sets this campaign has borrowed definitions from (normally none or one)."""
    async with memory._read(guild_id, campaign_id) as scope:
        cur = await scope.conn.execute(
            "SELECT DISTINCT from_set FROM memory_predicates WHERE guild_id = %s"
            " AND campaign_id = %s AND from_set IS NOT NULL ORDER BY from_set",
            scope.ids,
        )
        return [str(r["from_set"]) for r in await cur.fetchall()]


async def release(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    set_id: str,
    *,
    keep: Collection[str] = (),
    source: str,
) -> Synced:
    """The set's definitions leave the campaign (Unfollow) in one batch: unused ones are removed,
    used ones retired (kept for the facts that use them, no longer usable for new ones). The keys
    in `keep` (Cherry pick's ticked ones) stay as the campaign's own."""
    async with memory._write(guild_id, campaign_id, source) as w:
        gone = await _release(w, set_id, set(), keep=set(keep))
    return Synced(removed=gone[0], deprecated=gone[1], batch=w.batch)


async def _release(
    w: Any, set_id: str, still_wanted: Collection[str], *, keep: Collection[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Take the set's rows that are not wanted any more out of the campaign. A retired row keeps
    its mark as the set's (still read-only; a later Follow brings it back to use), so undoing this
    puts everything back exactly as it was."""
    removed: list[str] = []
    retired: list[str] = []
    rows = sorted(
        (r for r in await w.select(PREDICATES) if r["from_set"] == set_id), key=lambda r: r["key"]
    )
    for row in rows:
        key, label = str(row["key"]), str(row["label"])
        if key in still_wanted:
            continue
        if key in keep:  # becomes the campaign's own
            await w.update(PREDICATES, key, {"from_set": None})
            continue
        used = await w.conn.execute(
            "SELECT 1 FROM memory_relations WHERE guild_id = %s AND campaign_id = %s"
            " AND predicate = %s LIMIT 1",
            (*w.ids, key),
        )
        if await used.fetchone() is None:
            try:
                await w.delete(PREDICATES, key)
                removed.append(label)
                continue
            except MemoryRuleError:
                pass  # another term still points at it: retire it instead of failing the lot
        if row["status"] != DEPRECATED:
            await w.update(PREDICATES, key, {"status": DEPRECATED})
            retired.append(label)
    return tuple(removed), tuple(retired)


async def _ontology_without(w: Any, key: str) -> Ontology:
    """The campaign's terms as they are, minus one predicate (so it can be checked as new)."""
    types = [_type_term(r) for r in await w.select(TYPES)]
    predicates = [_predicate_term(r) for r in await w.select(PREDICATES) if r["key"] != key]
    return Ontology.build(types, predicates)


def _example_of(d: Mapping[str, Any]) -> str:
    """The one example a new term needs, made from the definition itself: a followed set carries no
    examples (they hold the publisher's names), so the follower's copy gets a plain one."""
    return f"X {d['label']} Y."[:200]


def _fields(d: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "label": d["label"],
        "description": d["description"],
        "examples": [_example_of(d)],
        "subject_types": list(d["subject_types"]),
        "object_types": list(d["object_types"]),
        "is_symmetric": bool(d["symmetric"]),
        "covers": [],
        "not_this": [],
        "inverse_label": d["inverse_label"],
        "synonyms": [],
        "version": int(d["version"]),
    }


def _term_of(d: Mapping[str, Any]) -> PredicateTerm:
    return PredicateTerm(
        key=d["key"],
        label=d["label"],
        description=d["description"],
        subject_types=tuple(d["subject_types"]),
        object_types=tuple(d["object_types"]),
        symmetric=bool(d["symmetric"]),
        core=False,
        examples=(_example_of(d),),
        inverse_label=d["inverse_label"],
        version=int(d["version"]),
    )
