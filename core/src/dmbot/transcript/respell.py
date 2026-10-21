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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dmbot.recaps.store import RecapRespelled


@dataclass(frozen=True, slots=True)
class Respelled:
    text: str  # the line with the new spelling
    offsets: tuple[int, ...]  # where each new spelling starts in `text`, in order
    was: tuple[str, ...]  # what each place said before (the name only, as it was written)


_APOSTROPHES = "'\u2019\u2018\u02bc`"
_DASHES = "-\u2010\u2011\u2013"


def _part(word: str) -> str:
    """One word of a name as a pattern: any apostrophe or dash in it matches its look-alikes, as
    the secret-name matcher does (a phone types a curly one)."""
    out = []
    for ch in word:
        if ch in _APOSTROPHES:
            out.append(f"[{_APOSTROPHES}]")
        elif ch in _DASHES:
            out.append(f"[{_DASHES}]")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def _words(name: str) -> re.Pattern[str]:
    """The name as whole words, any case, any run of spaces between its words."""
    parts = [_part(w) for w in name.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)", re.IGNORECASE)


def _spans(text: str, name: str) -> list[tuple[int, int]]:
    return [m.span() for m in _words(name).finditer(text)] if name.split() else []


def contains(text: str, name: str) -> bool:
    """Whether `name` is in `text` as whole words, any case (the longer-name check)."""
    return bool(_spans(text, name))


def respell(
    text: str, old: str, new: str, longer: Iterable[str] = (), hidden: Iterable[str] = ()
) -> Respelled | None:
    """`text` with each whole-word `old` written as `new`, or None when nothing changes. `longer`:
    the campaign's other names that contain `old` as whole words; a match inside one of them
    is left alone. `hidden`: names players must not read (secret names, DM-only entries): a line
    the change would make say one the line did not say before is left as it was."""
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
    result = "".join(out)
    if any(contains(result, h) and not contains(text, h) for h in hidden):
        return None  # the new spelling would complete a name that is not for the transcript
    return Respelled(result, tuple(offsets), tuple(was))


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


def left_note(remaining: int) -> str:
    """How many lines are still to do after one run, and what to press."""
    return (
        f"Up to {_count(remaining, 'more line', 'more lines')} may still change: "
        "press Finish the past transcripts."
    )


def undone_note(old: str, lines: int, sessions: int) -> str:
    where = (
        f"{_count(lines, 'line', 'lines')} of {_count(sessions, 'past session', 'past sessions')}"
    )
    return f"Put {old} back in {where}."


def recaps_note(
    old: str, new: str, done: RecapRespelled, *, after_transcripts: bool = False
) -> str:
    """What the DM is told about recaps after a fixed spelling (#2170): counts and what stays.
    `after_transcripts`: a sentence about the past transcripts came first, so this one says
    "Also"."""
    parts = []
    if done.waiting:
        parts.append(_count(done.waiting, "recap that is waiting", "recaps that are waiting"))
    if done.posted:
        parts.append(_count(done.posted, "posted recap", "posted recaps"))
    note = ""
    if parts:
        lead = "Also changed" if after_transcripts else "Changed"
        note = f"{lead} {old} to {new} in {' and '.join(parts)}."
    if done.approvals_kept:
        note += " An approval stays: a spelling fix is not a change of story."
    if done.posted:
        note += " Posts already in Discord are not changed."
    if done.skipped:
        many = _count(done.skipped, "recap", "recaps")
        note += f" {many} would get too long, so left as written."
    if done.audio_old:
        note += f" Audio already made still says {old}; make it again on the website."
    return note.strip()
