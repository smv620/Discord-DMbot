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
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from dmbot.memory._changes import BELIEFS, ENTITIES, PERSPECTIVES, RELATIONS
from dmbot.memory.checks import (
    CONTRADICTION,
    HELD_TWICE,
    TOO_MANY,
    Nesting,
    Problem,
    _overlap,
    check_relation,
    waiting_guess,
)
from dmbot.memory.models import CONFIRMED, FALSE_EVIDENCE, Belief, Entity, Relation
from dmbot.memory.ontology import Ontology

if TYPE_CHECKING:
    from dmbot.memory.store import MemoryStore

TRUTH = "truth"  # the perspective kind of the DM's own view

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
    kind: str = "party"  # whose view: truth, party, pc or npc
    guess: bool = False  # the connection only holds a guess nobody has kept (read as if kept)


@dataclass(frozen=True, slots=True)
class Candidate:
    """Two connections one point of view holds that may clash. Ids only: no name, no words."""

    perspective_id: str
    kind: str
    first_relation_id: str  # the smaller id, so the pair is one candidate whichever way it is met
    second_relation_id: str
    times_unknown: bool  # no game time on one of them: "The game times aren't known"
    involves_guess: bool = False  # one of the two is a guess a character voiced, not a kept fact


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
            continue
        end = ordered[n + 1].session_at
        if end is None:
            continue
        # formed and changed in the same session (or recorded out of order): never held for any
        # time, so it can meet nothing
        if belief.session_at is not None and belief.session_at >= end:
            continue
        out.append((belief, belief.session_at, end))
    return out


# Only a point of view that is not The truth can break "too many" or "held twice" by BELIEVING it:
# The truth's beliefs are the DM's own confirmed connections, and the warning the DM saw when
# confirming them already said it. A clashing pair (CONTRADICTION) counts for every point of view.
BY_BELIEF_ONLY = (TOO_MANY, HELD_TWICE)


def _problems(
    onto: Ontology,
    mine: Held,
    near: Sequence[Held],
    types: Mapping[str, str],
    unique: frozenset[str],
    nesting: Nesting | None,
) -> list[Problem]:
    """What `check_relation` finds against the connections this point of view held at an
    overlapping time that touch the same names (`near`)."""
    live = [
        o.relation
        for o in near
        if o.relation.id != mine.relation.id
        and _overlap(mine.held_from, mine.held_until, o.held_from, o.held_until)
    ]
    r = mine.relation
    if r.predicate not in onto.predicates:
        return []
    found = check_relation(
        onto, r, types.get(r.subject_id, ""), types.get(r.object_id, ""), live,
        object_unique=r.object_id in unique, nesting=nesting,
    )  # fmt: skip
    return [
        p
        for p in found
        if p.kind in KINDS
        and p.other_id is not None
        and not (mine.kind == TRUTH and p.kind in BY_BELIEF_ONLY)
    ]


def candidates(
    onto: Ontology,
    held: Sequence[Held],
    types: Mapping[str, str],
    unique: frozenset[str] = frozenset(),
    nesting: Nesting | None = None,
) -> list[Candidate]:
    """The candidates among what these points of view hold, each pair once, the surest first (the
    ones whose game times are known come before those that are not). Pure.
    `types`: each entry's kind; `unique`: the items of which only one exists; `nesting`: the places
    inside places (being in the inn and in the town it is in is one place, not two)."""
    held = [h for h in held if asserts(h.belief)]
    relations = {h.relation.id: h.relation for h in held}
    guesses = {h.relation.id for h in held if h.guess}
    # The connections The truth holds: the party's and the characters' copies of two facts the DM
    # holds together are not a disagreement (the DM was warned when confirming them).
    truth_has = {h.relation.id for h in held if h.kind == TRUTH}
    # What a point of view holds, by the names it touches: a rule only ever meets facts that share
    # a name with the one it checks, so each belief meets a few neighbours, not every other belief.
    touching: dict[tuple[str, str], list[Held]] = {}
    for h in held:
        for end in {h.relation.subject_id, h.relation.object_id}:
            touching.setdefault((h.belief.perspective_id, end), []).append(h)
    seen: set[tuple[str, str, str, str]] = set()
    out: list[Candidate] = []
    for mine in held:
        perspective = mine.belief.perspective_id
        near = {
            id(h): h
            for end in (mine.relation.subject_id, mine.relation.object_id)
            for h in touching.get((perspective, end), ())
        }
        for problem in _problems(onto, mine, list(near.values()), types, unique, nesting):
            other = relations.get(problem.other_id or "")
            if other is None:
                continue
            if (
                problem.kind in BY_BELIEF_ONLY
                and mine.relation.id in truth_has
                and other.id in truth_has
            ):
                continue
            first, second = sorted((mine.relation.id, other.id))
            key = (perspective, problem.kind, first, second)
            if key in seen:
                continue
            seen.add(key)
            unknown = None in (mine.relation.from_game_time, other.from_game_time)
            involves = mine.relation.id in guesses or other.id in guesses
            out.append(Candidate(perspective, problem.kind, first, second, unknown, involves))
    return sorted(out, key=lambda c: (c.times_unknown, c.kind, c.first_relation_id))


class Contradictions:
    """Reads one campaign's beliefs and finds the candidates (no writing)."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def find(self, guild_id: int, campaign_id: str) -> list[Candidate]:
        from dmbot.memory.beliefs import belief_of
        from dmbot.memory.states import place_nesting
        from dmbot.memory.store import _load_ontology, _relation

        async with self._store._read(guild_id, campaign_id, snapshot=True) as scope:
            onto = await _load_ontology(scope)
            # Kept connections, and the waiting guess connections a reading placed (a secret
            # proposal made by the extractor): a character's guesses can clash too.
            relations = {
                r["id"]: r
                for r in await scope.select(
                    RELATIONS,
                    " AND (status = %s OR (status = 'proposed' AND secret"
                    " AND source = 'extractor'))",
                    [CONFIRMED],
                )
            }
            # Beliefs hold no word of anyone's: ids, numbers and a status, read as the store does.
            rows = await scope.select(BELIEFS, "")
            kept = {p["id"]: str(p["kind"]) for p in await scope.select(PERSPECTIVES, "")}
            nesting = await place_nesting(scope, onto)
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
            Held(
                belief,
                _read_as_kept(relations[relation_id]),
                start,
                end,
                kept[perspective_id],
                waiting_guess(_relation(relations[relation_id])),
            )
            for (perspective_id, relation_id), history in histories.items()
            for belief, start, end in spans(history)
        ]
        # Every belief in the history is offered: the spans decide which ones overlap in time.
        return candidates(onto, held, types, unique, nesting)


def _read_as_kept(row: Mapping[str, object]) -> Relation:
    """A connection as the rules should see it: a waiting guess connection is compared as if it
    were kept, since the question is whether two things one character believes clash."""
    from dmbot.memory.store import _relation

    relation = _relation(dict(row))
    return replace(relation, status=CONFIRMED) if waiting_guess(relation) else relation


def _is_unique(row: Mapping[str, object]) -> bool:
    return Entity(
        str(row["id"]), str(row["type"]), str(row["name"]), "", str(row["status"]), None, "", 0,
        unique_item=row["unique_item"] if isinstance(row["unique_item"], bool) else None,
    ).is_unique  # fmt: skip
