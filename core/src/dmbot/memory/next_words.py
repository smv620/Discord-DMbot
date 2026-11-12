"""How two places beside each other are said (#4173 slice 6a): the word a `next_to` connection keeps
in its detail, as a place tie keeps "lives in" (`place_words`). "Borders" reads the same from either
place ("share a border"); "on the shore of", "on the coast of", "at the foot of" and "on" (a town on
a named route) read one way, from the place beside to what it is beside, so the order they were said
in is kept. A detail that is none of these is plain "is next to". Pure: no Discord, no database."""

from __future__ import annotations

from typing import NamedTuple

BORDERS, SHORE, COAST = "borders", "on the shore of", "on the coast of"
FOOT, ON = "at the foot of", "on"


class Word(NamedTuple):
    forward: str  # from the first place: "is on the shore of"
    one_way: bool  # reads only from the first place: the stored order is the meaning
    pair: str = ""  # with both places first, for a two-way one: "share a border"


_WORDS: dict[str, Word] = {
    BORDERS: Word("borders", False, "share a border"),
    SHORE: Word("is on the shore of", True),
    COAST: Word("is on the coast of", True),
    FOOT: Word("is at the foot of", True),
    ON: Word("is on", True),
}
# The words a reader or the DM may give for each (as `place_words` takes several for one way)
_SAID: dict[str, str] = {
    "borders": BORDERS, "border": BORDERS, "shares a border with": BORDERS,
    "share a border": BORDERS,
    "on the shore of": SHORE, "on the shores of": SHORE, "shore": SHORE, "lakeside": SHORE,
    "on the coast of": COAST, "coast": COAST, "coastal": COAST,
    "at the foot of": FOOT, "foot": FOOT, "below": FOOT,
    "on": ON, "lies on": ON, "along": ON,
}  # fmt: skip
PLAIN = "is next to"
PLAIN_PAIR = "are next to each other"


def word_of(detail: str) -> str:
    """The stored word for a detail, or "" for plain "next to"."""
    return _SAID.get(" ".join(detail.casefold().split()), "")


def one_way(detail: str) -> bool:
    """Does this `next_to` read only from its first place ("on the shore of")? Then the order it
    was said in is its meaning, and it is never sorted or read back the other way."""
    word = word_of(detail)
    return bool(word) and _WORDS[word].one_way


def forward(detail: str) -> str:
    """How it reads from the first place: "is on the shore of", "borders", or "is next to"."""
    word = word_of(detail)
    return _WORDS[word].forward if word else PLAIN


def pair(detail: str) -> str:
    """With both places first, for one that reads both ways: "share a border" or "are next to
    each other"."""
    word = word_of(detail)
    return (_WORDS[word].pair or PLAIN_PAIR) if word else PLAIN_PAIR
