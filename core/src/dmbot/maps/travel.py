"""The sums for "how long will it take?" (#4260, MapBot layers slice 10a; design
docs/design/mapbot_layers.md §9, "Travel time is suggested, never flagged"). Pure: no database, no
Discord, no AI. A suggestion for the DM, never a fact: the miles come from what "how far" already
uses, the pace from the SRD, and the terrain caps it. Nothing here changes the game clock or marks
a journey travelled.

The numbers:
- **Miles a day on foot** for each pace (the SRD's travel pace table). `PACE_SOURCE` names the entry
  cited; which edition's table is used is Supervisor's ruling on #4260.
- **The Maximum Pace each terrain allows** (SRD 5.2 Travel Terrain, p. 192): the slowest of the two
  places' terrains caps the pace. Arctic allows Fast only with the right equipment (skis).
- **Days** are rounded up to the next half day, and never less than half a day: travel is planned
  in parts of days, and "about" is always said."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

FAST, NORMAL, SLOW = "fast", "normal", "slow"
PACES = (FAST, NORMAL, SLOW)
_ORDER = {SLOW: 0, NORMAL: 1, FAST: 2}

# Miles a day for 8 hours on foot (the travel pace table); the source is cited with every answer
MILES_A_DAY = {FAST: 30, NORMAL: 24, SLOW: 18}
PACE_SOURCE = "SRD Travel Pace, p. 192"
TERRAIN_SOURCE = "SRD Travel Terrain, p. 192"

# The Maximum Pace column of the SRD 5.2 Travel Terrain table, by the terrain layer's own words
# (`memory/map_facts.py`); "waterborne" depends on the vessel, so it gives no foot pace at all
MAX_PACE = {
    "arctic": FAST, "coastal": NORMAL, "desert": NORMAL, "forest": NORMAL, "grassland": FAST,
    "hill": NORMAL, "mountain": SLOW, "swamp": SLOW, "underdark": NORMAL, "urban": NORMAL,
}  # fmt: skip
NEEDS_GEAR_FOR_FAST = frozenset({"arctic"})  # (skis or the like, the table's footnote)


@dataclass(frozen=True, slots=True)
class Days:
    """One pace's suggestion: the pace, and the days rounded up to a half."""

    pace: str
    days: float


@dataclass(frozen=True, slots=True)
class Plan:
    """What the answer says: the paces the terrain allows (fastest first, at most three), the
    terrain that caps them ("" when none does), and whether Fast needs gear."""

    days: tuple[Days, ...]
    capped_by: str = ""
    fast_needs_gear: bool = False


def half_days(miles: float, per_day: int) -> float:
    """Days rounded up to the next half, at least a half."""
    return max(0.5, math.ceil(miles / per_day * 2) / 2)


def cap(terrains: Iterable[str]) -> tuple[str, str]:
    """The fastest pace the terrains allow, and the terrain that sets it ("" when none caps it)."""
    best, by = FAST, ""
    for terrain in terrains:
        allowed = MAX_PACE.get(terrain)
        if allowed is not None and _ORDER[allowed] < _ORDER[best]:
            best, by = allowed, terrain
    return best, by


def plan(miles: float, terrains: Iterable[str]) -> Plan:
    """The suggestion for this many miles across these terrains, on foot."""
    seen = [t for t in terrains if t]
    top, by = cap(seen)
    allowed = [p for p in PACES if _ORDER[p] <= _ORDER[top]]
    return Plan(
        days=tuple(Days(p, half_days(miles, MILES_A_DAY[p])) for p in allowed),
        capped_by=by,
        fast_needs_gear=FAST in allowed and any(t in NEEDS_GEAR_FOR_FAST for t in seen),
    )
