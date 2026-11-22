"""The three lines at the top of a character's card (#5050): who they are, where and when they
were first met, and when they were last mentioned. This file is the pure part: finding the first
and last line that names a character, finding the scene around it, and saying it in plain words.
No database, no AI, nothing sent anywhere.

A scene is DMbot's own short description of what happened (a recap's beat, never a quote of
what anyone said); with no recap for the session the line says the date alone, never an
invented scene. The words are plain: no "node", "entity" or "graph".
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from dmbot.memory import heard

if TYPE_CHECKING:
    from dmbot.transcript.models import Line

NEVER_AT_THE_TABLE = "Not mentioned at the table yet."
STILL_WRITING = "DMbot is still writing this."
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
class Hit:
    """The session and the line a character was first (or last) named in."""

    session_index: int  # the position in the sessions given, as they were given
    line_number: int  # `Said.number`: the 1-based place among the session's stored lines


def find_hit(
    sessions: Iterable[Sequence[Line]],
    agreed: Collection[int],
    named_in: Callable[[str], bool],
    *,
    newest: bool = False,
) -> Hit | None:
    """The first line that names the character, in sessions oldest first; with `newest`, the last
    line, in sessions as given (newest first), reading each one from its end. The lines are the
    live count's own (`heard.countable`: people who agree, no sidebar, no blank), so the card
    can never name a line the count did not see. Stops at the first session that has one."""
    for index, lines in enumerate(sessions):
        found = [n for n, line in heard.countable(lines, agreed) if named_in(line.text)]
        if found:
            return Hit(index, found[-1] if newest else found[0])
    return None


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


def when_line(number: int, started_at: int, beat: SceneLike | None) -> str:
    """ "Session 3, 15 Nov: the party reached the Northlook inn in Bryn Shander." The scene is
    left off, with the date kept, when the session has no recap beats."""
    head = f"Session {number}, {day(started_at)}"
    scene = scene_words(beat)
    return f"{head}: {scene}." if scene else f"{head}."


def lore_line(document: str, heading: str = "") -> str:
    """ "In your lore: *Icewind Dale notes*, under 'Bryn Shander'." The heading is left off when
    the lore reader kept none."""
    doc = " ".join(document.split())
    head = " ".join(heading.split())
    return f"In your lore: *{doc}*, under '{head}'." if head else f"In your lore: *{doc}*."


def who_line(sentence: str | None, name: str, *, pending: bool = False) -> str:
    """The first line: the one sentence DMbot wrote from what it holds, or why there is none."""
    if sentence and sentence.strip():
        return cut_sentence(sentence)
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
