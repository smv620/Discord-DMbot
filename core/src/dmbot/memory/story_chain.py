"""Where the story is, in one uploaded document (#4173 slice 3; Supervisor's rulings on #4173,
2026-11-11 and 2026-11-13): the places a lore upload names, put in the order the text first names
them, and each one's assumed parent, the nearest bigger place still open when it is named. "The
Three Flags Sailing" named while Targos is open is assumed to be in Targos, because nothing says the
story left it. Pure: no Discord, no database.

The rulings this keeps:
- The chain is per document: one upload's places, in that upload's own words, never two files.
- The order is the text's (the first spelling of each place), never the AI's listing order.
- A place first named in a list (three or more of this read's places in one sentence) opens no level
  and gets no assumed parent: a list says nothing about what is inside what.
- A place of no known size takes no part, and nor does water or a route (outside the size order,
  slice 6a).
- A place with a stated parent is never given an assumed one, but it is on the chain: what comes
  after it may be assumed inside it.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from dmbot.memory import place_levels, places

LIST_PLACES = 3  # this many of the read's places in one sentence is a list (Supervisor, #4173)
# A sentence ends at . ! ? or a line break (a heading, a list item); the next starts after it.
# ("St." or "Mt." also ends one: it only matters to the list rule, and a list cut in two is
# simply not a list, so no parent is ever assumed from it.)
_SENTENCE_END = re.compile(r"[.!?]+(?=\s)|\n")


@dataclass(frozen=True, slots=True)
class Place:
    """One place this read found: its entry, its size and every spelling the text may use."""

    entity_id: str
    level: str
    spellings: tuple[str, ...]


def _sentences(text: str) -> list[tuple[int, int]]:
    """Each sentence's (start, end) in `text`."""
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _SENTENCE_END.finditer(text):
        spans.append((start, m.end()))
        start = m.end()
    spans.append((start, len(text)))
    return spans


def _sentence_of(spans: Sequence[tuple[int, int]], at: int) -> tuple[int, int]:
    return next((s for s in spans if s[0] <= at < s[1]), (at, at + 1))


def assumed_parents(
    text: str, found: Sequence[Place], stated: Collection[str]
) -> list[tuple[str, str]]:
    """(place, its assumed parent) for each place of `found` that `stated` (the entries that already
    have a parent, said in this read or kept in the campaign) leaves out and that has a bigger place
    open when the text first names it. A place at a size closes every open place of that size or
    smaller, so a new tavern closes the old tavern and any room in it."""
    usable = [p for p in found if p.level in place_levels.RANK]
    first: list[tuple[int, Place]] = []
    for p in usable:
        spot = places.find_in(text, p.spellings)
        if spot is not None:
            first.append((spot[0], p))
    first.sort(key=lambda pair: (pair[0], place_levels.RANK[pair[1].level]))
    spans = _sentences(text)
    in_sentence: dict[tuple[int, int], int] = {}
    for at, _ in first:
        span = _sentence_of(spans, at)
        in_sentence[span] = in_sentence.get(span, 0) + 1
    open_at: dict[int, str] = {}  # rank (0 = largest) to the place open at that size
    pairs: list[tuple[str, str]] = []
    for at, p in first:
        if in_sentence[_sentence_of(spans, at)] >= LIST_PLACES:
            continue  # (a list: no level opened, no parent assumed)
        rank = place_levels.RANK[p.level]
        for r in [r for r in open_at if r >= rank]:
            del open_at[r]
        parent = open_at[max(open_at)] if open_at else None
        if parent is not None and p.entity_id not in stated and parent != p.entity_id:
            pairs.append((p.entity_id, parent))
        open_at[rank] = p.entity_id
    return pairs
