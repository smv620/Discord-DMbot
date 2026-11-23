"""The three lines at the top of a character's card (#5050): who they are, where and when they
were first met, and when they were last mentioned. This file is the pure part: finding the first
and last line that names a character, finding the scene around it, and saying it in plain words.
No database, no AI, nothing sent anywhere.

A scene is DMbot's own short description of what happened (a recap's beat, or a clause the
background job wrote from the lines around the mention; never a quote of what anyone said, never
invented). With no scene yet the line says it is still being written, never the date alone for
good. The words are plain: no "node", "entity" or "graph".
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from dmbot.memory import heard

if TYPE_CHECKING:
    from dmbot.transcript.models import Line

FIRST_MET = "First met"
LAST_MENTIONED = "Last mentioned"
NEVER_AT_THE_TABLE = "Not mentioned at the table yet."
STILL_WRITING = "DMbot is still writing this."
HIDDEN_WHO = "DMbot writes this line only from what your players may know."
WHO_MAX = 300  # the sentence is cut here at the end of a word, if the AI went long


class SceneLike(Protocol):
    """What a recap beat gives (`recaps.beats.Beat`): what happened, where, and the line numbers
    it rests on."""

    @property
    def what(self) -> str: ...

    @property
    def where(self) -> str: ...

    @property
    def lines(self) -> Sequence[int]: ...


@dataclass(frozen=True, slots=True)
class Seen:
    """What one pass over one session's lines found for one character."""

    first_play: int | None  # the first line that names them, not counting a recap read aloud
    last: int  # the last line that names them, a recap's included (it still was said that day)


@dataclass(frozen=True, slots=True)
class Where:
    """A session (its place in the list given, oldest first) and a line number in it."""

    session_index: int
    line_number: int


@dataclass(frozen=True, slots=True)
class Span:
    """Where a character was first met and last mentioned; None for never, at the table."""

    first: Where | None
    last: Where | None


def scan_session(
    lines: Sequence[Line],
    agreed: Collection[int],
    named_in: Callable[[str], Iterable[str]],
    wanted: Collection[str],
    retold: Collection[int] = (),
) -> dict[str, Seen]:
    """One pass over a session's lines for ALL the characters asked about at once (#5050: a batch
    of characters costs one read of each session, never one per character). The lines are the live
    count's own (`heard.countable`: people who agree, no sidebar, no blank), so the card can never
    name a line the count did not see. `retold`: the lines read aloud as last time's recap: they
    never make a "first met" (the first time the DM recaps someone is never when they appeared),
    but they do count for "last mentioned". Only characters in `wanted` are noted."""
    left_out = set(retold)
    found: dict[str, Seen] = {}
    for number, line in heard.countable(lines, agreed):
        for entity_id in set(named_in(line.text)) & set(wanted):
            before = found.get(entity_id)
            first = before.first_play if before else None
            if first is None and number not in left_out:
                first = number
            found[entity_id] = Seen(first, number)
    return found


async def spans(
    count: int,
    scan: Callable[[int], Awaitable[Mapping[str, Seen]]],
    wanted: Collection[str],
) -> dict[str, Span]:
    """First and last mention of each character in `wanted` across `count` sessions, oldest first.
    `scan(i)` is session i's pass (`scan_session`, after loading its lines), asked for lazily and
    at most once for each session, so a long campaign is read once for the whole batch. "First"
    is looked for from the oldest session on and stops when every character has one; "last" from
    the newest back, the same."""
    memo: dict[int, Mapping[str, Seen]] = {}

    async def seen(index: int) -> Mapping[str, Seen]:
        if index not in memo:
            memo[index] = await scan(index)
        return memo[index]

    first: dict[str, Where] = {}
    last: dict[str, Where] = {}
    want = set(wanted)
    for index in range(count):
        if len(first) == len(want):
            break
        for entity_id, hit in (await seen(index)).items():
            if entity_id in want and entity_id not in first and hit.first_play is not None:
                first[entity_id] = Where(index, hit.first_play)
    for index in range(count - 1, -1, -1):
        if len(last) == len(want):
            break
        for entity_id, hit in (await seen(index)).items():
            if entity_id in want and entity_id not in last:
                last[entity_id] = Where(index, hit.last)
    return {entity_id: Span(first.get(entity_id), last.get(entity_id)) for entity_id in want}


def scene_near(beats: Sequence[SceneLike], line_number: int) -> SceneLike | None:
    """The beat a line belongs to: the one that cites it, else the one whose cited lines are
    nearest (a name said between two scenes belongs to the closer one; a tie goes to the earlier).
    None when the session has no beats."""
    best: tuple[int, int, SceneLike] | None = None
    for position, beat in enumerate(beats):
        if not beat.lines:
            continue
        gap = min(abs(n - line_number) for n in beat.lines)
        if best is None or (gap, position) < (best[0], best[1]):
            best = (gap, position, beat)
    return None if best is None else best[2]


def day(started_at: int) -> str:
    """ "15 Nov": the session's day, UTC, as the recap list says it (a number is not enough)."""
    stamp = dt.datetime.fromtimestamp(started_at, dt.UTC)
    return f"{stamp.day} {stamp:%b}"


def _sentence(text: str) -> str:
    return " ".join(text.split()).rstrip(" .")


def scene_words(beat: SceneLike | None) -> str:
    """What was happening, from the beat: its words and its place when the words don't say it.
    "" for no beat."""
    if beat is None or not _sentence(beat.what):
        return ""
    what = _sentence(beat.what)
    where = _sentence(beat.where)
    if where and where.casefold() not in what.casefold():
        what = f"{what} in {where}"
    return what


STILL_WRITING_SCENE = "(DMbot is still writing what happened)"


def when_line(label: str, number: int, started_at: int, scene: str = "") -> str:
    """The finished line, label and all, for the card and the website to draw as it is:
    "First met: Session 3, 15 Nov. The party reached the Northlook inn in Bryn Shander."
    `scene`: DMbot's words for what was happening, a recap beat's (`scene_words`) or the clause
    the background job wrote and kept. Never the date alone for good: until the job has written a
    scene the line says so ("First met: Session 3, 15 Nov (DMbot is still writing what
    happened).") and the card never waits for it."""
    head = f"{label}: Session {number}, {day(started_at)}"
    text = _sentence(scene)
    if not text:
        return f"{head} {STILL_WRITING_SCENE}."
    return f"{head}. {text[0].upper()}{text[1:]}."


def lore_line(document: str, heading: str = "") -> str:
    """ "First met: in your lore, *Icewind Dale notes*, under 'Bryn Shander'." The heading is left
    off when the lore reader kept none."""
    doc = " ".join(document.split())
    head = " ".join(heading.split())
    where = f"*{doc}*, under '{head}'" if head else f"*{doc}*"
    return f"{FIRST_MET}: in your lore, {where}."


def who_line(
    sentence: str | None, name: str, *, pending: bool = False, hidden: bool = False
) -> str:
    """The first line: the one sentence DMbot wrote from what it holds, or why there is none:
    still being written (`pending`), written from nothing the players may know (`hidden`: a secret
    NPC must not read as an unknown one, nor give away that anything is held back), or too little
    known."""
    if sentence and sentence.strip():
        return cut_sentence(sentence)
    if hidden:
        return HIDDEN_WHO
    if pending:
        return STILL_WRITING
    return f"DMbot doesn't know much about {name} yet."


def cut_sentence(text: str, limit: int = WHO_MAX) -> str:
    """The AI's sentence, on one line, cut at the end of a word if it ran past `limit`. (A length
    is a target in the instructions, never a check: nothing is refused or redone for it; this
    only keeps a card from carrying a paragraph.)"""
    one = " ".join(text.split())
    if len(one) <= limit:
        return one
    return one[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
