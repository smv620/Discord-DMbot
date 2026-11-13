"""Where the story is, through a session's own words (#4173 slice 4; Supervisor's ruling on #4173,
2026-11-13 04:14): the open places, widest first, carried from session to session, one chain for
the party and one for each split group. Each place named that has no parent gets the nearest bigger
place open on its chain as its assumed parent (slice 3's mark). Pure: no Discord, no database.

The rulings this keeps:
- Only the DM's lines move a chain. A DM line opens a place (closing every open place its size or
  smaller), or closes with a leaving word. Players name far-off places all the time ("my cousin in
  Waterdeep"), so a player's line never moves a chain; a place a player names still gets an assumed
  parent from the chain as it stands.
- A leaving word closes a place only when the place it names is open on that chain: "left Targos"
  closes Targos and what is inside it; "left the letter in Bryn Shander" leaves no place.
- "Left for Y", "set out for Y", "heading to Y" are a heading-for: everything open at Y's size or
  smaller closes (they are between places), and Y opens only when the DM names it again.
- "On the road", "days of travel", "through the wilds" close every settlement and smaller.
- A sentence naming three or more places is a list (slice 3's rule): it opens nothing, closes
  nothing and gives no parent.
- A place gets at most one guess (`stated` holds the places with a parent already).
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from dmbot.memory import place_levels
from dmbot.memory.story_chain import LIST_PLACES, Place

MAIN = "main"  # the party's chain, when it is together
# Words just before a place that make the line a heading-for it ("they left for Targos"); read
# first, so "left for" is never a leaving
_HEADING = re.compile(
    r"\b(?:left|leaves?|leaving|set(?:s|ting)?\s+(?:out|off)|head(?:s|ed|ing)?|bound"
    r"|travel(?:s|led|ed|ling|ing)?|journey(?:s|ed|ing)?|march(?:es|ed|ing)?|r(?:ide|ides|ode|iding)"
    r"|on\s+(?:the|their|our|his|her)\s+way|mak(?:e|es|ing)\s+(?:their|our|his|her)\s+way)"
    r"\s+(?:back\s+)?(?:for|to|towards?)\s+(?:the\s+)?$",
    re.IGNORECASE,
)
# Words just before a place that leave it ("they left Targos", "departed from the inn")
_LEAVING = re.compile(
    r"\b(?:left|leaves?|leaving|depart(?:s|ed|ing)?(?:\s+from)?|out\s+of|fled|flee(?:s|ing)?)"
    r"\s+(?:the\s+)?$",
    re.IGNORECASE,
)
# A leaving verb earlier in the sentence that isn't aimed at the place ("She left the letter in
# Bryn Shander"): the place is named in passing, read against the chain, and moves nothing
# (Supervisor's condition on #4173: that line closes nothing)
_LEAVE_VERB = re.compile(r"\b(?:left|leaves?|leaving|depart(?:s|ed|ing)?|fled)\b", re.IGNORECASE)
# Travel between places: closes every settlement and smaller
_TRAVEL = re.compile(
    r"\b(?:on\s+the\s+(?:road|trail)|days?\s+of\s+travel|through\s+the\s+wild(?:s|erness)?)\b",
    re.IGNORECASE,
)
_BEFORE = 60  # how far back from a place the heading and leaving words are looked for
_SENTENCE_END = re.compile(r"[.!?]+(?=\s)|\n")
_ON_THE_ROAD = place_levels.RANK["settlement"]


@dataclass(frozen=True, slots=True)
class Line:
    """One line of the session, in order: its words, whether the DM said it, and the chain it
    belongs to (`MAIN`, a split group's key, or None when nobody can tell: then it is skipped)."""

    text: str
    dm: bool
    chain: str | None


@dataclass(slots=True)
class Walked:
    """What a walk leaves: each chain's open places (widest first) and the assumed parents found."""

    chains: dict[str, list[str]]
    pairs: list[tuple[str, str]] = field(default_factory=list)


def _mentions(text: str, found: Sequence[Place]) -> list[tuple[int, int, Place]]:
    """Every place named in the line, in order; a spelling inside a longer one is not a second
    mention ("Bryn" inside "Bryn Shander")."""
    spots: list[tuple[int, int, Place]] = []
    for p in found:
        words = sorted({s.strip() for s in p.spellings if s.strip()}, key=len, reverse=True)
        if not words:
            continue
        pattern = re.compile(
            r"(?<!\w)(?:" + "|".join(re.escape(w) for w in words) + r")(?!\w)", re.IGNORECASE
        )
        spots += [(m.start(), m.end(), p) for m in pattern.finditer(text)]
    spots.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    kept: list[tuple[int, int, Place]] = []
    for spot in spots:
        if kept and spot[0] < kept[-1][1]:
            continue  # (inside the one before)
        kept.append(spot)
    return kept


def _listed(text: str, spots: Sequence[tuple[int, int, Place]]) -> set[int]:
    """The starts of the mentions in a sentence that names three or more places: a list."""
    bounds = [0, *(m.end() for m in _SENTENCE_END.finditer(text)), len(text) + 1]
    out: set[int] = set()
    for a, b in itertools.pairwise(bounds):
        inside = [s for s in spots if a <= s[0] < b]
        if len({s[2].entity_id for s in inside}) >= LIST_PLACES:
            out |= {s[0] for s in inside}
    return out


def _parent(chain: Sequence[str], ranks: Mapping[str, int], rank: int) -> str | None:
    """The nearest open place bigger than `rank` (the chain is widest first)."""
    bigger = [e for e in chain if ranks.get(e, rank) < rank]
    return bigger[-1] if bigger else None


def walk(
    lines: Iterable[Line],
    found: Sequence[Place],
    chains: Mapping[str, Sequence[str]],
    stated: Collection[str],
) -> Walked:
    """Walk a session's lines in order from the chains as they were (see the module's rules)."""
    usable = [p for p in found if p.level in place_levels.RANK]
    ranks = {p.entity_id: place_levels.RANK[p.level] for p in usable}
    out = Walked({k: [e for e in v if e in ranks] for k, v in chains.items()})
    given = set(stated)
    for line in lines:
        if line.chain is None:
            continue  # (nobody can tell whose story it is: skipped, never guessed)
        chain = out.chains.setdefault(line.chain, list(out.chains.get(MAIN, [])))
        spots = _mentions(line.text, usable)
        listed = _listed(line.text, spots)
        events: list[tuple[int, str, Place | None]] = [(s[0], "place", s[2]) for s in spots]
        if line.dm:
            events += [(m.start(), "travel", None) for m in _TRAVEL.finditer(line.text)]
        for at, kind, p in sorted(events, key=lambda e: e[0]):
            if kind == "travel":
                chain[:] = [e for e in chain if ranks[e] < _ON_THE_ROAD]
                continue
            assert p is not None
            if at in listed:
                continue  # (a list says nothing about what is inside what)
            rank = ranks[p.entity_id]
            before = line.text[max(0, at - _BEFORE) : at]
            if line.dm and _HEADING.search(before):
                chain[:] = [e for e in chain if ranks[e] < rank]  # (between places now)
                continue
            if line.dm and _LEAVING.search(before):
                if p.entity_id in chain:  # (only a place open on this chain is left)
                    del chain[chain.index(p.entity_id) :]
                continue
            parent = _parent(chain, ranks, rank)
            if parent is not None and p.entity_id not in given and parent != p.entity_id:
                out.pairs.append((p.entity_id, parent))
                given.add(p.entity_id)
            in_passing = bool(_LEAVE_VERB.search(_SENTENCE_END.split(before)[-1]))
            if line.dm and not in_passing:
                chain[:] = [e for e in chain if ranks[e] < rank]
                chain.append(p.entity_id)
    return out


def rejoined(groups: Iterable[Sequence[str]]) -> list[str]:
    """The party's chain at Rejoin: only the places every group still has open, widest first (a
    group that went elsewhere never drags the party with it)."""
    lists = [list(g) for g in groups]
    if not lists:
        return []
    return [e for e in lists[0] if all(e in g for g in lists[1:])]
