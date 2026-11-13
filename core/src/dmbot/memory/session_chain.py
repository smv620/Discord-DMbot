"""Where the story is, through a session's own words (#4173 slice 4; Supervisor's ruling on #4173,
2026-11-13 04:14): the open places, widest first, carried from session to session, one chain for
the party and one for each split group. Each place named that has no parent gets the nearest bigger
place open on its chain as its assumed parent (slice 3's mark). Pure: no Discord, no database.

The rulings this keeps:
- Only the DM's lines move a chain. A DM line opens a place it names (closing every open place its
  size or smaller). Players name far-off places all the time ("my cousin in Waterdeep"), so a
  player's line never moves a chain; a place a player names still gets an assumed parent from the
  chain as it stands.
- Whether a DM line leaves a place, heads for one, travels, or names one in passing is the AI's
  call, never a keyword (the owner's change, 2026-11-13; `scene_moves`): each line carries the
  moves found for it. "Left X" closes X and what is inside it, only when X is open on that chain;
  "heading for Y" closes everything open at Y's size or smaller, and Y opens only when the DM names
  it as reached; "travel" closes every settlement and smaller; "none" (a place named in passing,
  a memory, a letter left somewhere) reads the place and moves nothing. A move naming a place the
  campaign doesn't know is ignored: the AI's word never makes a place or opens a level by itself.
- "X in Y" with Y the bigger place, in one breath: Y is taken first, so X sits inside it.
- A sentence naming three or more places is a list (slice 3's rule): it opens nothing, closes
  nothing and gives no parent.
- A place gets at most one guess (`stated` holds the places with a parent already).
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from dmbot.memory import place_levels, route_words
from dmbot.memory.scene_moves import ARRIVE, HEADING, LEAVE, NONE, TRAVEL, Move
from dmbot.memory.story_chain import LIST_PLACES, Place

MAIN = "main"  # the party's chain, when it is together
# "X in Y" with Y the bigger place, in one breath: Y opens first, so X sits inside it (the
# reviewer on slice 4: "rooms at the Three Flags Sailing in Targos")
_INSIDE = re.compile(r"^\s*,?\s*(?:in|at|of|inside|within|near)\s+(?:the\s+)?$", re.IGNORECASE)
_SENTENCE_END = re.compile(r"[.!?]+(?=\s)|\n")
_ON_THE_ROAD = place_levels.RANK["settlement"]


@dataclass(frozen=True, slots=True)
class Line:
    """One line of the session, in order: its words, whether the DM said it, and the chain it
    belongs to (`MAIN`, a split group's key, or None when nobody can tell: then it is skipped)."""

    text: str
    dm: bool
    chain: str | None
    moves: tuple[Move, ...] = ()  # the AI's scene moves on this line (DM lines only)


@dataclass(frozen=True, slots=True)
class Leg:
    """A journey the party made (#4173 slice 6d part 3): from one settlement to another, on the
    party's own chain, and the way the DM's words said ("" when they didn't say)."""

    start: str
    end: str
    means: str


@dataclass(slots=True)
class Walked:
    """What a walk leaves: each chain's open places (widest first), the assumed parents found and
    the party's journeys."""

    chains: dict[str, list[str]]
    pairs: list[tuple[str, str]] = field(default_factory=list)
    legs: list[Leg] = field(default_factory=list)
    town: str | None = None  # the town the party was last in, for the walk after a Rejoin


def _mentions(text: str, found: Sequence[Place]) -> list[tuple[int, int, Place]]:
    """Every place named in the line, in order; a spelling inside a longer one is not a second
    mention ("Bryn" inside "Bryn Shander")."""
    spots: list[tuple[int, int, Place]] = []
    for p in found:
        # (each spelling on its own: one regex of them all would stop at the first that fits)
        for word in {s.strip() for s in p.spellings if s.strip()}:
            pattern = re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)", re.IGNORECASE)
            spots += [(m.start(), m.end(), p) for m in pattern.finditer(text)]
    # (overlapping spellings: the longest wins, "the Three Flags" inside "Three Flags Sailing"
    # counts once, as the whole name)
    spots.sort(key=lambda s: (-(s[1] - s[0]), s[0]))
    kept: list[tuple[int, int, Place]] = []
    for spot in spots:
        if any(spot[0] < k[1] and k[0] < spot[1] for k in kept):
            continue
        kept.append(spot)
    return sorted(kept, key=lambda s: s[0])


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
    routes: Sequence[Place] = (),
    last_town: str | None = None,
) -> Walked:
    """Walk a session's lines in order from the chains as they were (see the module's rules).
    `routes`: the campaign's named route places ("the Eastway"); being on one closes the town level
    and below (#4173 slice 6d, the movement rule), as travel does. `last_town`: the town the party
    was last in, from the walk before a Rejoin (it is not kept from one session to the next: a
    session that starts on the road writes no journey to its first town)."""
    usable = [p for p in found if p.level in place_levels.RANK]
    ranks = {p.entity_id: place_levels.RANK[p.level] for p in usable}
    route_ids = {r.entity_id for r in routes}
    out = Walked({k: [e for e in v if e in ranks] for k, v in chains.items()})
    given = set(stated)
    # The party's journeys (Supervisor's ruling on part 3, 2026-11-13): only on the party's own
    # chain while it is together, from the town it was last in (kept when travel closed it, and
    # passed on from the walk before a Rejoin) to the next town the AI says the DM brings it to
    # (an explicit "arrive": a town named in passing, with no move, is never a journey). The way is
    # read only from the DM's lines once the party has left, and the arrival line itself.
    town = last_town or next(
        (e for e in out.chains.get(MAIN, []) if ranks[e] == _ON_THE_ROAD), None
    )
    way_words: list[str] = []
    for line in lines:
        if line.chain is None:
            continue  # (nobody can tell whose story it is: skipped, never guessed)
        chain = out.chains.setdefault(line.chain, list(out.chains.get(MAIN, [])))
        # (roads and towns in one pass, so the longest name wins: "the Neverwinter Road" is a
        # road, never a mention of Neverwinter)
        both = _mentions(line.text, [*usable, *routes])
        spots = [s for s in both if s[2].entity_id not in route_ids]
        on_roads = [s for s in both if s[2].entity_id in route_ids]
        listed = _listed(line.text, both)
        # (the order a place is taken in: its own start, or just before the place it is said to
        # be inside of, when that place is smaller and the words between are "in/at/of")
        order = {s[0]: float(s[0]) for s in spots}
        for a, b in itertools.pairwise(spots):
            if ranks[b[2].entity_id] < ranks[a[2].entity_id] and _INSIDE.match(
                line.text[a[1] : b[0]]
            ):
                order[b[0]] = order[a[0]] - 0.5
        events = [(order[s[0]], s[2], s[0]) for s in spots]
        # (each place the AI named on this line, by any of its spellings; one it can't find is
        # ignored: the AI's word never makes a place)
        said = {sp.casefold(): p for p in usable for sp in p.spellings}
        moved: dict[str, str] = {}
        travel = False
        for m in line.moves if line.dm else ():
            if m.kind == TRAVEL:
                travel = True
            elif m.place.casefold() in said:
                moved[said[m.place.casefold()].entity_id] = m.kind
        # being put on a named road (the AI's arrive, heading or travel for it: never a road only
        # named, or left) closes the town level, as travel does
        on_route = line.dm and any(
            at not in listed and moved_kind(line, r) in (ARRIVE, HEADING, TRAVEL)
            for at, _, r in on_roads
        )
        if travel or on_route:
            chain[:] = [e for e in chain if ranks[e] < _ON_THE_ROAD]
        named = {s[2].entity_id for s in spots}
        for entity_id, kind in moved.items():  # (a move whose place the line doesn't spell out)
            if entity_id not in named:
                _move(chain, ranks, entity_id, kind)
        away = line.chain == MAIN and line.dm and town is not None and town not in chain
        if away:
            way_words.append(line.text)
        for _, p, at in sorted(events, key=lambda e: e[0]):
            if at in listed:
                continue  # (a list says nothing about what is inside what)
            kind = moved.get(p.entity_id, ARRIVE)
            if kind in (LEAVE, HEADING):
                _move(chain, ranks, p.entity_id, kind)
                continue
            rank = ranks[p.entity_id]
            parent = _parent(chain, ranks, rank)
            if parent is not None and p.entity_id not in given and parent != p.entity_id:
                out.pairs.append((p.entity_id, parent))
                given.add(p.entity_id)
            if line.dm and kind != NONE:  # (named in passing: read, and it moves nothing)
                chain[:] = [e for e in chain if ranks[e] < rank]
                chain.append(p.entity_id)
                if line.chain == MAIN and rank == _ON_THE_ROAD:
                    arrived = moved.get(p.entity_id) == ARRIVE  # (the AI said so, not a default)
                    if arrived and town is not None and town != p.entity_id:
                        words = way_words if away else [*way_words, line.text]
                        out.legs.append(Leg(town, p.entity_id, _way(words)))
                    if arrived or town is None:
                        town, way_words = p.entity_id, []
    out.town = town
    return out


def moved_kind(line: Line, place: Place) -> str:
    """The AI's move for this place on this line, by any of its spellings, or "" for none."""
    spellings = {sp.casefold() for sp in place.spellings}
    return next((m.kind for m in line.moves if m.place.casefold() in spellings), "")


def _way(words: Sequence[str]) -> str:
    """The one way the DM's words on the journey say, or "" when they say none or more than one
    (Supervisor: never a guessed means; two ways in one stretch is not one way)."""
    text = " ".join(words)
    said = {m for m in route_words.MEANS if route_words.said_in(text, m)}
    return said.pop() if len(said) == 1 else ""


def _move(chain: list[str], ranks: Mapping[str, int], entity_id: str, kind: str) -> None:
    """A leaving or a heading-for, on one chain: "left X" only when X is open there."""
    if kind == LEAVE and entity_id in chain:
        del chain[chain.index(entity_id) :]
    elif kind == HEADING:
        rank = ranks[entity_id]
        chain[:] = [e for e in chain if ranks[e] < rank]  # (between places now)


def rejoined(groups: Iterable[Sequence[str]]) -> list[str]:
    """The party's chain at Rejoin: only the places every group still has open, widest first (a
    group that went elsewhere never drags the party with it)."""
    lists = [list(g) for g in groups]
    if not lists:
        return []
    return [e for e in lists[0] if all(e in g for g in lists[1:])]
