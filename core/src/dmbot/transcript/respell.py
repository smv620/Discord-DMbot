"""Carrying a fixed spelling through a campaign's cleaned transcripts (#2170, docs/PLAN.md).
Pure: no Discord, no database, no AI.

When the DM fixes a name's spelling, the cleaned text of past sessions follows: whole-name
matches of the old spelling become the new one. It is deliberately narrow, because a wrong
change in a transcript the whole server can read is worse than a missed one:

- whole words only ("Branok" in "Branok's axe", never inside "Branoki");
- never a match that is part of a longer name the campaign knows ("Branok Hall"), secret
  names included: what is covered is left as it was and nothing is written;
- only the cleaned text. What was heard is never touched (the owner's #933 rule).

The positions of what was changed and the name as it was written there are kept, never the words
around them, so an Undo can put the old spelling back without the log holding anything anyone
said.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Respelled:
    text: str  # the line with the new spelling
    offsets: tuple[int, ...]  # where each new spelling starts in `text`, in order
    was: tuple[str, ...]  # what each place said before (the name only, as it was written)


def _words(name: str) -> re.Pattern[str]:
    """The name as whole words, any case, any run of spaces between its words."""
    parts = [re.escape(w) for w in name.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)", re.IGNORECASE)


def _spans(text: str, name: str) -> list[tuple[int, int]]:
    return [m.span() for m in _words(name).finditer(text)] if name.split() else []


def respell(text: str, old: str, new: str, longer: Iterable[str] = ()) -> Respelled | None:
    """`text` with each whole-word `old` written as `new`, or None when nothing changes. `longer`:
    the campaign's other names that contain `old` as whole words; a match inside one of them
    is left alone."""
    mine = _spans(text, old)
    if not mine:
        return None
    covered = [span for name in longer for span in _spans(text, name)]
    keep = [(a, b) for a, b in mine if not any(c <= a and b <= d for c, d in covered)]
    keep = [span for span in keep if text[span[0] : span[1]] != new]
    if not keep:
        return None
    out: list[str] = []
    offsets: list[int] = []
    was: list[str] = []
    at = 0
    length = 0
    for a, b in keep:
        out.append(text[at:a])
        length += a - at
        offsets.append(length)
        was.append(text[a:b])
        out.append(new)
        length += len(new)
        at = b
    out.append(text[at:])
    return Respelled("".join(out), tuple(offsets), tuple(was))


def put_back(text: str, offsets: Sequence[int], was: Sequence[str], new: str) -> str | None:
    """`text` with the new spelling at each recorded place written as it was before, or None when
    the line was changed since (a place no longer holds `new`): that line is left as it is."""
    if len(offsets) != len(was):
        return None
    places = sorted(zip(offsets, was, strict=True), reverse=True)
    if any(text[at : at + len(new)] != new for at, _ in places):
        return None
    for at, before in places:
        text = text[:at] + before + text[at + len(new) :]
    return text


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def done_note(old: str, new: str, lines: int, sessions: int) -> str:
    """What the DM is told after a fixed spelling reached past transcripts. Names and counts
    only, never a line."""
    where = (
        f"{_count(lines, 'line', 'lines')} of {_count(sessions, 'past session', 'past sessions')}"
    )
    return f"Changed {old} to {new} in {where}."


def undone_note(old: str, lines: int, sessions: int) -> str:
    where = (
        f"{_count(lines, 'line', 'lines')} of {_count(sessions, 'past session', 'past sessions')}"
    )
    return f"Put {old} back in {where}."
