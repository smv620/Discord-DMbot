"""Measuring on a kept map (#4670, MapBot layers slice 4b; docs/design/mapbot_layers.md §4.3, §4.4
and §9; Supervisor's ruling on #4670): a straight line between two pins with the map's scale, and
the bearing from one to the other with its north. Pure: no database, no Discord, no AI.

Rules kept here:
- **No scale, no distance** (§4.3): a map with no scale gives no number, never a guess.
- **Which map** (§4.4): the most detailed map with both places (a battle or building map before a
  settlement's, before a region's, before the world's) and, for a distance, a scale; the next one up
  when the most detailed has none. Maps are never averaged.
- **Measured is never exact** (§9): numbers are rounded and always said "about".
- **North** is the map's own (read from a compass or set by the DM), else assumed up, and an answer
  resting on that says so.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.maps import measure_words as words

# How detailed a map is, by its kind: the smaller the number, the closer in (§4.4).
DETAIL = {"battle": 0, "building": 1, "settlement": 2, "region": 3, "world": 4, "other": 5}


@dataclass(frozen=True, slots=True)
class MapInfo:
    id: str
    title: str
    kind: str
    width: int | None
    height: int | None
    per_px: float | None  # distance per pixel of the full-size picture, or None: no scale
    unit: str  # "miles" or "feet" ("" with no scale)
    north: float  # degrees clockwise from the top of the picture
    north_known: bool  # read from a compass or set by the DM (not assumed up)
    created_at: int = 0  # the newer of two maps of one kind answers first


@dataclass(frozen=True, slots=True)
class Pin:
    map_id: str
    place_id: str
    x: float  # across, 0 to 1
    y: float  # down, 0 to 1


def distance(m: MapInfo, a: Pin, b: Pin) -> float | None:
    """The straight line from a to b in the map's unit, or None with no scale or no size."""
    if m.per_px is None or not m.width or not m.height:
        return None
    return math.hypot((b.x - a.x) * m.width, (b.y - a.y) * m.height) * m.per_px


def bearing(m: MapInfo, a: Pin, b: Pin) -> float | None:
    """Degrees clockwise from north, from a towards b; None when they sit on one spot. The
    picture's pixels are used (a map need not be square) when its size is known."""
    w, h = (m.width or 1), (m.height or 1)
    dx, dy = (b.x - a.x) * w, (b.y - a.y) * h
    if dx == 0 and dy == 0:
        return None
    on_picture = math.degrees(math.atan2(dx, -dy))  # (0 = up, 90 = right)
    return (on_picture - m.north) % 360.0


def direction(degrees: float) -> str:
    """One of the eight directions (§9)."""
    return words.DIRECTIONS[round(degrees / 45.0) % 8]


def rounded(value: float, unit: str) -> str | None:
    """A measured number as said: miles whole from 10 up, to a half below; feet to the nearest 5;
    with its unit, singular for one. None when it is less than the smallest it says (never
    "about 0 miles": the answer says "less than a mile" instead)."""
    # (halves round up: Python's round() takes them to the even number, which could say "0")
    if unit == "feet":
        if value < 2.5:
            return None
        return f"{max(5, math.floor(value / 5.0 + 0.5) * 5)} feet"
    if value < 0.75:
        return None
    if value < 10:
        said = f"{max(1.0, math.floor(value * 2 + 0.5) / 2):g}"
        return f"{said} mile" if said == "1" else f"{said} miles"
    return f"{math.floor(value + 0.5)} miles"


SMALLEST = {"feet": "5 feet", "miles": "a mile"}


def choose(
    maps: Sequence[MapInfo], pins: Sequence[Pin], a: str, b: str, *, need_scale: bool
) -> tuple[MapInfo | None, MapInfo | None]:
    """(the map to answer from, the most detailed map with both places). The first is None when no
    map has both or, for a distance, none with both has a scale; the second says which map to name
    then ("this map has no scale")."""
    on: dict[str, set[str]] = {}
    for p in pins:
        on.setdefault(p.map_id, set()).add(p.place_id)
    both = sorted(
        (m for m in maps if {a, b} <= on.get(m.id, set())),
        key=lambda m: (DETAIL.get(m.kind, 5), -m.created_at, m.id),
    )
    if not both:
        return None, None
    if need_scale:
        usable = [m for m in both if m.per_px and m.width and m.height]
        return (usable[0] if usable else None), both[0]
    # (a direction is truest on a map whose size is known: a wide map is not square)
    sized = [m for m in both if m.width and m.height]
    return (sized or both)[0], both[0]


def pin_of(pins: Sequence[Pin], map_id: str, place_id: str) -> Pin | None:
    return next((p for p in pins if p.map_id == map_id and p.place_id == place_id), None)


def answer(
    kind: str,
    maps: Sequence[MapInfo],
    pins: Sequence[Pin],
    a: tuple[str, str],
    b: tuple[str, str],
    *,
    dm: bool,
) -> str:
    """The answer's words, `kind` "far" or "way", for the places a and b as (id, name), in the
    order the question gave them. Always an answer: a number, a direction, or what is missing, with
    the fix said to the DM (`dm`). `maps` are only those the asker may see: a player's are the
    table's, so a player is never told a hidden map exists."""
    (a_id, a_name), (b_id, b_name) = a, b
    seen = {m.id for m in maps}
    pins = [p for p in pins if p.map_id in seen]
    if not any(p.place_id == a_id for p in pins):
        return words.not_on_a_map(a_name, dm=dm)
    if not any(p.place_id == b_id for p in pins):
        return words.not_on_a_map(b_name, dm=dm)
    used, closest = choose(maps, pins, a_id, b_id, need_scale=kind == "far")
    if closest is None:
        return words.no_shared_map(a_name, b_name, dm=dm)
    if used is None:
        return words.no_scale(closest.title, dm=dm)
    pa, pb = pin_of(pins, used.id, a_id), pin_of(pins, used.id, b_id)
    assert pa is not None and pb is not None
    if kind == "far":
        d = distance(used, pa, pb)
        assert d is not None
        amount = rounded(d, used.unit)
        if amount is None:
            return words.at_most(SMALLEST[used.unit], a_name, b_name, used.title)
        return words.far(amount, a_name, b_name, used.title)
    deg = bearing(used, pb, pa)  # ("which way is A from B": from B towards A)
    if deg is None:
        return words.same_spot(a_name, b_name, used.title)
    return words.way(a_name, direction(deg), b_name, used.title, north_known=used.north_known)
