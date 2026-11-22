"""Whether a road, pass or bridge is open, hard going, closed or gone (#4258, MapBot layers slice
4a; design `docs/design/mapbot_layers.md` §6 and §7, Supervisor's ruling on #4258): the four
words and the set phrases the lore reader knows. Pure: no Discord, no database.

The phrases are short on purpose and each later addition is scored on the sample, as the other
layers' are. A phrase counts only right after a route's own name ("Icewind Pass is snowed in") or
in "the road between A and B is …" / "the road from A to B is …"; anything else says nothing, and
a missing row is the safe side. Weather is never a condition ("a blizzard on the pass" says
nothing: §6 never stores weather), and a town is never "flooded" or "out" (the reader is asked
only about routes).
"""

from __future__ import annotations

import re
from typing import NamedTuple

from dmbot.memory import hedges

CONDITION = "condition"
OPEN, HARD, CLOSED, GONE = "open", "hard", "closed", "gone"
VALUES = (OPEN, HARD, CLOSED, GONE)

# The phrase right after the name, and the value it gives. "is out" is closed, not gone: a bridge
# that is out can be repaired (Supervisor). The open phrases are words of change: they only ever
# end a row that said otherwise (the store's §7.3 rule), never stand on their own.
PHRASES: dict[str, str] = {
    "is snowed in": CLOSED, "is blocked": CLOSED, "is closed": CLOSED, "is impassable": CLOSED,
    "is flooded": CLOSED, "is out": CLOSED,
    "is treacherous": HARD, "is hard going": HARD, "is rough": HARD, "is overgrown": HARD,
    "was destroyed": GONE, "has collapsed": GONE, "was washed away": GONE, "is no more": GONE,
    "is open again": OPEN, "was cleared": OPEN, "reopened": OPEN,
}  # fmt: skip
CHANGE = OPEN  # (the value only words of change give)
# These end their clause ("the bridge is out."), so "is out of reach", "is out west" and "is no more
# than a muddy track" say nothing (the reviewer's finding on #4258)
_ENDS_CLAUSE = frozenset({"is out", "is no more"})
# "reopened its tolls" reopened something else: never the road
_NOT_BEFORE = {"reopened": frozenset({"its", "the", "a", "an", "his", "her", "their", "our"})}
_WAYS = r"(?:road|way|path|trail)"
# A name after one of these is part of another road's name ("the road to Icewind Pass is closed"
# is about that road, not the pass): the safe side says nothing
_ABOUT_ANOTHER = frozenset(
    {"to", "from", "toward", "towards", "near", "past", "beyond", "through", "across", "over"}
)


class Said(NamedTuple):
    """One condition the text states: the value, and whether its words are words of change."""

    value: str
    changed: bool


class Between(NamedTuple):
    """A condition of the road between two places, as the text names them."""

    a: str
    b: str
    said: Said


def _phrase_at(rest: str) -> Said | None:
    """The phrase `rest` starts with, when it is one whole phrase, longest first."""
    for phrase in sorted(PHRASES, key=len, reverse=True):
        if not rest.startswith(phrase):
            continue
        after = rest[len(phrase) :]
        if phrase in _ENDS_CLAUSE:
            if after.strip() and after.lstrip()[:1] not in ",.;:!?)":
                continue
        else:
            following = after.split()[:1]
            if after[:1].isalnum() or (following and following[0] in _NOT_BEFORE.get(phrase, ())):
                continue
        value = PHRASES[phrase]
        return Said(value, value == CHANGE)
    return None


def _plain(text: str) -> str:
    return " ".join(text.split()).casefold()


def read(name: str, text: str, *, is_route: bool) -> Said | None:
    """The condition the text gives a route place, from the first mention followed right away by a
    phrase ("Icewind Pass is snowed in"). None for a place that isn't a route, and when nothing
    is said."""
    if not is_route or not name.strip() or not text:
        return None
    plain, key = _plain(text), _plain(name)
    for m in re.finditer(r"(?<!\w)" + re.escape(key) + r"(?!\w)", plain):
        rest = plain[m.end() :]
        before = plain[: m.start()].split()
        if before and before[-1] in _ABOUT_ANOTHER:
            continue
        if hedges.hedged(plain, m.start(), m.end()):  # (asked, supposed or someone's view, #4982)
            continue
        said = _phrase_at(rest[1:]) if rest.startswith(" ") else None  # (right after the name)
        if said is not None:
            return said
    return None


_BETWEEN = re.compile(
    r"(?<!\w)(?:the\s+)?" + _WAYS + r"\s+(?:between\s+(?P<a>.+?)\s+and\s+(?P<b>.+?)"
    r"|from\s+(?P<c>.+?)\s+to\s+(?P<d>.+?))\s+(?=(?:is|was|has|reopened)\b)"
)


def read_between(text: str) -> list[Between]:
    """Each "the road (way, path, trail) between A and B is …" or "… from A to B is …" with one of
    the phrases, as the text names A and B (the caller finds the places and the one known road
    that joins them, or writes nothing). In the text's own order."""
    out: list[Between] = []
    plain = _plain(text)
    for part in re.finditer(r"[^.!?;\n]+", plain):
        sentence = part.group()
        for m in _BETWEEN.finditer(sentence):
            if hedges.hedged(plain, part.start() + m.start(), part.start() + m.end()):
                continue  # (asked, supposed or someone's view, #4982)
            said = _phrase_at(sentence[m.end() :])
            if said is None:
                continue
            a, b = (m["a"], m["b"]) if m["a"] is not None else (m["c"], m["d"])
            out.append(Between(_name(a), _name(b), said))
    return out


def _name(words: str) -> str:
    """A place as the text names it, without a leading "the"."""
    words = words.strip(" ,")
    return words[4:] if words.startswith("the ") else words
