"""The words of the travel-time suggestion (#4260, MapBot layers slice 10a; design §9; the
ux-critic's words before code). Pure. The DM's only: a player's table question gets "That's one
for your DM." before any map is read.

Rules kept here (the ux-critic's):
- **The time first**, then the distance it rests on, the places and the map, then the citation,
  then "Your call." (two words, the DM decides; never on a quote of the DM's own lore).
- **A pace is listed only when its time differs** from Normal's, and a pace the land rules out is
  said with the land that rules it out, never dropped without a word.
- **Days to the half,** "1½ days"; under a day, hours.
- **The land's own words** ("ice and snow", "mountains"), as the DM's land menu says them."""

from __future__ import annotations

import math
from collections.abc import Sequence

from dmbot.maps import travel
from dmbot.maps.measure_words import on_map

YOUR_CALL = "Your call."
FOR_DM = "That's one for your DM."
# The land as the DM's menu says it (`ui/place_world.LAND`), lower case, for "<land> allow only …"
LAND = {
    "arctic": "ice and snow", "coastal": "the coast", "desert": "desert", "forest": "forest",
    "grassland": "grassland", "hill": "hills", "mountain": "mountains", "swamp": "swamp",
    "underdark": "the Underdark", "urban": "town",
}  # fmt: skip
HOW = {"mounted": "mounted", "wagon": "wagon", "boat": "boat"}
MILES_AN_HOUR = {travel.FAST: 4, travel.NORMAL: 3, travel.SLOW: 2}


def amount(days: float) -> str:
    """'1½ days', '2 days', 'half a day'."""
    whole, half = int(days), days - int(days) >= 0.5
    if whole == 0:
        return "half a day"
    said = f"{whole}½" if half else str(whole)
    return f"{said} day" if said == "1" else f"{said} days"


def _hours(miles: float, pace: str) -> str:
    n = max(1, math.ceil(miles / MILES_AN_HOUR[pace]))
    return f"{n} hour" if n == 1 else f"{n} hours"


def _short(plan: travel.Plan, miles: float, pace: str) -> str:
    """One pace's time: hours under a day at a Normal pace, else days."""
    found = next((d for d in plan.days if d.pace == pace), None)
    if found is None:
        return ""
    if miles < travel.MILES_A_DAY[travel.NORMAL]:
        return _hours(miles, pace)
    return amount(found.days)


def lead(plan: travel.Plan, miles: float) -> str:
    """The first sentence: the time on foot, and what the land allows."""
    paces = [d.pace for d in plan.days]
    land = LAND.get(plan.capped_by, plan.capped_by)
    if paces == [travel.SLOW]:
        return f"About {_short(plan, miles, travel.SLOW)} on foot: only a Slow pace through {land}."
    normal = _short(plan, miles, travel.NORMAL)
    text = f"About {normal} on foot at a Normal pace"
    for pace, word in ((travel.FAST, "Fast"), (travel.SLOW, "Slow")):
        other = _short(plan, miles, pace)
        if other and other != normal:
            gear = (
                " (needs skis on ice and snow)"
                if pace == travel.FAST and plan.fast_needs_gear
                else ""
            )
            text += f"; {other} if {word}{gear}"
    text += "."
    if travel.FAST not in paces:
        text += f" No Fast pace through {land}."
    return text


def straight(amount_said: str, a: str, b: str, map_title: str) -> str:
    return (
        f"That's about {amount_said} in a straight line from {a} to {b} on {on_map(map_title)}."
        " No road is marked, so this is across open country, and a real path is longer."
    )


def by_road(length: str, a: str, b: str, source: str) -> str:
    """A stated length in miles: 'That's 40 miles by road from A to B, from your lore.'"""
    return f"That's {length} by road from {a} to {b}, {source}."


def stated_time(length: str, a: str, b: str, source: str) -> str:
    """A stated length that is a time already: given back, never summed (and no "Your call.")."""
    return f"{source[:1].upper()}{source[1:]} {length} from {a} to {b}."


def no_land() -> str:
    return "No land is set there, so I haven't allowed for terrain."


def closed() -> str:
    return "The road between them is marked closed; this assumes it's open."


def cannot(how: str) -> str:
    return f"I can't work out {HOW.get(how, how)} travel yet."


def cite(sources: Sequence[str]) -> str:
    """'SRD 5.2.1 pp. 12, 192' (one source, its pages), else each as it is."""
    pages = [s.rsplit(" p. ", 1) for s in sources]
    if len(pages) > 1 and all(len(p) == 2 for p in pages) and len({p[0] for p in pages}) == 1:
        return f"{pages[0][0]} pp. {', '.join(p[1] for p in pages)}."
    return "; ".join(sources) + "."
