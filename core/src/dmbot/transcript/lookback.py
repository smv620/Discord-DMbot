"""The Transcript Cleaner's look-back pass (#127; docs/PLAN.md, "Transcript Cleaner"): it ASKS
only, it never changes a line by itself. Pure: no Discord, no database, no AI.

Speech is cut into pieces after a pause, so a name said with a breath in the middle ("Belle" |
"Ross") reaches the Cleaner as two lines, and the Cleaner reads one line at a time. This looks at
the seam between a speaker's piece and the one before it, and when the last words of the earlier
piece and the first words of this one, read together, are what the Cleaner would have fixed or
asked about in one line, it returns a question for the DM: "Did they mean…?", shown with both
pieces so the DM sees that the answer joins two lines.

Narrow on purpose (a wrong fix is worse than a missed one, and two lines would change at once):
- the same speaker, the earlier piece ending at most `GAP_MS` before this one begins;
- a run of 2 or 3 words with at least one on each side of the seam, found by the Cleaner itself
  (`cleaner.clean` on the two pieces read as one line), so every one of its checks holds: confirmed
  non-secret names only, spelled alike enough, one name, never a table person's name, never next
  to a secret name (the line as written is checked);
- not where either piece alone already has a fix or a question on those words;
- not when the tail ends in punctuation, or the head is followed straight away by punctuation or
  is the whole of its piece (taking it out would leave a stray mark or an empty line);
- a run of real words ("or us") is not found: the Cleaner never reads those as a name.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass

from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.scene import WORD
from dmbot.transcript.cleaner import Cleaned, Question, Vocabulary, clean, own_name

GAP_MS = 2500  # the earlier piece ended at most this long before this one began
OVERLAP_MS = 1000  # pieces may overlap a little (the cut is not exact)
CONTEXT_WORDS = 4  # words read from the end of the earlier piece and the start of this one


@dataclass(frozen=True, slots=True)
class Piece:
    """One person's piece of speech as the Cleaner read it, kept for the next piece."""

    started_ms: int
    end_ms: int
    heard: str
    result: Cleaned


@dataclass(frozen=True, slots=True)
class Seam:
    """Words that run across two pieces. `question.start`/`end` are where the head words are in
    this piece (as heard); `prev_start`/`prev_end` where the tail words are in the earlier one."""

    question: Question
    prev_start: int
    prev_end: int
    tail: str
    head: str
    prev: Piece


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _own_ranges(result: Cleaned, shift: int) -> list[tuple[int, int]]:
    ranges = [(f.start + shift, f.end + shift) for f in result.fixes]
    return ranges + [(q.start + shift, q.end + shift) for q in result.questions]


def find(
    lookup: CampaignLookup,
    prev: Piece,
    this: Piece,
    *,
    people: Iterable[str] = (),
    vocabulary: Vocabulary | None = None,
    scene: Collection[str] = (),
) -> Seam | None:
    """The question that joins the end of `prev` and the start of `this`, or None. The caller
    has checked that both pieces are the same person's and that they are still recorded."""
    gap = this.started_ms - prev.end_ms
    if gap > GAP_MS or gap < -OVERLAP_MS or this.started_ms < prev.started_ms:
        return None
    before, after = prev.heard.rstrip(), this.heard.lstrip()
    if not before or not after or not before[-1].isalnum():
        return None  # (a tail ending in punctuation: the mark would be lost in the join)
    lead = len(this.heard) - len(this.heard.lstrip())  # leading spaces dropped from `after`
    # Only the words around the seam are read, not both whole pieces: that is where a split
    # name is, and it keeps the work per line small.
    words = list(WORD.finditer(before))
    first = words[-CONTEXT_WORDS].start() if len(words) > CONTEXT_WORDS else 0
    window = before[first:]
    after_words = list(WORD.finditer(after))
    last = after_words[CONTEXT_WORDS - 1].end() if len(after_words) >= CONTEXT_WORDS else len(after)
    head_window = after[:last]
    cut = len(window) + 1  # where the later piece starts in the two read as one line
    both = f"{window} {head_window}"
    result = clean(
        lookup, both, vocabulary=vocabulary, people=list(people), scene=scene, unsure=True
    )
    prev_own = _own_ranges(prev.result, -first)
    this_own = _own_ranges(this.result, cut - lead)
    found: list[tuple[int, int, tuple[tuple[str, str], ...]]] = []
    for fix in result.fixes:
        name = own_name(lookup, fix.entity_id) if fix.entity_id else None
        if name is not None:
            found.append((fix.start, fix.end, ((fix.entity_id, name),)))
    for question in result.questions:
        found.append((question.start, question.end, question.options))
    for start, end, options in sorted(found):
        if not (start < len(window) and end > cut):  # must have words on both sides
            continue
        if any(_overlaps((start, end), own) for own in (*prev_own, *this_own)):
            continue  # a piece alone already has something on these words
        tail, head = both[start : len(window)].strip(), both[cut:end].strip()
        if not tail or not head:
            continue
        head_end = end - cut + lead  # in this piece as heard
        rest = this.heard[head_end:]
        if not rest.strip() or rest[0] != " ":
            continue  # the head is its whole piece, or punctuation follows it
        question = Question(
            start=0,
            end=head_end,
            heard=f"{tail} {head}",
            options=options,
            context=f"{tail} | {head}",
        )
        return Seam(question, first + start, len(before), tail, head, prev)
    return None


def head_edit(heard: str, end: int) -> tuple[int, int]:
    """The range to remove from this piece when its head words went into the earlier one:
    from the start to `end`, and the spaces after it, so no gap is left."""
    stop = end
    while stop < len(heard) and heard[stop] == " ":
        stop += 1
    return 0, stop
