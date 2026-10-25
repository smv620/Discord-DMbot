"""What the party has wrong (#2661; docs/STORY_MEMORY.md 9a, "The after-session report"): where the
party's or a character's belief differs from The truth, as candidates for PlotBot to sort into
disguised, misunderstood and simply false. Finding is code; the sort is the AI's, and neither
changes a belief (the DM decides).

A belief counts as a difference when a point of view that is not The truth holds it NOW (the
newest belief, at 0.5 or more and not a denial) and The truth either
- **denies** that very connection (its belief there is 0.0, False Evidence): the party believes
  what the DM's view says is not so; or
- holds a connection **between the same two names that clashes with it** under the ontology and
  overlaps it in story time: the party believes "Aldric is an ally of the Ashen Crown" where the
  DM's view has him as its enemy.
Not a difference: a guess about something The truth neither holds nor denies (nobody knows yet),
or The truth's own doubt (a belief below even odds is not a denial), or a belief The truth shares.

Ids, numbers and a status only: no name, no words. Each (point of view, connection) is reported
once.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dmbot.memory.checks import conflicts, same_pair, same_time
from dmbot.memory.contradictions import Held, asserts, load
from dmbot.memory.models import FALSE_EVIDENCE
from dmbot.memory.ontology import Ontology

if TYPE_CHECKING:
    from dmbot.memory.store import MemoryStore

TRUTH = "truth"
DENIED, CLASHES = "denied", "clashes"


@dataclass(frozen=True, slots=True)
class Difference:
    """One belief that differs from The truth. Ids only."""

    perspective_id: str
    view_kind: str  # party, pc or npc
    relation_id: str  # the connection the point of view believes
    truth_relation_id: str | None  # the connection The truth holds instead (None when it denies)
    kind: str  # DENIED or CLASHES
    times_unknown: bool  # no game time on one of the two: "The game times aren't known"
    involves_guess: bool  # what is believed is a waiting guess nobody has kept


def _denies(truth: Held) -> bool:
    b = truth.belief
    return b.confidence == 0.0 or b.status == FALSE_EVIDENCE


def differences(onto: Ontology, held: Sequence[Held]) -> list[Difference]:
    """The differences among these beliefs. Pure."""
    now = [h for h in held if h.held_until is None]  # what each point of view holds now
    truth_now = {h.relation.id: h for h in now if h.kind == TRUTH}
    truth_holds = [h for h in truth_now.values() if asserts(h.belief)]
    by_pair: dict[frozenset[str], list[Held]] = {}
    for t in truth_holds:
        by_pair.setdefault(frozenset((t.relation.subject_id, t.relation.object_id)), []).append(t)
    out: list[Difference] = []
    seen: set[tuple[str, str]] = set()
    for mine in now:
        if mine.kind == TRUTH or not asserts(mine.belief):
            continue
        key = (mine.belief.perspective_id, mine.relation.id)
        if key in seen:
            continue
        theirs = truth_now.get(mine.relation.id)
        if theirs is not None and asserts(theirs.belief):
            continue  # the DM's view holds it too
        pair = frozenset((mine.relation.subject_id, mine.relation.object_id))
        term = onto.predicates.get(mine.relation.predicate)
        clash = None
        if term is not None:
            clash = next(
                (
                    t
                    for t in by_pair.get(pair, ())
                    if t.relation.id != mine.relation.id
                    and same_pair(t.relation, mine.relation)
                    and conflicts(onto, term, t.relation.predicate)
                    and same_time(t.relation, mine.relation)
                ),
                None,
            )
        if clash is None and not (theirs is not None and _denies(theirs)):
            continue  # not held, not denied, nothing clashing: the DM's view has no word on it
        seen.add(key)
        other = clash.relation if clash is not None else None
        unknown = mine.relation.from_game_time is None or (
            other is not None and other.from_game_time is None
        )
        out.append(
            Difference(
                mine.belief.perspective_id, mine.kind, mine.relation.id,
                other.id if other is not None else None,
                CLASHES if clash is not None else DENIED,
                unknown, mine.guess,
            )
        )  # fmt: skip
    return sorted(out, key=lambda d: (d.times_unknown, d.kind, d.relation_id, d.perspective_id))


class Differences:
    """Reads one campaign's beliefs and finds the differences (no writing)."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def find(self, guild_id: int, campaign_id: str) -> list[Difference]:
        got = await load(self._store, guild_id, campaign_id)
        return differences(got.onto, got.held)
