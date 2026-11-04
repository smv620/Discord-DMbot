"""Descriptions are not names (#3431): "the woman", "beggar woman" and "a hooded figure" say what
someone is, not who, so no other name made of one may place a line or a connection on its own.
After a merge, "the woman" would otherwise match every woman in the story. Pure.

A spelling is a description when it has no capitalised word once a leading article or owner word is
off it. The entry keeps the spelling (it shows on the entry and in "Where was it said?"); only the
matching leaves it out. A DM who types a short other name by hand ("kind": short, nickname, title)
still gets it matched, because that is a choice, not a guess."""

from __future__ import annotations

_LEADING = frozenset({"the", "a", "an", "my", "our", "his", "her", "their", "your", "this", "that"})
_HAND_KINDS = frozenset({"short", "nickname", "title", "misheard"})


def is_description(text: str) -> bool:
    """No proper-noun word in it: every word after a leading article or owner is lower case."""
    words = [w for w in text.replace("'s", " ").split() if w[:1].isalpha()]
    while words and words[0].casefold() in _LEADING:
        words = words[1:]
    return bool(words) and all(w[0].islower() for w in words)


def matches_alone(text: str, kind: str, source: str) -> bool:
    """Whether another name may place a line or a connection by itself. A description may not,
    unless the DM typed it by hand as a short name, nickname or title."""
    if not is_description(text):
        return True
    return source == "dm" and kind in _HAND_KINDS
