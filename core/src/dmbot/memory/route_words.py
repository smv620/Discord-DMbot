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

import re
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
        "is connected across the wild to", "are connected across the wild", "across the wild"
    ),
}
# The words a reader or the DM may give for each (as `next_words` takes several for one)
_SAID: dict[str, str] = {
    "road": ROAD, "by road": ROAD, "highway": ROAD, "cart track": ROAD,
    "causeway": ROAD,
    "river": RIVER, "by river": RIVER, "upriver": RIVER, "downriver": RIVER, "by boat": RIVER,
    "sea": SEA, "by sea": SEA, "ship": SEA, "by ship": SEA, "sea route": SEA,
    "water trade route": SEA, "sea trade route": SEA, "ferry": SEA, "lake crossing": SEA,
    "air": AIR, "by air": AIR, "airship": AIR, "by airship": AIR,
    "griffon route": AIR,
    "portal": PORTAL, "permanent portal": PORTAL, "magic gate": PORTAL,
    "teleportation circle": PORTAL,
    "wild": WILD, "path through the wild": WILD, "through the wild": WILD, "cross-country": WILD,
    "overland": WILD, "across the tundra": WILD,
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


def said_in(text: str, means: str) -> bool:
    """Does the text itself name this means, in any of its words ("a highway runs" names a road)?
    A route's means must be stated (Supervisor's condition): the AI's choice alone is not enough."""
    words = f" {' '.join(re.findall(r'[\w-]+', text.casefold()))} "
    return any(
        f" {said} " in words
        for said, kept in _SAID.items()
        if kept == means and said not in _NOT_ALONE
    )


# A means' own word that says nothing alone in a story's words ("wild shape", "a wild boar"): it
# names the stored means, but a stated way needs a longer phrase ("through the wild")
_NOT_ALONE = frozenset({WILD})


# A journey or a plan reads one way only, from where the party set out (`checks.ordered` keeps that
# order): "Targos is where the party set out by road for Bryn Shander", never "connected by road".
_STATE_FORWARD = {
    TRAVELLED: "is where the party set out {by} for",
    PLANNED: "is where the party plans to set out {by} for",
}
_STATE_BACK = {
    TRAVELLED: "is where the party arrived {by} from",
    PLANNED: "is where the party plans to go {by} from",
}


def _by(detail: str) -> str:
    means = means_of(detail)
    if means:
        return _MEANS[means].by
    return f"(“{detail}”)" if detail else ""


def state_forward(detail: str, state: str) -> str:
    """How a journey or a plan reads from where the party set out; `forward` for a known route."""
    if state not in _STATE_FORWARD:
        return forward(detail)
    return " ".join(_STATE_FORWARD[state].format(by=_by(detail)).split())


def state_backward(detail: str, state: str, *, one_way: bool = False) -> str:
    """How a journey or a plan reads from where the party arrived; `backward` for a known route."""
    if state not in _STATE_BACK:
        return backward(detail, one_way=one_way)
    return " ".join(_STATE_BACK[state].format(by=_by(detail)).split())


def summary_phrase(detail: str, state: str, *, outward: bool) -> str:
    """A route in a place's summary, said of the neighbour about the place (`node_summary`):
    "connected by road"; a journey "where the party went by road" (the place was where it set out)
    or "where the party came from by road"; a plan "where the party plans to go by road" or "where
    the party plans to set out from by road". Never a journey or a plan read as a road."""
    by = _by(detail)
    if state == TRAVELLED:
        text = f"where the party went {by}" if outward else f"where the party came from {by}"
    elif state == PLANNED:
        text = (
            f"where the party plans to go {by}"
            if outward
            else f"where the party plans to set out from {by}"
        )
    else:
        text = f"connected {by}"
    return " ".join(text.split())
