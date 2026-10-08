"""The off-topic filter's pure parts (docs/PLAN.md, "Off-topic filter"; #52). No Discord,
no database, no AI.

Each line gets a topic: the game (in character, narration, NPCs), table talk (rules,
dice, "whose turn?"), or off-topic (scheduling, work, life, news). Only clearly
off-topic talk is hidden, and only in the cleaned transcript: "When unsure, keep it."

- `obviously_game`: a cheap check before any AI call. A line that names the campaign's
  people or places, or uses dice and rules words, is about the game; no need to ask.
  Nothing is ever *hidden* on keywords alone.
- `marker`: what replaces hidden talk, with how long it lasted:
  `[1m 22s of off-topic chat skipped]`, `[8s of off-topic chat skipped]`.
- `collapse`: a run of hidden lines from one person becomes one marker with the total
  time; anyone else speaking in between ends the run.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from dmbot.memory.scan import GAME_TERMS
from dmbot.memory.scene import WORD

GAME, TABLE_TALK, OFF_TOPIC = "game", "table_talk", "off_topic"
TOPICS = (GAME, TABLE_TALK, OFF_TOPIC)

# Words that only come up at a D&D table, beyond the rules words the name scan knows.
_TABLE_TEXT = """
    roll rolls rolled rolling reroll d4 d6 d8 d10 d12 d20 d100 nat crit crits critical
    initiative saving throw save saves attack attacks damage hit hits miss misses
    spell spells cast casts casting cantrip slot slots level hp ac dc modifier bonus
    action reaction turn round rest dm npc npcs campaign session character sheet
"""
_TABLE_WORDS = frozenset(_TABLE_TEXT.split())
_DICE = re.compile(r"\b(?:\d*d(?:4|6|8|10|12|20|100)|nat(?:ural)? ?(?:1|20))\b", re.IGNORECASE)


def obviously_game(text: str, named: int = 0) -> bool:
    """Plainly about the game, or too short to hide, so the AI needn't be asked: it names
    something from the campaign (`named`: how many of its names the line mentions), has
    dice ("d20", "2d6", "nat 20"), two or more table words ("roll initiative"), or is two
    words or fewer ("yeah", "ok sure": kept anyway, when unsure, keep it)."""
    if named or _DICE.search(text):
        return True
    words = [w.group().casefold() for w in WORD.finditer(text)]
    if len(words) <= 2:
        return True
    return sum(w in _TABLE_WORDS or w in GAME_TERMS for w in words) >= 2


def marker(seconds: float) -> str:
    """`[1m 22s of off-topic chat skipped]`; under a minute, `[8s of off-topic chat
    skipped]`; never less than one second."""
    total = max(1, round(seconds))
    minutes, secs = divmod(total, 60)
    length = f"{minutes}m {secs}s" if minutes else f"{secs}s"
    return f"[{length} of off-topic chat skipped]"


@dataclass(frozen=True, slots=True)
class Spoken:
    """A line for the cleaned transcript, with its topic and how long it was said."""

    speaker: int
    started_ms: int
    seconds: float
    text: str
    topic: str = GAME


@dataclass(frozen=True, slots=True)
class Shown:
    """What the cleaned transcript shows: the line, or a marker for hidden talk."""

    speaker: int
    started_ms: int
    text: str
    skipped: bool = False


def collapse(lines: Iterable[Spoken]) -> Iterator[Shown]:
    """Lines in spoken order, with each run of one person's off-topic lines turned into
    one marker (the run ends when anyone else speaks, or they say something else)."""
    run: list[Spoken] = []

    def flush() -> Iterator[Shown]:
        if run:
            seconds = sum(s.seconds for s in run)
            yield Shown(run[0].speaker, run[0].started_ms, marker(seconds), skipped=True)
            run.clear()

    for line in lines:
        if line.topic == OFF_TOPIC:
            if run and run[0].speaker != line.speaker:
                yield from flush()
            run.append(line)
            continue
        yield from flush()
        yield Shown(line.speaker, line.started_ms, line.text)
    yield from flush()


# The AI is asked about a window of lines at once: this many, or as many as come in
# this long, whichever is first (#52: one call per window bounds the cost).
WINDOW_LINES = 6
WINDOW_S = 20.0


@dataclass(frozen=True, slots=True)
class Waiting:
    """A line waiting for the filter: who, when (to find it again), and its words."""

    speaker: int
    started_ms: int
    text: str
    seconds: float = 0.0  # how long it was said (for the live marker)


@dataclass(slots=True)
class TopicWindow:
    """Lines not obviously about the game, gathered until there are enough to ask about
    (`WINDOW_LINES`) or the first has waited `WINDOW_S`. Pure: the caller asks the AI."""

    lines: list[Waiting] = field(default_factory=list)
    opened_at: float | None = None  # monotonic seconds, when the first line came in
    window_lines: int = WINDOW_LINES
    window_s: float = WINDOW_S

    def add(self, line: Waiting, now: float) -> bool:
        """Add a line; True if the window is now full or due (take it and ask)."""
        if not self.lines:
            self.opened_at = now
        self.lines.append(line)
        return len(self.lines) >= self.window_lines or self.due(now)

    def due(self, now: float) -> bool:
        return (
            bool(self.lines)
            and self.opened_at is not None
            and (now - self.opened_at >= self.window_s)
        )

    def take(self) -> list[Waiting]:
        lines, self.lines, self.opened_at = self.lines, [], None
        return lines

    def drop_speaker(self, speaker: int) -> None:
        """They stopped being recorded: their lines are never sent to the AI."""
        self.lines = [w for w in self.lines if w.speaker != speaker]
        if not self.lines:
            self.opened_at = None
