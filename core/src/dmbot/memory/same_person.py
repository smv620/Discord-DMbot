"""Is this unnamed entry ("the guard at the gate") the same person as a named one ("Ken the guard")?
(#3037, slice 1). Pure: no database, no AI.

It only ever proposes. Each possible pair gets the evidence for it, in fixed words the DM reads next
to the question, and the pairs are ranked by that evidence. A weak match is never an offer
(#3064): the text must say so outright, or there must be at least two pieces of evidence (two shared
words, two shared connections, or one of each); one word or one connection alone is not enough, and
a connection many characters share (everyone who lives in the town) is not evidence at all. When the
best matches tie past the limit the tied group is dropped: "can't tell" is honest, picking by id is
a guess. Nothing here decides, joins or guesses. The three kinds of evidence are the ones the
owner named (2026-10-30): the text says so outright, the two share connections, and their
descriptions share a meaningful word ("guard", "gate"). A pair the DM already said no to is never
offered again. Who knows what is not touched here: the merge that follows a yes moves rows, and
never widens what anyone knew (#2995).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.memory import unnamed as unnamed_mod
from dmbot.memory.models import name_key
from dmbot.memory.ontology import DISGUISED_AS

STATED = "the text says so"
CONNECTIONS = "they share connections"
WORDS = "their descriptions share words"
SAME_WORDS = "they are described the same way"  # two unnamed people (#3447)

MAX_OFFERS_PER_ENTRY = 3  # the best few for one unnamed entry: a long list is a guess
MIN_EVIDENCE = 2  # shared words plus shared connections, unless the text said so outright
STATED_POINTS = 100
CONNECTION_POINTS = 10
WORD_POINTS = 5
MAX_CONNECTION_POINTS = 40  # (a crowded entry does not win on connections alone)
MAX_WORD_POINTS = 20

_PLAIN = frozenset(
    {
        "the", "a", "an", "of", "at", "in", "on", "to", "and", "with", "who", "that", "name",
        "unknown", "unnamed", "his", "her", "their", "its", "this", "these", "those", "for", "from",
        "was", "were", "had", "has", "have", "been", "being", "are", "not", "there", "here",
        "she", "him", "they", "them", "one", "some", "any", "but", "when", "where", "which",
        "into", "over", "about", "than", "then", "very", "also", "would", "could", "will",
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
    # Two unnamed people (#3447): `unnamed_id` is the older and the one a yes keeps, `named_id` the
    # newer. The question reads both as people the story never named.
    both_unnamed: bool = False


def _fold(word: str) -> str:
    """One plural folded to its singular ("guards" is "guard"), so "gate guard" and "guards the
    gate" read alike. Crude on purpose: both sides are folded the same way."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def meaningful(text: str) -> frozenset[str]:
    """The words that can tell two descriptions apart: three letters or more, no article or
    filler ("the", "was", "unnamed", the old "name unknown"), plurals folded, and never the
    "(first seen …)" that only keeps a name unique (#3736)."""
    words = (
        w
        for w in _WORD.findall(name_key(unnamed_mod.without_tag(text)))
        if len(w) >= 3 and w not in _PLAIN
    )
    return frozenset(_fold(w) for w in words)


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
    holders: dict[Link, int] = {}  # how many named entries hold each connection
    for n in named:
        for link in n.links:
            holders[link] = holders.get(link, 0) + 1
    # each named entry's words once, not once per pair (100 x 3,000 entries took 14 s otherwise)
    words = {n.id: meaningful(n.words) for n in named}
    for u in unnamed:
        mine = meaningful(u.what)
        ranked: list[Offer] = []
        for n in named:
            if n.kind != u.kind or n.id == u.id or (u.id, n.id) in declined:
                continue
            said = (u.id, n.id) in stated
            # (a connection many hold says nothing about this one: it is not evidence)
            shared = sum(1 for link in u.links & n.links if holders[link] <= MAX_OFFERS_PER_ENTRY)
            overlap = len(mine & words[n.id])
            if not said and shared + overlap < MIN_EVIDENCE:
                continue
            given: list[tuple[int, str]] = []
            if said:
                given.append((STATED_POINTS, STATED))
            if shared:
                given.append((min(shared * CONNECTION_POINTS, MAX_CONNECTION_POINTS), CONNECTIONS))
            if overlap:
                given.append((min(overlap * WORD_POINTS, MAX_WORD_POINTS), WORDS))
            given.sort(key=lambda g: -g[0])  # strongest first, as promised
            ranked.append(Offer(u.id, n.id, sum(g[0] for g in given), tuple(g[1] for g in given)))
        if any(STATED in o.reasons for o in ranked):
            ranked = [o for o in ranked if STATED in o.reasons]  # (the text settled who: only that)
        ranked.sort(key=lambda o: (-o.score, o.named_id))
        if len(ranked) > MAX_OFFERS_PER_ENTRY:
            edge = ranked[MAX_OFFERS_PER_ENTRY - 1].score
            if ranked[MAX_OFFERS_PER_ENTRY].score == edge:  # a tie past the limit: can't tell
                ranked = [o for o in ranked if o.score > edge]
        found.extend(ranked[:MAX_OFFERS_PER_ENTRY])
    found.sort(key=lambda o: (-o.score, o.unnamed_id, o.named_id))
    return found


def keeper_order(row: dict[str, Any], ties: Mapping[str, int]) -> tuple[int, int, str]:
    """Which of two people described the same way a Yes keeps (#3457): the older first, then the
    one with more confirmed connections, then the smaller id, so two made in the same second still
    have one answer and the same pair is never offered the other way round."""
    return (int(row.get("created_at") or 0), -ties.get(str(row["id"]), 0), str(row["id"]))


def unnamed_pairs(
    entries: Sequence[dict[str, Any]],
    declined: Collection[tuple[str, str]] = (),
    ties: Mapping[str, int] | None = None,
) -> list[Offer]:
    """Two unnamed people the story described the same way ("the woman", kept in two sessions),
    each offered once with the older (#3447). Only people with no one they hang off ("Ken's
    sister" is Ken's) and the same relationship word; the newer ones pair with the oldest, so a
    yes shortens the list. A pair the DM said "not the same" to is never offered again."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for e in entries:
        if (
            e["unnamed"]
            and e["type"] == "character"  # (the only kind a join accepts)
            and e.get("unnamed_anchor") is None
            and e["status"] in ("proposed", "confirmed")
        ):
            key = (
                str(e["type"]),
                unnamed_mod.same_person_key(e["unnamed_what"], e["unnamed_role"]),
            )
            groups.setdefault(key, []).append(e)
    found: list[Offer] = []
    for group in groups.values():
        group.sort(key=lambda e: keeper_order(e, ties or {}))
        for i, later in enumerate(group[1:], start=1):
            # paired with the oldest one it has not been ruled apart from, so a "not the same" to
            # one pair never hides the others
            for earlier in group[:i]:
                pair = (str(earlier["id"]), str(later["id"]))
                if pair not in declined and pair[::-1] not in declined:
                    found.append(Offer(pair[0], pair[1], 0, (SAME_WORDS,), both_unnamed=True))
                    break
    found.sort(key=lambda o: (o.unnamed_id, o.named_id))
    return found


def from_rows(
    entries: Sequence[dict[str, Any]],
    aliases: Sequence[dict[str, Any]],
    relations: Sequence[dict[str, Any]],
    apart: Collection[tuple[str, str]],
    disguises: Collection[tuple[str, str]] = (),
) -> list[Offer]:
    """The offers for one campaign's rows (the store reads them, scoped to the campaign). An
    unnamed entry is the left side, a named confirmed one of the same kind the right (a player's
    character may be either: "the hooded stranger" can turn out to be one); a declined
    pair is kept as `name_not_same` keeps it, in id order, and turned back into (unnamed, named).
    `disguises`: (disguised, copied) pairs of a `disguised_as` connection at any status but
    rejected: a disguise is two people, so such a pair is never offered, in either direction.
    `stated` is empty until a reading records "the text says it is": that is a later slice."""
    # (the store hands over entries that are not players: a link through a person would let a
    # shared player count as evidence for two different characters, so it is left out below)
    live = {str(e["id"]) for e in entries}
    spoken: dict[str, list[str]] = {}
    for a in aliases:
        if a["status"] == "confirmed":  # only names the DM said yes to count as evidence
            spoken.setdefault(str(a["entity_id"]), []).append(str(a["text"]))
    links: dict[str, set[Link]] = {}
    disguised: set[tuple[str, str]] = {(a, b) for x, y in disguises for a, b in ((x, y), (y, x))}
    for r in relations:
        subject, other = str(r["subject_id"]), str(r["object_id"])
        if r["predicate"] == DISGUISED_AS and r.get("status") != "rejected":
            disguised |= {(subject, other), (other, subject)}  # (a confirmed one, in the rows)
        if subject not in live or other not in live:
            continue  # a connection to a rejected or merged entry says nothing
        links.setdefault(subject, set()).add((str(r["predicate"]), other, True))
        links.setdefault(other, set()).add((str(r["predicate"]), subject, False))
    unnamed: list[Unnamed] = []
    named: list[Named] = []
    for e in entries:
        eid = str(e["id"])
        mine = frozenset(links.get(eid, ()))
        if e["unnamed"]:
            what = " ".join(str(x) for x in (e["unnamed_what"], e["unnamed_role"]) if x)
            unnamed.append(Unnamed(eid, str(e["type"]), what, mine))
        elif e["status"] == "confirmed":
            words = " ".join([str(e["name"]), *spoken.get(eid, []), str(e["description"] or "")])
            named.append(Named(eid, str(e["type"]), words, mine))
    # from the stored pairs, not by looping over every (unnamed, named) pair
    left = {u.id for u in unnamed}
    right = {n.id for n in named}
    declined = {(a, b) for a, b in apart if a in left and b in right} | {
        (b, a) for a, b in apart if b in left and a in right
    }
    declined |= {(a, b) for a, b in disguised if a in left and b in right}
    made = offers(unnamed, named, declined=declined)
    # two unnamed people described the same way (#3447); a disguise or a "not the same" keeps a pair
    # apart, in either order
    apart_both = set(apart) | disguised
    counts = {entry: len(found) for entry, found in links.items()}
    return [*made, *unnamed_pairs(entries, apart_both, counts)]
