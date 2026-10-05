"""Rule checks run on every new relationship (docs/PLAN.md, "Campaign memory rules", 5).

Pure functions, no database. A failed check never blocks or changes the fact: it
becomes a flag for review, because only the DM decides what's true in their story.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from dmbot.memory.models import REJECTED, Relation
from dmbot.memory.ontology import Ontology, PredicateTerm

WRONG_SUBJECT, WRONG_OBJECT = "wrong_subject", "wrong_object"
TOO_MANY, CONTRADICTION = "too_many", "contradiction"
FLAG_KINDS = (WRONG_SUBJECT, WRONG_OBJECT, TOO_MANY, CONTRADICTION)


@dataclass(frozen=True, slots=True)
class Problem:
    kind: str
    other_id: str | None = None  # the relationship it clashes with


def _overlap(lo1: int | None, hi1: int | None, lo2: int | None, hi2: int | None) -> bool:
    """Do two half-open spans [lo, hi) overlap? None means open-ended."""
    starts_before_other_ends = hi2 is None or lo1 is None or lo1 < hi2
    other_starts_before_this_ends = hi1 is None or lo2 is None or lo2 < hi1
    return starts_before_other_ends and other_starts_before_this_ends


def same_time(a: Relation, b: Relation) -> bool:
    """Could both facts hold at once? By session now; by game time too once both have
    one (TimeBot, Phase 4). Unknown times count as overlapping, to be safe."""
    if not _overlap(a.from_session_at, a.to_session_at, b.from_session_at, b.to_session_at):
        return False
    have_game_time = None not in (a.from_game_time, b.from_game_time)
    return not have_game_time or _overlap(
        a.from_game_time, a.to_game_time, b.from_game_time, b.to_game_time
    )


def same_pair(a: Relation, b: Relation) -> bool:
    return {a.subject_id, a.object_id} == {b.subject_id, b.object_id}


def conflicts(onto: Ontology, p: PredicateTerm, other_key: str) -> bool:
    other = onto.predicates.get(other_key)
    return other_key in p.conflicts_with or (other is not None and p.key in other.conflicts_with)


def ordered(p: PredicateTerm, subject_id: str, object_id: str) -> tuple[str, str]:
    """Two-way relationships are stored once, smaller ID first, so "A ally B" and
    "B ally A" are the same fact (inverse consistency by construction)."""
    if p.symmetric and object_id < subject_id:
        return object_id, subject_id
    return subject_id, object_id


def duplicate_of(new: Relation, existing: Iterable[Relation]) -> Relation | None:
    """An existing live fact saying the same thing at the same time."""
    for old in existing:
        if (
            old.status != REJECTED
            and old.predicate == new.predicate
            and (old.subject_id, old.object_id) == (new.subject_id, new.object_id)
            and old.detail.casefold() == new.detail.casefold()
            and same_time(old, new)
        ):
            return old
    return None


def check_relation(
    onto: Ontology,
    new: Relation,
    subject_type: str,
    object_type: str,
    existing: Iterable[Relation],
) -> list[Problem]:
    """Problems with `new`, given the live facts already stored about its subject and
    object. Never raises for a rule failure: problems are returned to be flagged."""
    p = onto.predicates[new.predicate]
    problems: list[Problem] = []
    if not onto.is_any(subject_type, p.subject_types):
        problems.append(Problem(WRONG_SUBJECT))
    if not onto.is_any(object_type, p.object_types):
        problems.append(Problem(WRONG_OBJECT))
    live = [r for r in existing if r.status != REJECTED and r.id != new.id and same_time(r, new)]
    if p.max_per_subject is not None:
        others = [
            r
            for r in live
            if r.predicate == p.key
            and r.subject_id == new.subject_id
            and r.object_id != new.object_id
        ]
        if len(others) >= p.max_per_subject:
            problems += [Problem(TOO_MANY, r.id) for r in others]
    problems += [
        Problem(CONTRADICTION, r.id)
        for r in live
        if same_pair(r, new) and conflicts(onto, p, r.predicate)
    ]
    return problems
