"""Is this unnamed entry ("the guard at the gate") the same person as a named one ("Ken the guard")?
(#3037, slice 1). Pure: no database, no AI.

It only ever proposes. Each possible pair gets the evidence for it, in fixed words the DM reads next
to the question, and the pairs are ranked by that evidence; a pair with no evidence is not offered
at all, and nothing here decides, joins or guesses. The three kinds of evidence are the ones the
owner named (2026-10-30): the text says so outright, the two share connections, and their
descriptions share a meaningful word ("guard", "gate"). A pair the DM already said no to is never
offered again. Who knows what is not touched here: the merge that follows a yes moves rows, and
never widens what anyone knew (#2995).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from dmbot.memory.models import name_key

STATED = "the text says so"
CONNECTIONS = "they share connections"
WORDS = "their descriptions share words"

MAX_OFFERS_PER_ENTRY = 3  # the best few for one unnamed entry: a long list is a guess
STATED_POINTS = 100
CONNECTION_POINTS = 10
WORD_POINTS = 5
MAX_CONNECTION_POINTS = 40  # (a crowded entry does not win on connections alone)
MAX_WORD_POINTS = 20

_PLAIN = frozenset(
    {
        "the", "a", "an", "of", "at", "in", "on", "to", "and", "with", "who", "that", "name",
        "unknown", "his", "her", "their", "its", "this", "these", "those", "for", "from",
    }
)  # fmt: skip
_WORD = re.compile(r"[a-z']+")

# One connection of an entry: (relationship, the entry at the other end, whether the entry is the
# subject). The same relationship to the same entry in the same direction is the same connection.
Link = tuple[str, str, bool]


@dataclass(frozen=True, slots=True)
class Unnamed:
    id: str
    kind: str
    what: str  # the story's word for them ("guard at the gate")
    links: frozenset[Link] = frozenset()


@dataclass(frozen=True, slots=True)
class Named:
    id: str
    kind: str
    words: str  # the name, its other names and its description, as one text
    links: frozenset[Link] = frozenset()


@dataclass(frozen=True, slots=True)
class Offer:
    unnamed_id: str
    named_id: str
    score: int
    reasons: tuple[str, ...]  # fixed words, strongest first


def meaningful(text: str) -> frozenset[str]:
    """The words that can tell two descriptions apart: three letters or more, no article or
    filler ("the", "name unknown")."""
    return frozenset(w for w in _WORD.findall(name_key(text)) if len(w) >= 3 and w not in _PLAIN)


def offers(
    unnamed: Sequence[Unnamed],
    named: Sequence[Named],
    *,
    stated: Collection[tuple[str, str]] = (),
    declined: Collection[tuple[str, str]] = (),
) -> list[Offer]:
    """The pairs worth asking the DM about, best evidence first. `stated`: (unnamed id, named id)
    pairs a text said outright. `declined`: pairs the DM turned down. The same input always gives
    the same order (score, then ids)."""
    found: list[Offer] = []
    for u in unnamed:
        mine = meaningful(u.what)
        ranked: list[Offer] = []
        for n in named:
            if n.kind != u.kind or n.id == u.id or (u.id, n.id) in declined:
                continue
            points = 0
            reasons: list[str] = []
            if (u.id, n.id) in stated:
                points += STATED_POINTS
                reasons.append(STATED)
            shared = len(u.links & n.links)
            if shared:
                points += min(shared * CONNECTION_POINTS, MAX_CONNECTION_POINTS)
                reasons.append(CONNECTIONS)
            overlap = len(mine & meaningful(n.words))
            if overlap:
                points += min(overlap * WORD_POINTS, MAX_WORD_POINTS)
                reasons.append(WORDS)
            if points:
                ranked.append(Offer(u.id, n.id, points, tuple(reasons)))
        if any(STATED in o.reasons for o in ranked):
            ranked = [o for o in ranked if STATED in o.reasons]  # (the text settled who: only that)
        ranked.sort(key=lambda o: (-o.score, o.named_id))
        found.extend(ranked[:MAX_OFFERS_PER_ENTRY])
    found.sort(key=lambda o: (-o.score, o.unnamed_id, o.named_id))
    return found
