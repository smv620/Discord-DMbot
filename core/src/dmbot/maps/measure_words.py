"""The words of MapBot's "how far" and "which way" answers (#4670, MapBot layers slice 4b;
docs/design/mapbot_layers.md §4.3, §4.4 and §9; Supervisor's ruling on #4670; the ux-critic's
wording). One place, so Discord and the website say the same. Pure: no Discord, no database.

Rules kept in the words (§9): every answer that measures says "about" and names the map it
used; a measured line is called a straight line; an answer that rests on north being assumed says
"if north is at the top"; no scale is no number, never a guess, with the fix said to the DM (a
player is told the DM can add one, as setting a scale is the DM's); "pin" is never said; and a
player is never told a hidden map exists (the same words whether or not a DM-only map has both
places). DMbot never says a stated distance is wrong."""

from __future__ import annotations

# The eight directions, clockwise from north (§9: "as one of eight directions").
DIRECTIONS = (
    "north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west",
)  # fmt: skip

LINK = "🗺️ Open the map"  # the link's words, after the answer


def on_map(title: str) -> str:
    """'the Icewind Dale map', or the title as it is when it says "map" already ("the Ten-Towns
    Map", never "Map map"); 'the map' with no title."""
    title = title.strip()
    if not title:
        return "the map"
    if "map" in title.casefold().split() or title.casefold().startswith("map "):
        return title if title.casefold().startswith(("the ", "map ")) else f"the {title}"
    return f"the {title} map"


def far(amount: str, a: str, b: str, map_title: str) -> str:
    """'About 40 miles from Bryn Shander to Targos in a straight line, on the Icewind Dale map.'
    (a, b as the question gave them.) Closer than the map can tell is `at_most`'s words."""
    return f"About {amount} from {a} to {b} in a straight line, on {on_map(map_title)}."


def at_most(limit: str, a: str, b: str, map_title: str) -> str:
    """Closer than the map can tell: 'About a mile or less from …' ("about" in every answer)."""
    return f"About {limit} or less from {a} to {b} in a straight line, on {on_map(map_title)}."


def way(place: str, direction: str, from_place: str, map_title: str, *, north_known: bool) -> str:
    """'Bryn Shander is about north-east of Targos on the Icewind Dale map.' With north assumed:
    '… on the Icewind Dale map, if north is at the top.' (One of eight directions is never exact,
    so "about" here too.)"""
    tail = "" if north_known else ", if north is at the top"
    return f"{place} is about {direction} of {from_place} on {on_map(map_title)}{tail}."


def no_scale(map_title: str, *, dm: bool) -> str:
    said = on_map(map_title)
    said = said[0].upper() + said[1:]
    if dm:
        return f"{said} has no scale. Set its scale on the Maps page and I can measure on it."
    return f"{said} has no scale, so I can't measure on it. Your DM can add one."


def no_shared_map(a: str, b: str, *, dm: bool) -> str:
    said = f"I don't have a map with both {a} and {b} on it."
    return f"{said} You can add both to one map on the Maps page." if dm else said


def not_on_a_map(name: str, *, dm: bool) -> str:
    said = f"I can't find {name} on any map yet."
    return f"{said} You can add it on the Maps page." if dm else said


def same_spot(a: str, b: str, map_title: str) -> str:
    return f"{a} and {b} are at about the same spot on {on_map(map_title)}."
