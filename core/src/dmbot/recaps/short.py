"""A short session asks the DM before a recap is written (#2104).

A recap is written after every session (docs/PLAN.md, "Recaps go on after every session"), but a
session under `RECAP_SHORT_MINUTES` long, or with that little game talk in it, is often a quick
chat or a test. The bot asks the DM first instead of spending a recap on it. Pure functions: how
long the session ran, how much of it was game talk, and the words of the question. Nothing here
reads or changes what the recap itself reads.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from dmbot.memory.extractor import readable
from dmbot.transcript.models import OFF_RECORD, Line

RECAP_SHORT_MINUTES = 30  # a backend constant: under this, in span or in game talk, the DM is asked
MS_PER_MINUTE = 60_000


def span_minutes(lines: Sequence[Line]) -> int:
    """From the first stored line's start to the last one's end, in whole minutes."""
    lines = [line for line in lines if line.sidebar != OFF_RECORD]  # a marker is no speech
    if not lines:
        return 0
    first = min(line.started_ms for line in lines)
    last = max(line.started_ms + max(line.duration_ms, 0) for line in lines)
    return max(last - first, 0) // MS_PER_MINUTE


def talk_minutes(lines: Sequence[Line], agreed: Collection[int]) -> int:
    """The spoken time of the lines the recap would read: game talk of the people who agreed,
    with the off-topic chat, the DM sidebar and everyone else left out (`extractor.readable`)."""
    total = sum(
        lines[said.number - 1].duration_ms
        for said in readable(lines)
        if said.user_id in agreed and lines[said.number - 1].duration_ms > 0
    )
    return total // MS_PER_MINUTE


def is_short(span: int, talk: int) -> bool:
    return span < RECAP_SHORT_MINUTES or talk < RECAP_SHORT_MINUTES


def _minutes(n: int) -> str:
    if n < 1:
        return "under a minute"
    return f"{n} minute" if n == 1 else f"{n} minutes"


def question(span: int, talk: int) -> str:
    """The question on the DM screen, in plain words."""
    return (
        f"Short session: {_minutes(span)} long, {_minutes(talk)} of game talk (off-topic chat "
        "not counted). Write a recap?\nNo answer means no recap."
    )
