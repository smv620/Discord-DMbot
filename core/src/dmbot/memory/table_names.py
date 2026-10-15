"""Learns who answers to which name at the table, from "a name, then an answer" (#1188).

A line calls someone by name ("Mike, what do you do?", "your turn, Cerric"); the very next
line, from someone else within ten seconds, answers in the first person ("I run with my
sword"). Said at least twice by the same person in a session, that is worth asking the DM
about. No AI: only these rules, and only consented speakers' lines.

Everything is kept in memory for one session. The caller feeds every spoken line in order,
saying whether its speaker agreed to be recorded: a line from someone who didn't agree is
never read, but it still breaks a pair, so a third speaker in between always counts."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from dmbot.memory.models import name_key
from dmbot.memory.ontology import is_dm_word

ANSWER_WITHIN_MS = 10_000
MIN_ANSWERS = 2  # a name answered by the same person this many times in a session
MAX_NAMES = 200  # a bound on one session's counts; a long rambling session can't grow it

# A name here is one or two capitalised words next to a comma. Words that start a sentence
# and are not names stay out.
_WORD = r"[A-Z][\w'’-]{1,24}"
_NAME = rf"{_WORD}(?: {_WORD})?"
_START = re.compile(rf"^\W*({_NAME})\s*,")
_END = re.compile(rf",\s*({_NAME})\W*$")
_NOT_NAMES = frozenset(
    [
        "i",
        "ok",
        "okay",
        "so",
        "well",
        "yes",
        "no",
        "yeah",
        "hey",
        "hi",
        "hello",
        "right",
        "sure",
        "now",
        "then",
        "and",
        "but",
        "oh",
        "um",
        "uh",
        "alright",
        "all",
        "everyone",
        "everybody",
        "guys",
        "folks",
        "please",
        "thanks",
        "thank",
        "dm",
        "gm",
        "dungeon",
        "master",
        "game",
        "dmbot",
        "team",
        "listen",
        "look",
        "wait",
        "great",
        "good",
        "nice",
        "cool",
    ]
)
_ANSWER = re.compile(
    r"\b(i|i'll|i’ll|i'm|i’m|i'd|i’d|i've|i’ve|my|me|ok|okay|sure|yes|yeah)\b", re.IGNORECASE
)
_VERBS = re.compile(
    r"^\W*(run|cast|attack|hit|stab|shoot|move|go|dash|hide|search|climb|jump|open|check|"
    r"roll|grab|take|use|drink|swing|cut|sneak|talk|ask|say|try|follow|help|dodge|"
    r"disengage|wait|ready|look|listen|throw|push|pull|kick|charge|fire|heal)\b",
    re.IGNORECASE,
)


def called_names(text: str) -> list[str]:
    """The names a line calls someone by: at its start before a comma, or at its end after
    one ("Mike, what do you do?", "your turn, Cerric"). At most one at each end."""
    found: list[str] = []
    for pattern in (_START, _END):
        m = pattern.search(text.strip())
        if m is None:
            continue
        name = " ".join(m.group(1).split())
        if name_key(name) not in _NOT_NAMES and not is_dm_word(name) and name not in found:
            found.append(name)
    return found


def is_answer(text: str) -> bool:
    """A first-person answer: it says I, I'll, I'm, my, ok, sure or yes, or it starts with
    an action verb ("run at it", "cast fireball")."""
    return bool(_ANSWER.search(text) or _VERBS.match(text))


@dataclass(frozen=True, slots=True)
class Answered:
    """`name` (as first said) was answered `times` times by `speaker`."""

    name: str
    speaker: int
    times: int


class NameThenAnswer:
    """The pairs seen this session. `feed` does a little constant work per line."""

    def __init__(self) -> None:
        self._previous: tuple[int, int, list[str]] | None = None  # speaker, at_ms, names
        self._counts: Counter[tuple[str, int]] = Counter()
        self._display: dict[str, str] = {}

    def feed(self, speaker: int, at_ms: int, text: str, *, consented: bool) -> None:
        """The next line of the session, in the order heard (a late line only loses a pair,
        never invents one). A line from someone who didn't agree is never read and breaks the
        pair, as any third voice would."""
        before, self._previous = self._previous, None
        if not consented:
            return
        if (
            before is not None
            and before[0] != speaker
            and 0 <= at_ms - before[1] <= ANSWER_WITHIN_MS
            and is_answer(text)
        ):
            for name in before[2]:
                self._count(name, speaker)
        names = called_names(text)
        if names:
            self._previous = (speaker, at_ms, names)

    def _count(self, name: str, speaker: int) -> None:
        key = name_key(name)
        if (key, speaker) not in self._counts and len(self._counts) >= MAX_NAMES:
            return
        self._display.setdefault(key, name)
        self._counts[(key, speaker)] += 1

    def answered(self, minimum: int = MIN_ANSWERS) -> list[Answered]:
        """Every name that one person answered at least `minimum` times, most first."""
        rows = [
            Answered(self._display[key], speaker, n)
            for (key, speaker), n in self._counts.items()
            if n >= minimum
        ]
        return sorted(rows, key=lambda a: (-a.times, a.name, a.speaker))

    def drop_speaker(self, user_id: int) -> None:
        """Stop recording me: nothing they said or answered stays (the pair in the middle
        too)."""
        for key in [k for k in self._counts if k[1] == user_id]:
            del self._counts[key]
        kept = {k[0] for k in self._counts}  # a name only they said is forgotten too
        self._display = {k: v for k, v in self._display.items() if k in kept}
        if self._previous is not None and self._previous[0] == user_id:
            self._previous = None
