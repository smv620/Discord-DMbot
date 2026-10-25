"""Contradictions by point of view (#2660; docs/STORY_MEMORY.md section 9a, "Contradictions").

A candidate is two assertions that conflict under the ontology, held by the **same** point of view
(The truth, the party, one character), whose **story times** overlap and whose **belief times**
overlap. Different points of view disagreeing is never one: the party believing what the DM's view
denies is story (a theory, a lie, a disguise), and goes into the after-session report instead.

The rules are the ones `checks.check_relation` already applies to a new connection (a clashing
relationship between the same two names, too many of a kind, a one-of-a-kind item held twice), run
here between the connections one point of view holds. Nothing here changes a belief or picks the
true one: a candidate is only something to put in front of the DM.

Pure functions plus one reader. The reader reads one campaign, in one snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dmbot.memory._changes import BELIEFS, ENTITIES, PERSPECTIVES, RELATIONS
from dmbot.memory.checks import (
    CONTRADICTION,
    HELD_TWICE,
    TOO_MANY,
    Problem,
    _overlap,
    check_relation,
    waiting_guess,
)
from dmbot.memory.models import CONFIRMED, FALSE_EVIDENCE, Belief, Entity, Relation
from dmbot.memory.ontology import Ontology

if TYPE_CHECKING:
    from dmbot.memory.store import MemoryStore

# Only these rules are about two assertions meeting; the others (a wrong end, a place inside itself,
# a dead name acting) are about one connection and stay the continuity warnings they were.
KINDS = (CONTRADICTION, TOO_MANY, HELD_TWICE)

# A belief of less than even odds ("unlikely") does not assert anything, so it cannot contradict.
ASSERTS_FROM = 0.5


@dataclass(frozen=True, slots=True)
class Held:
    """One point of view's belief in one connection, and the span of belief time it was current:
    from the session it was formed in until the session of the next belief it gave way to (None at
    either end: not known / still current, so it counts as overlapping anything)."""

    belief: Belief
    relation: Relation
    held_from: int | None
    held_until: int | None


@dataclass(frozen=True, slots=True)
class Candidate:
    """Two connections one point of view holds that may clash. Ids only: no name, no words."""

    perspective_id: str
    kind: str
    first_relation_id: str  # the smaller id, so the pair is one candidate whichever way it is met
    second_relation_id: str
    times_unknown: bool  # no game time on one of them: "The game times aren't known"


def asserts(belief: Belief) -> bool:
    """Does this belief say the connection is so? Not a denial (The truth at 0.0, False Evidence)
    and not a long shot."""
    return belief.confidence >= ASSERTS_FROM and belief.status != FALSE_EVIDENCE


def spans(history: Sequence[Belief]) -> list[tuple[Belief, int | None, int | None]]:
    """The belief times of one point of view's beliefs in one connection, oldest first: each held
    from its own session until the next one's; the newest is still held (no end). A belief that was
    replaced by one with no known session has no knowable end, so it is left out: what a point of
    view holds NOW is its newest belief, and an old one whose end cannot be placed would only
    raise warnings the DM cannot check."""
    ordered = sorted(history, key=lambda b: b.seq)
    out: list[tuple[Belief, int | None, int | None]] = []
    for n, belief in enumerate(ordered):
        if n + 1 == len(ordered):
            out.append((belief, belief.session_at, None))
        elif ordered[n + 1].session_at is not None:
            out.append((belief, belief.session_at, ordered[n + 1].session_at))
    return out


def _problems(
    onto: Ontology,
    mine: Held,
    others: Sequence[Held],
    types: Mapping[str, str],
    unique: frozenset[str],
) -> list[Problem]:
    live = [
        o.relation
        for o in others
        if o.belief.perspective_id == mine.belief.perspective_id
        and o.relation.id != mine.relation.id
        and _overlap(mine.held_from, mine.held_until, o.held_from, o.held_until)
    ]
    r = mine.relation
    if r.predicate not in onto.predicates:
        return []
    found = check_relation(
        onto, r, types.get(r.subject_id, ""), types.get(r.object_id, ""), live,
        object_unique=r.object_id in unique,
    )  # fmt: skip
    return [p for p in found if p.kind in KINDS and p.other_id is not None]


def candidates(
    onto: Ontology,
    held: Sequence[Held],
    types: Mapping[str, str],
    unique: frozenset[str] = frozenset(),
) -> list[Candidate]:
    """The candidates among what these points of view hold, each pair once, the surest first (the
    ones whose game times are known come before those that are not). Pure.
    `types`: each entry's kind; `unique`: the items of which only one exists."""
    held = [h for h in held if asserts(h.belief) and not waiting_guess(h.relation)]
    relations = {h.relation.id: h.relation for h in held}
    seen: set[tuple[str, str, str, str]] = set()
    out: list[Candidate] = []
    for mine in held:
        # (`_problems` only looks at what the same point of view held at an overlapping time)
        for problem in _problems(onto, mine, held, types, unique):
            other = relations.get(problem.other_id or "")
            if other is None:
                continue
            first, second = sorted((mine.relation.id, other.id))
            key = (mine.belief.perspective_id, problem.kind, first, second)
            if key in seen:
                continue
            seen.add(key)
            unknown = None in (mine.relation.from_game_time, other.from_game_time)
            out.append(Candidate(mine.belief.perspective_id, problem.kind, first, second, unknown))
    return sorted(out, key=lambda c: (c.times_unknown, c.kind, c.first_relation_id))


class Contradictions:
    """Reads one campaign's beliefs and finds the candidates (no writing)."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def find(self, guild_id: int, campaign_id: str) -> list[Candidate]:
        from dmbot.memory.beliefs import belief_of
        from dmbot.memory.store import _load_ontology, _relation

        async with self._store._read(guild_id, campaign_id, snapshot=True) as scope:
            onto = await _load_ontology(scope)
            relations = {
                r["id"]: r for r in await scope.select(RELATIONS, " AND status = %s", [CONFIRMED])
            }
            # Beliefs hold no word of anyone's: ids, numbers and a status, read as the store does.
            rows = await scope.select(BELIEFS, "")
            kept = {p["id"] for p in await scope.select(PERSPECTIVES, "")}
            entities = await scope.select(ENTITIES, " AND status = %s", [CONFIRMED])
        types = {e["id"]: str(e["type"]) for e in entities}
        unique = frozenset(
            str(e["id"]) for e in entities if onto.is_a(str(e["type"]), "item") and _is_unique(e)
        )
        histories: dict[tuple[str, str], list[Belief]] = {}
        for row in rows:
            if row["perspective_id"] in kept and row["relation_id"] in relations:
                key = (row["perspective_id"], row["relation_id"])
                histories.setdefault(key, []).append(belief_of(row))
        held = [
            Held(belief, _relation(relations[relation_id]), start, end)
            for (_, relation_id), history in histories.items()
            for belief, start, end in spans(history)
        ]
        # Every belief in the history is offered: the spans decide which ones overlap in time.
        return candidates(onto, held, types, unique)


def _is_unique(row: Mapping[str, object]) -> bool:
    return Entity(
        str(row["id"]), str(row["type"]), str(row["name"]), "", str(row["status"]), None, "", 0,
        unique_item=row["unique_item"] if isinstance(row["unique_item"], bool) else None,
    ).is_unique  # fmt: skip
