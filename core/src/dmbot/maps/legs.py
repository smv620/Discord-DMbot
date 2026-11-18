"""A route taken leg by leg (#4260, MapBot layers slice 10d; design §9 "How far is it?" and "How
long will it take?"; Supervisor's ruling on #4260). Pure: no database, no Discord, no AI. Given the
campaign's kept, open roads (`route_to`), it finds a chain from one place to another when no road
joins them directly, says each leg with its stated length and condition, and adds the lengths up
only when every leg has one in the same unit (Supervisor's ruling on #4240).

Rules kept here (the ruling on #4260):
- **The fewest legs first, at most `MAX_LEGS`.** Two chains with as few legs: the first in a stable
  order (each step tries the next places by name, then id), and the other is named as "another way,
  through X", never picked by length, since lengths may be missing.
- **A closed leg stays on the route** and is said: DMbot never routes around it, as the DM may open
  it.
- **A leg with no length is named,** never guessed; with one, or with mixed units, there is no
  total.
Who may see which legs is the caller's (the DM's view, or only when the party knows every leg).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from dmbot.memory import route_length

MAX_LEGS = 4  # (a longer chain is a journey to plan, not a question to answer at the table)
MAX_PATHS = 5000  # (a guard on a campaign with very many roads: no answer, never a hang)


@dataclass(frozen=True, slots=True)
class Road:
    id: str  # the `route_to` connection's id
    a: str  # one end's entry id
    b: str  # the other end's
    length_words: str = ""  # the stated length in its own words, "" when none
    closed: bool = False  # its condition now is "closed"


@dataclass(frozen=True, slots=True)
class Leg:
    start: str  # entry id, in the route's order
    end: str
    road: Road


@dataclass(frozen=True, slots=True)
class Route:
    legs: tuple[Leg, ...]
    other_via: str | None  # the first stop of another chain with as few legs, if any

    @property
    def stops(self) -> tuple[str, ...]:
        """Every place on the route, start and end included, in order."""
        return (self.legs[0].start, *(leg.end for leg in self.legs))


def find(roads: Sequence[Road], start: str, end: str, names: Mapping[str, str]) -> Route | None:
    """The chain of roads from `start` to `end` with the fewest legs (at most `MAX_LEGS`), or None.
    None too when one road joins them directly: that is 10a's and 4b's answer, not a route.
    `names`: entry id to name, for the stable order; a place with no name is never a stop."""
    if start == end or start not in names or end not in names:
        return None
    by_place: dict[str, list[tuple[str, Road]]] = {}
    for road in roads:
        if road.a == road.b or road.a not in names or road.b not in names:
            continue
        by_place.setdefault(road.a, []).append((road.b, road))
        by_place.setdefault(road.b, []).append((road.a, road))
    for nexts in by_place.values():
        nexts.sort(key=lambda n: (names[n[0]].casefold(), n[0], n[1].id))
    if any(other == end for other, _road in by_place.get(start, [])):
        return None
    # (breadth first, so the first chain found has the fewest legs. A place is grown from only at
    # the depth it was first reached; `found` keeps every chain of the shortest length, in order)
    paths: list[tuple[Leg, ...]] = [()]
    reached = {start}
    found: list[tuple[Leg, ...]] = []
    for _depth in range(MAX_LEGS):
        longer: list[tuple[Leg, ...]] = []
        for path in paths:
            here = path[-1].end if path else start
            on_path = {start, *(leg.end for leg in path)}
            for other, road in by_place.get(here, []):
                if other in on_path:
                    continue
                step = (*path, Leg(here, other, road))
                if other == end:
                    found.append(step)
                elif other not in reached:
                    if len(longer) >= MAX_PATHS:
                        return None  # (cut short, the fewest legs can't be sure: no answer)
                    longer.append(step)
        if found:
            break
        reached.update(path[-1].end for path in longer)
        paths = longer
    if not found:
        return None
    first = found[0]
    on_route = {leg.end for leg in first}
    other_via = next((p[0].end for p in found[1:] if p[0].end not in on_route), None)
    return Route(first, other_via)


@dataclass(frozen=True, slots=True)
class Total:
    amount: float
    unit: str  # route_length's: hours, days or miles


# Why there is no total (the words are `legs_words`'s)
NO_LENGTH, NOT_A_NUMBER, A_RANGE, MIXED_UNITS, MIXED_WAYS = (
    "no length", "not a number", "a range", "mixed units", "mixed ways",
)  # fmt: skip

# The ways of travelling a time may name (the ux-critic's: two days by cart and three on foot add
# up to no one trip). A distance is the same whatever the way, so miles never need this.
_WAYS = {
    "foot": re.compile(r"\b(?:on\s+foot|walk|walking|march|hike|trek)\b", re.I),
    "ride": re.compile(r"\b(?:ride|riding|horse|horseback|mounted)\b", re.I),
    "cart": re.compile(r"\b(?:cart|wagon|carriage)\b", re.I),
    "boat": re.compile(r"\b(?:sail|sailing|boat|ship|barge)\b", re.I),
}


def _way(words: str) -> str:
    return next((w for w, pattern in _WAYS.items() if pattern.search(words)), "")


def total(route: Route) -> Total | str:
    """The legs' lengths added up, or why not (Supervisor's ruling on #4240: only in one unit;
    narrowed by the ux-critic's, so a total never says what isn't so): a leg with no length or no
    plain number, a range (its amount is the low end, so a sum would understate), mixed units, or
    times for different ways of travelling."""
    said = []
    for leg in route.legs:
        if not leg.road.length_words:
            return NO_LENGTH
        length = route_length.parse(leg.road.length_words)
        if length is None or length.amount is None or length.unit is None:
            return NOT_A_NUMBER
        if route_length.is_range(leg.road.length_words):
            return A_RANGE
        said.append(length)
    units = {length.unit for length in said}
    if len(units) != 1:
        return MIXED_UNITS
    unit = units.pop()
    assert unit is not None
    if unit != route_length.MILES and len({_way(length.words) for length in said}) != 1:
        return MIXED_WAYS
    return Total(sum(length.amount or 0.0 for length in said), unit)
