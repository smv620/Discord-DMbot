"""How a character, group or item is tied to a place, in plain words (#3363). "Is in" sounds
permanent, so it is kept for what does not move (a tavern in a town, an event held somewhere).
Something that moves lives in, works in, is based in, is kept in, comes from, or "was last seen
in" a place. The word is kept on the connection's detail, as a family role is (`kin`).
Pure: no Discord, no database."""

from __future__ import annotations

from typing import NamedTuple

LIVES, WORKS, BASED, KEPT, FROM, LAST_SEEN = "lives", "works", "based", "kept", "from", "last_seen"


class Way(NamedTuple):
    key: str
    past: bool = False  # "lived in" stays past: it never ends a present "lives in"


# Words a detail may hold, and the way each one means. A detail that is none of these is not a
# way (the connection reads by its kind, as before).
_WAYS: dict[str, Way] = {
    "lives in": Way(LIVES), "lives": Way(LIVES), "resides in": Way(LIVES),
    "lived in": Way(LIVES, True), "lived": Way(LIVES, True),
    "works in": Way(WORKS), "works": Way(WORKS), "works at": Way(WORKS),
    "worked in": Way(WORKS, True), "worked": Way(WORKS, True),
    "is based in": Way(BASED), "based in": Way(BASED), "based": Way(BASED),
    "was based in": Way(BASED, True),
    "is kept in": Way(KEPT), "kept in": Way(KEPT), "kept": Way(KEPT),
    "was kept in": Way(KEPT, True),
    "comes from": Way(FROM), "is from": Way(FROM), "from": Way(FROM), "born in": Way(FROM),
    "grew up in": Way(FROM), "was born in": Way(FROM),
    "was last seen in": Way(LAST_SEEN), "last seen in": Way(LAST_SEEN),
    "last seen": Way(LAST_SEEN), "visited": Way(LAST_SEEN),  # a visit is one moment (#3244)
}  # fmt: skip
# The words as they read from the first name, in the present and the past.
_FORWARD = {
    LIVES: ("lives in", "lived in"),
    WORKS: ("works in", "worked in"),
    BASED: ("is based in", "was based in"),
    KEPT: ("is kept in", "was kept in"),
    FROM: ("comes from", "came from"),
    LAST_SEEN: ("was last seen in", "was last seen in"),
}
# The same from the place's side, before the name: "Aldmoor is home to Tavin".
_BACK = {
    LIVES: "is home to",
    WORKS: "is the workplace of",
    BASED: "is the base of",
    KEPT: "is where you keep",
    FROM: "is the origin of",
    LAST_SEEN: "is where you last saw",
}
# Kinds of entry that move. A place, an event or anything else stays "is in".
_MOBILE = frozenset({
    "character", "person", "npc", "player_character", "deity", "creature", "faction", "item",
})  # fmt: skip


def is_mobile(kind: str) -> bool:
    return kind in _MOBILE


def way_of(detail: str) -> Way | None:
    """The way a connection's detail says, or None when it says none."""
    return _WAYS.get(" ".join(detail.split()).casefold())


def forward(detail: str, subject_kind: str) -> str:
    """How the connection reads from the subject: "lives in" for a way, "was last seen in" for
    something that moves with no way said, and "is in" for what does not move."""
    way = way_of(detail)
    if way is not None:
        return _FORWARD[way.key][1 if way.past else 0]
    return _FORWARD[LAST_SEEN][0] if is_mobile(subject_kind) else "is in"


def backward(detail: str, subject_kind: str) -> str:
    """How it reads from the place's side: "is home to", "is where you last saw"."""
    way = way_of(detail)
    if way is not None:
        return _BACK[way.key]
    return _BACK[LAST_SEEN] if is_mobile(subject_kind) else "is where you find"
