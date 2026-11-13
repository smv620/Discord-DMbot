"""How two places are connected by a way (#4173 slice 6d, Supervisor's yes to the routes
proposal, 2026-11-11): the means a `route_to` connection keeps in its detail, from one fixed list
(road, river, sea, air, portal, wild), as `next_words` keeps how two places sit side by side. A
means off the list is kept as the text's own words ("other"). Two means between the same two places
are two connections: the detail is part of what makes one the same as another
(`checks.duplicate_of`).

A route reads both ways by default ("are connected by road"); the DM's direction mark (#4154) makes
it one way (a portal that only goes out, a river read downstream). A journey's order (travelled from
A to B) is the row's own order and its `route_state`, never the means. Pure: no Discord, no
database."""

from __future__ import annotations

from typing import NamedTuple

ROUTE_TO = "route_to"  # (the kind, `ontology`)
ROAD, RIVER, SEA, AIR, PORTAL, WILD = "road", "river", "sea", "air", "portal", "wild"
MEANS = (ROAD, RIVER, SEA, AIR, PORTAL, WILD)
# A route's state (its `route_state` column, migration 0467): known (the way exists, "" ),
# travelled (the party went that way, in the session `from_session_at` says) or planned (the
# table means to: never where anyone is)
KNOWN, TRAVELLED, PLANNED = "", "travelled", "planned"
STATES = (KNOWN, TRAVELLED, PLANNED)


class Means(NamedTuple):
    forward: str  # from the first place: "is connected by road to"
    pair: str  # with both places first: "are connected by road"
    by: str  # in a journey: "by road"


_MEANS: dict[str, Means] = {
    ROAD: Means("is connected by road to", "are connected by road", "by road"),
    RIVER: Means("is connected by river to", "are connected by river", "by river"),
    SEA: Means("is connected by sea to", "are connected by sea", "by sea"),
    AIR: Means("is connected by air to", "are connected by air", "by air"),
    PORTAL: Means("is linked by a portal to", "are linked by a portal", "through a portal"),
    WILD: Means(
        "is reached across the wild from", "are connected through the wild", "across the wild"
    ),
}
# The words a reader or the DM may give for each (as `next_words` takes several for one)
_SAID: dict[str, str] = {
    "road": ROAD, "by road": ROAD, "highway": ROAD, "trail": ROAD, "cart track": ROAD,
    "causeway": ROAD,
    "river": RIVER, "by river": RIVER, "upriver": RIVER, "downriver": RIVER, "by boat": RIVER,
    "sea": SEA, "by sea": SEA, "ship": SEA, "by ship": SEA, "by water": SEA, "trade route": SEA,
    "sea route": SEA, "water trade route": SEA, "ferry": SEA, "lake crossing": SEA,
    "air": AIR, "by air": AIR, "airship": AIR, "by airship": AIR, "flying": AIR,
    "griffon route": AIR,
    "portal": PORTAL, "permanent portal": PORTAL, "gate": PORTAL, "magic gate": PORTAL,
    "teleportation circle": PORTAL,
    "wild": WILD, "path through the wild": WILD, "through the wild": WILD, "cross-country": WILD,
    "overland": WILD, "on foot": WILD, "across the tundra": WILD, "over the pass": WILD,
}  # fmt: skip
OTHER_FORWARD = "is connected to"
OTHER_PAIR = "are connected"


def _key(text: str) -> str:
    return " ".join(text.casefold().split())


def means_of(detail: str) -> str:
    """The means on the list for a detail, or "" for one off it (the text's own words)."""
    return _SAID.get(_key(detail), "")


def canonical(text: str) -> str:
    """The detail a route is kept with: the means on the list ("road"), or the text's own words
    when it is none of them (an "other" route keeps its words), or "" when nothing was said."""
    return means_of(text) or " ".join(text.split())


def forward(detail: str) -> str:
    """How it reads from the first place: "is connected by road to", or with the text's own
    words, 'is connected (“by mule train”) to'."""
    means = means_of(detail)
    if means:
        return _MEANS[means].forward
    return f"is connected (“{detail}”) to" if detail else OTHER_FORWARD


def pair(detail: str) -> str:
    """With both places first: "are connected by road", 'are connected (“by mule train”)'."""
    means = means_of(detail)
    if means:
        return _MEANS[means].pair
    return f"{OTHER_PAIR} (“{detail}”)" if detail else OTHER_PAIR


def journey(detail: str) -> str:
    """How a travelled leg says its means: "by road", "through a portal", or the text's own
    words."""
    means = means_of(detail)
    return _MEANS[means].by if means else detail


def backward(detail: str, *, one_way: bool = False) -> str:
    """How it reads from the second place. Two-way (the default) reads as `forward`; one the DM
    made one way (a portal that only goes out) reads "is reached by road from", so the second place
    never seems to lead back."""
    if not one_way:
        return forward(detail)
    means = means_of(detail)
    if means:
        return f"is reached {_MEANS[means].by} from"
    return f"is reached (“{detail}”) from" if detail else "is reached from"
