"""The words of MapBot's "how far" and "which way" answers (#4670, MapBot layers slice 4b;
docs/design/mapbot_layers.md §4.3, §4.4 and §9; Supervisor's ruling on #4670; the ux-critic's
wording). One place, so Discord and the website say the same. Pure: no Discord, no database.

Rules kept in the words (§9): every answer says "about" and names the map it used; a measured line
is called a straight line; an answer that rests on north being assumed says "if north is at the
top"; no scale is no number, never a guess, with the fix said to the DM (a player is told the DM
can add one, as setting a scale is the DM's); "pin" is never said; and a player is never told a
hidden map exists (the same words whether or not a DM-only map has both places). DMbot never says
a stated distance is wrong."""

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
    (a, b as the question gave them.) `amount` "" is less than the map can tell: 'Less than a mile
    …' or 'Less than 100 feet …' comes from `less_than`."""
    return f"About {amount} from {a} to {b} in a straight line, on {on_map(map_title)}."


def less_than(limit: str, a: str, b: str, map_title: str) -> str:
    return f"Less than {limit} from {a} to {b} in a straight line, on {on_map(map_title)}."


def way(b: str, direction: str, a: str, map_title: str, *, north_known: bool) -> str:
    """'Bryn Shander is north-east of Targos on the Icewind Dale map.' With north assumed:
    '… on the Icewind Dale map, if north is at the top.'"""
    tail = "" if north_known else ", if north is at the top"
    return f"{b} is {direction} of {a} on {on_map(map_title)}{tail}."


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
    return f"{a} and {b} are at the same spot on {on_map(map_title)}."
