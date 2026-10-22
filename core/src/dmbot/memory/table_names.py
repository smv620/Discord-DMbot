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
from collections.abc import Mapping
from dataclasses import dataclass

from dmbot.memory.models import name_key
from dmbot.memory.ontology import is_dm_word
from dmbot.names import not_names

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


def addressed(text: str) -> list[str]:
    """The words a line calls someone by, as written: at its start before a comma, or at its end
    after one ("Mike, what do you do?", "DM, can I roll?"). At most one at each end. The DM
    words are here; `called_names` leaves them out."""
    found: list[str] = []
    for pattern in (_START, _END):
        m = pattern.search(text.strip())
        if m is None:
            continue
        name = " ".join(m.group(1).split())
        if name not in found:
            found.append(name)
    return found


def called_names(text: str) -> list[str]:
    """The names a line calls someone by: at its start before a comma, or at its end after
    one ("Mike, what do you do?", "your turn, Cerric"). At most one at each end. Never DM, GM,
    Dungeon Master or Game Master: they are not names (#1208)."""
    return [
        name
        for name in addressed(text)
        if name_key(name) not in _NOT_NAMES
        and not is_dm_word(name)
        and not_names.keep(name, text)  # never a pronoun, unless the line says it is a name (#2358)
    ]


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
        # The campaign's DMs and their own names (#1547): a line that calls the DM by a DM word or
        # by a DM's own name is about the DM who is here, never a name to learn. Only this class
        # reads what is kept; nothing goes to the story graph.
        self._dm_keys: dict[str, int] = {}
        self._dm_ids: frozenset[int] = frozenset()
        self._dm_spoke: dict[int, int] = {}  # DM user id → when they last spoke (ms)
        self._dm_called: Counter[int] = Counter()

    def set_dms(self, dms: Mapping[int, str]) -> None:
        """Who the campaign's DMs are and what each is called at the table (their person name,
        '' for none). Called again whenever it may have changed."""
        # Two DMs with the same name: that name means neither (it could not be told apart).
        keys: dict[str, int | None] = {}
        for uid, name in dms.items():
            if name.strip():
                keys[name_key(name)] = None if name_key(name) in keys else uid
        self._dm_keys = {k: u for k, u in keys.items() if u is not None}
        self._dm_ids = frozenset(dms)

    def session_dm(self, caller: int, at_ms: int) -> int | None:
        """The DM a line to "the DM" means: a DM who has spoken in this session (so is here and
        agreed), not the caller. One such DM is the answer, however long ago they spoke; with two
        or more, the one who spoke last within the answer window (a tie goes to the lower id);
        if none spoke that recently, no one."""
        here = {u: t for u, t in self._dm_spoke.items() if u != caller and u in self._dm_ids}
        if len(here) == 1:
            return next(iter(here))
        recent = {u: t for u, t in here.items() if 0 <= at_ms - t <= ANSWER_WITHIN_MS}
        if not recent:
            return None
        return max(recent, key=lambda u: (recent[u], -u))

    def dm_addressed(self) -> dict[int, int]:
        """How often each DM was called by a DM word or by their own name this session. Only
        this learner keeps it (in memory, for the session); nothing reads it yet, and nothing is
        written to the story graph (#1547)."""
        return dict(self._dm_called)

    def feed(self, speaker: int, at_ms: int, text: str, *, consented: bool) -> None:
        """The next line of the session, in the order heard (a late line only loses a pair,
        never invents one). A line from someone who didn't agree is never read and breaks the
        pair, as any third voice would."""
        before, self._previous = self._previous, None
        if not consented:
            return
        if speaker in self._dm_ids:
            self._dm_spoke[speaker] = at_ms
        if (
            before is not None
            and before[0] != speaker
            and 0 <= at_ms - before[1] <= ANSWER_WITHIN_MS
            and is_answer(text)
        ):
            for name in before[2]:
                self._count(name, speaker)
        names = []
        for word in addressed(text):
            if is_dm_word(word) or name_key(word) in self._dm_keys:
                # "DM, can I roll?" or "Sam, can I roll?" (Sam runs the game): it means the DM who
                # is here. It is counted for that DM and is never learned as anyone's nickname.
                who = (
                    self._dm_keys[name_key(word)]
                    if name_key(word) in self._dm_keys
                    else self.session_dm(speaker, at_ms)
                )
                # Only a DM who has spoken here (so agreed to be recorded) is counted, whichever
                # way they were called, and never for themselves.
                if who is not None and who != speaker and who in self._dm_spoke:
                    self._dm_called[who] += 1
            elif name_key(word) not in _NOT_NAMES and not_names.keep(word, text):
                names.append(word)
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
        self._dm_spoke.pop(user_id, None)
        self._dm_called.pop(user_id, None)
        for key in [k for k in self._counts if k[1] == user_id]:
            del self._counts[key]
        kept = {k[0] for k in self._counts}  # a name only they said is forgotten too
        self._display = {k: v for k, v in self._display.items() if k in kept}
        if self._previous is not None and self._previous[0] == user_id:
            self._previous = None
