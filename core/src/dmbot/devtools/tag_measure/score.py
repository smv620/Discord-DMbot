"""The tag measure's pure part (#4456; `docs/design/session_tagger.md` §10): where each transcript
line sits in the session file, the batches with the lines they cover, and each tag's reach and
load against the sample session key. No AI, no database, nothing printed."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.memory import tagger

# key rows that are not this session's own words: the "Previously" recap and rows made by a rule
NOT_READ = frozenset({"recap_only", "by_rule"})


def _squash(text: str) -> str:
    return re.sub(r"\W+", " ", text).strip().lower()


def file_lines(text: str, said: Sequence[str]) -> list[int]:
    """The file's line number (1-based, blank lines counted, as the key's `line` is) where each
    spoken line starts: the next line of the file that holds its first words, in order. A line
    that can't be found takes the one before it, so the order holds (counted by the caller)."""
    rows = [_squash(r) for r in text.lstrip("﻿").split("\n")]
    out: list[int] = []
    at = 0
    for words in said:
        head = _squash(words)[:40]
        found = next((i for i in range(at, len(rows)) if head and head in rows[i]), None)
        if found is None:
            out.append(out[-1] if out else 1)
            continue
        out.append(found + 1)
        at = found + 1
    return out


@dataclass(frozen=True, slots=True)
class Span:
    """One batch: its transcript line numbers and the file lines it covers (its first line up to
    the line before the next batch starts, so wrapped lines count)."""

    numbers: tuple[int, ...]
    first: int
    last: int


def spans(batches: Sequence[Sequence[int]], starts: Sequence[int], end: int) -> list[Span]:
    """Each batch's file lines. `batches`: transcript line numbers (1-based) in each batch;
    `starts`: the file line each transcript line starts on; `end`: the file's last line."""
    out: list[Span] = []
    for i, batch in enumerate(batches):
        first = starts[batch[0] - 1]
        nxt = starts[batches[i + 1][0] - 1] - 1 if i + 1 < len(batches) else end
        out.append(Span(tuple(batch), first, max(first, nxt)))
    return out


def key_rows(key: Mapping[str, Any]) -> list[tuple[str, int]]:
    """(tag, file line) for each row of the session key a tag ought to reach (§10): connections by
    their relation word, story threads as `quest`. Rows with no line, the recap's and a rule's are
    left out."""
    by_relation = {r: t.key for t in tagger.TAGS for r in t.relations}
    out: list[tuple[str, int]] = []
    for row in key.get("pairs", []):
        tag = by_relation.get(str(row.get("relation", "")))
        line = row.get("line")
        if tag and isinstance(line, int) and line > 0 and row.get("tier") not in NOT_READ:
            out.append((tag, line))
    for thread in key.get("threads", []):
        for line in thread.get("lines", []):
            if isinstance(line, int) and line > 0:
                out.append(("quest", line))
    return out


def measured_rows(
    rows: Sequence[tuple[str, int]], batch_spans: Sequence[Span], done: int
) -> list[tuple[str, int]]:
    """The key rows the measured part of the session could reach: a run the cap stopped early is
    scored on the batches it tagged only."""
    if done <= 0:
        return []
    last = batch_spans[done - 1].last
    return [(tag, line) for tag, line in rows if line <= last]


def outside(rows: Sequence[tuple[str, int]], batch_spans: Sequence[Span]) -> int:
    """Key rows no batch covers (before the first spoken line, past the end): a sign the file is
    not the export the key's line numbers were written on, when there are many."""
    return sum(1 for _, line in rows if not any(s.first <= line <= s.last for s in batch_spans))


def report(
    batch_spans: Sequence[Span],
    tagged: Sequence[Sequence[tagger.Tagged]],
    failed: Sequence[bool],
    rows: Sequence[tuple[str, int]],
) -> dict[str, dict[str, float]]:
    """Per tag (§10): its key rows, how many fall in a batch carrying the tag (`reach`), and with
    a neighbour batch too (`reach_near`, what a reader sent the batch either side would see); the
    share of batches tagged (`load`) and how many of those only "maybe". A failed batch counts as
    tagged with everything, as it is sent to every reader (§4)."""
    out: dict[str, dict[str, float]] = {}
    n = len(batch_spans)
    for kind in tagger.TAGS:
        on = [bool(failed[i]) or any(t.tag == kind.key for t in tagged[i]) for i in range(n)]
        maybe = sum(
            1
            for i in range(n)
            if not failed[i]
            and any(t.tag == kind.key for t in tagged[i])
            and all(t.sure == tagger.MAYBE for t in tagged[i] if t.tag == kind.key)
        )
        mine = [line for tag, line in rows if tag == kind.key]
        reached = near = 0
        for line in mine:
            i = next((j for j, s in enumerate(batch_spans) if s.first <= line <= s.last), None)
            if i is None:
                continue
            reached += on[i]
            near += any(on[j] for j in (i - 1, i, i + 1) if 0 <= j < n)
        out[kind.key] = {
            "rows": len(mine),
            "reach": reached,
            "reach_near": near,
            "tagged": sum(on),
            "load": sum(on) / n if n else 0.0,
            "maybe": maybe,
        }
    return out
