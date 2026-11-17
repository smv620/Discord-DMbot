"""When the DM's word and a map disagree (#4670, MapBot slice 4c; docs/design/mapbot_layers.md
§9, "When the DM's word and a map disagree"; Supervisor's ruling on #4670). Pure: no database, no
Discord, no AI. It only says whether a stated distance or direction is far off a kept map's; what
DMbot then says, and where, is the caller's. Nothing in the story is ever changed by it (§11).

Rules kept here (the owner's, 2026-11-12):
- **Distance, more than 20% off** the map's figure for the same two places is flagged. Against a
  straight line, only when the stated distance is the shorter one (a road is always longer than a
  straight line, so a longer stated one is no conflict); against a traced path, either way.
- **Direction, 45° or more** from the map's bearing is flagged; under 45° is silence. Only on a map
  whose north was read or set, never assumed.
- **Within those limits is silence.** A stated travel time is never compared (§9: time is only ever
  suggested), so only a stated length in miles or feet is.
- **A range is checked at its low end** (`route_length` keeps the lower end of "20-30 miles"), so
  a range whose top reaches the map's figure can still be flagged. Accepted: the note changes
  nothing and the DM keeps the length with one ✅.

The direction check (`check_direction`, `angle_of`) is for 4c-2's live note: no stated direction is
stored anywhere, so only the table's talk gives one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.maps import measure

OFF_BY = 0.20  # more than 20% off is flagged (§9)
WAY_OFF = 45.0  # 45° or more apart is flagged (§9)

FEET_PER_MILE = 5280.0

# The sixteen points of the compass, clockwise from north (§9: "north 0°, north-east 45°, east 90°,
# and so on round the sixteen points"), each with the ways a DM may say or write it.
_POINTS = (
    "north", "north-north-east", "north-east", "east-north-east",
    "east", "east-south-east", "south-east", "south-south-east",
    "south", "south-south-west", "south-west", "west-south-west",
    "west", "west-north-west", "north-west", "north-north-west",
)  # fmt: skip


def _key(said: str) -> str:
    return "".join(said.casefold().replace("-", " ").split())


ANGLES = {_key(p): i * 22.5 for i, p in enumerate(_POINTS)}


def angle_of(said: str) -> float | None:
    """A stated direction as degrees clockwise from north, or None for words that are no point of
    the compass. "north-east", "north east" and "northeast" are one."""
    return ANGLES.get(_key(said))


def in_miles(amount: float, unit: str) -> float | None:
    """A length in miles, or None for a unit that is no length (hours and days are times)."""
    if unit == "miles":
        return amount
    if unit == "feet":
        return amount / FEET_PER_MILE
    return None


def distance_off(stated: float, measured: float, *, straight: bool) -> bool:
    """Whether a stated distance is far enough off the map's to tell the DM (both in one unit)."""
    if measured <= 0 or stated <= 0:
        return False
    if straight:
        return stated < measured * (1.0 - OFF_BY)
    return abs(stated - measured) > measured * OFF_BY


def apart(a: float, b: float) -> float:
    """The smaller angle between two bearings, 0 to 180."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def direction_off(stated: float, bearing: float) -> bool:
    return apart(stated, bearing) >= WAY_OFF


@dataclass(frozen=True, slots=True)
class Distance:
    """A stated distance far off a map's straight line: the map and both figures, in miles."""

    map: measure.MapInfo
    stated_miles: float
    measured_miles: float


@dataclass(frozen=True, slots=True)
class Direction:
    """A stated direction far off a map's bearing: the map, and the map's direction in words."""

    map: measure.MapInfo
    map_way: str


def check_distance(
    maps: Sequence[measure.MapInfo],
    pins: Sequence[measure.Pin],
    a_id: str,
    b_id: str,
    stated_miles: float,
) -> Distance | None:
    """The map that disagrees with a stated distance between two places, or None. The map is the
    one an answer would use (§4.4: the most detailed with both places and a scale, never averaged),
    so the DM is told what "how far" would say. A straight line only: traced paths come later."""
    used, _ = measure.choose(maps, pins, a_id, b_id, need_scale=True)
    if used is None:
        return None
    pa, pb = measure.pin_of(pins, used.id, a_id), measure.pin_of(pins, used.id, b_id)
    if pa is None or pb is None:
        return None
    d = measure.distance(used, pa, pb)
    miles = in_miles(d, used.unit) if d is not None else None
    if miles is None or not distance_off(stated_miles, miles, straight=True):
        return None
    return Distance(used, stated_miles, miles)


def check_direction(
    maps: Sequence[measure.MapInfo],
    pins: Sequence[measure.Pin],
    place_id: str,
    from_id: str,
    stated: float,
) -> Direction | None:
    """The map that disagrees with "place is <stated> of from_place", or None. Only maps whose
    north was read or set count (never one assumed up); of those, the one an answer would use."""
    known = [m for m in maps if m.north_known]
    used, _ = measure.choose(known, pins, place_id, from_id, need_scale=False)
    if used is None:
        return None
    pp, pf = measure.pin_of(pins, used.id, place_id), measure.pin_of(pins, used.id, from_id)
    if pp is None or pf is None:
        return None
    deg = measure.bearing(used, pf, pp)  # (from the other place towards this one)
    if deg is None or not direction_off(stated, deg):
        return None
    return Direction(used, measure.direction(deg))


def amount(d: Distance) -> str:
    """The map's figure as said: "35 miles", or in feet below a mile ("2110 feet"), so a short
    stated length is set beside a figure that shows the difference (never "about 0")."""
    said = measure.rounded(d.measured_miles, "miles")
    if said is not None:
        return said
    return measure.rounded(d.measured_miles * FEET_PER_MILE, "feet") or measure.SMALLEST["feet"]
