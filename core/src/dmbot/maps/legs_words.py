"""A route's words, leg by leg (#4260, MapBot layers slice 10d; the ux-critic's words). Plain and
short, with the lengths as they were said; nothing guessed. With a total, the total comes first:
it is what "how far" asked."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from dmbot.maps import legs
from dmbot.maps.legs import Route, Total

NO_LENGTH = "no length set"
CLOSED = "closed now"
WHY_NOT = {
    legs.NO_LENGTH: "No total, since a leg has no length.",
    legs.NOT_A_NUMBER: "No total, since a length isn't a number.",
    legs.A_RANGE: "No total, since a length is a range.",
    legs.MIXED_UNITS: "No total, since the legs are in different units.",
    legs.MIXED_WAYS: "No total, since the legs are for different ways of travelling.",
}


def _amount(t: Total) -> str:
    """'45 miles', '1 day', '2½ days', 'half an hour' (halves as the travel answer says them,
    rounded down to the half: the lengths were said "about" already)."""
    one = t.unit.rstrip("s")
    whole, half = int(t.amount), t.amount - int(t.amount) >= 0.5
    if whole == 0:
        a = "an" if one == "hour" else "a"
        return f"half {a} {one}" if half else f"{t.amount:g} {t.unit}"
    n = f"{whole}½" if half else str(whole)
    return f"1 {one}" if n == "1" else f"{n} {t.unit}"


def _and(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def route(found: Route, names: Mapping[str, str], whole: Total | str) -> str:
    """'About 45 miles by road from Targos to Easthaven, through Bryn Shander: Targos to Bryn
    Shander, 20 miles; Bryn Shander to Easthaven, 25 miles.' Without a total, the legs, then why
    there is none. A closed road is said on its leg, and again after a total."""
    start, end = names[found.stops[0]], names[found.stops[-1]]
    through = _and([names[s] for s in found.stops[1:-1]])
    said_legs = []
    for leg in found.legs:
        length = leg.road.length_words or NO_LENGTH
        closed = f" ({CLOSED})" if leg.road.closed else ""
        said_legs.append(f"{names[leg.start]} to {names[leg.end]}, {length}{closed}")
    way = f"by road from {start} to {end}, through {through}: {'; '.join(said_legs)}."
    if isinstance(whole, Total):
        said = f"About {_amount(whole)} {way}"
        shut = sum(1 for leg in found.legs if leg.road.closed)
        if shut:
            said += (
                " One road on the way is closed now."
                if shut == 1
                else f" {shut} roads on the way are closed now."
            )
    else:
        said = f"{way[0].upper()}{way[1:]} {WHY_NOT[whole]}"
    if found.other_via is not None:
        said += f" Another way goes through {names[found.other_via]}."
    return said
