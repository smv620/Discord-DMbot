"""How a character, group or item is tied to a place, in plain words (#3363). "Is in" sounds
permanent, so it is kept for what does not move (a tavern in a town, an event held somewhere).
Something that moves lives in, works in, is based in, is kept in, comes from, or "was last seen
in" a place. The word is kept on the connection's detail, as a family role is (`kin`).
Pure: no Discord, no database."""

from __future__ import annotations

from typing import NamedTuple

LIVES, WORKS, KEPT, FROM, LAST_SEEN = "lives", "works", "kept", "from", "last_seen"


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
    # A group "works in" a town like a character does (owner, 2026-11-03): "based in" is a
    # way of saying it, not a word of its own.
    "is based in": Way(WORKS), "based in": Way(WORKS), "based": Way(WORKS),
    "was based in": Way(WORKS, True),
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
    KEPT: ("is kept in", "was kept in"),
    FROM: ("comes from", "comes from"),  # where someone began never ends, so it has no past
    LAST_SEEN: ("was last seen in", "was last seen in"),
}
# The same from the place's side, before the name: "Aldmoor is home to Tavin". The past stays
# past here too, so a past home never reads as a present one.
_BACK = {
    LIVES: ("is home to", "was home to"),
    WORKS: ("is the workplace of", "was the workplace of"),
    KEPT: ("is where you keep", "was where you kept"),
    FROM: ("is the origin of", "is the origin of"),
    LAST_SEEN: ("is where you last saw", "is where you last saw"),
}
# How a place tie reads in a node's summary: (when the node is the subject, when it is the object),
# before any word is known and after ("lives there", "where they live").
_SUMMARY = {
    LIVES: (("lives there", "where they live"), ("lived there", "where they lived")),
    WORKS: (("works there", "where they work"), ("worked there", "where they worked")),
    KEPT: (("is kept there", "where it is kept"), ("was kept there", "where it was kept")),
    FROM: (("comes from there", "where they come from"),) * 2,
    LAST_SEEN: (("was last seen there", "where they were last seen"),) * 2,
}
# With no word said and no kind known the summary claims nothing about when or how.
_SUMMARY_PLAIN = ("tied to it", "tied to this place")
# Kinds of entry that move. A place, an event or anything else stays "is in".
_MOBILE = frozenset({
    "character", "person", "npc", "player_character", "deity", "creature", "faction", "item",
})  # fmt: skip


def is_mobile(kind: str) -> bool:
    """Does this kind of entry move, so that its place needs a word and never "is in"?"""
    return kind in _MOBILE


def way_of(detail: str) -> Way | None:
    """The way a connection's detail says, or None when it says none."""
    return _WAYS.get(" ".join(detail.split()).casefold())


def slot(detail: str) -> tuple[str, bool]:
    """Which place tie this detail is, for the one-at-a-time limit (#3363): the word and whether
    it is past. A new "lives in" ends only the old "lives in"; "lived in" is history and never
    ends it, and a past word is never limited at all. A sighting ends only the old sighting. No
    word is a sighting for something that moves and the plain place tie for a place, and a subject
    has only one of those."""
    way = way_of(detail)
    return (way.key, way.past) if way is not None else (LAST_SEEN, False)


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
        return _BACK[way.key][1 if way.past else 0]
    return _BACK[LAST_SEEN][0] if is_mobile(subject_kind) else "is where you find"


def summary_phrases(detail: str) -> tuple[str, str]:
    """How a place tie reads in a node's summary, by the word its detail says (#3363)."""
    way = way_of(detail)
    return _SUMMARY[way.key][1 if way.past else 0] if way is not None else _SUMMARY_PLAIN
