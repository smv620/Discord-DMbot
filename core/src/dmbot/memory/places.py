"""Where a suggested name was said (#2329): the pointers kept next to a suggestion, and the short
windows the review card shows from them. Pure: no Discord, no database.

A pointer is a line reference (`transcript.models.line_ref`: session, when the line began, who said
it) and the place of the name in it, kept in `memory_mentions`. The words are never copied: the
card reads them from the session's own transcript when it is drawn, so a deleted transcript, a
person who withdrew consent or a respelled name show as they are now."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from dmbot.transcript.models import Line

MAX_PLACES = 7  # pointers kept for one suggestion: two shown, five more behind "More places"
SHOWN_AT_FIRST = 2
LINE_CHARS = 120  # a line is cut to about this around the name


@dataclass(frozen=True, slots=True)
class Spot:
    """One place a name was said: which line, and where in its words."""

    line: Line
    start: int
    end: int


def _pattern(spellings: Iterable[str]) -> re.Pattern[str] | None:
    words = sorted({s.strip() for s in spellings if s.strip()}, key=len, reverse=True)
    if not words:
        return None
    return re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(w) for w in words) + r")(?!\w)", re.IGNORECASE
    )


def find_in(text: str, spellings: Iterable[str]) -> tuple[int, int] | None:
    """The first place any of these spellings is said in `text` (whole words, any case)."""
    pattern = _pattern(spellings)
    found = pattern.search(text) if pattern else None
    return (found.start(), found.end()) if found else None


def find_spots(lines: Sequence[Line], spellings: Iterable[str]) -> list[Spot]:
    """Every line of table speech that says the name, once each, in the order spoken. The DM
    sidebar is left out: it is DMbot's answers, not what the table said."""
    pattern = _pattern(spellings)
    if pattern is None:
        return []
    spots: list[Spot] = []
    for line in lines:
        if line.sidebar:
            continue
        found = pattern.search(line.text)
        if found:
            spots.append(Spot(line, found.start(), found.end()))
    return spots


def spread(items: Sequence[Spot], n: int = MAX_PLACES) -> list[Spot]:
    """At most `n` of them, spaced across the whole session (first and last included)."""
    if len(items) <= n:
        return list(items)
    if n <= 1:
        return list(items[:n])
    picks = {round(i * (len(items) - 1) / (n - 1)) for i in range(n)}
    return [items[i] for i in sorted(picks)]


def cut(text: str, start: int, end: int, width: int = LINE_CHARS) -> tuple[str, str, str]:
    """(before, name, after): `text` cut to about `width` characters around the name, with "…"
    where words were left out."""
    name = text[start:end]
    room = max(0, width - len(name))
    before, after = text[:start], text[end:]
    keep_before = min(len(before), room // 2)
    keep_after = min(len(after), room - keep_before)
    keep_before = min(len(before), room - keep_after)
    head = before[len(before) - keep_before :] if keep_before else ""
    tail = after[:keep_after]
    return (
        ("…" if keep_before < len(before) else "") + head,
        name,
        tail + ("…" if keep_after < len(after) else ""),
    )


def trimmed(text: str, width: int = LINE_CHARS, *, from_end: bool = False) -> str:
    """A neighbouring line cut to `width` characters: its start, or its end for the line before."""
    text = " ".join(text.split())
    if len(text) <= width:
        return text
    return "…" + text[-width:] if from_end else text[:width] + "…"
