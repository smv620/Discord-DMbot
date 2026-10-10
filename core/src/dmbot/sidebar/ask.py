"""Asking the DM sidebar out loud at the table (docs/PLAN.md, "DM sidebar"; #935, #1040). Pure:
no Discord, no database, no AI.

Two clear ways to ask. The DM says they need to look something up ("hold on, I need to find
if you need line of sight for fireball"): a hold-on lead-in, then "I need to / I have to / let
me", then find, look up or check, then something. Or the DM calls DMbot at the start of the
line ("Hey DMbot, what's the range of fireball?"), and the rest is the question. So "I need to
find the map" said on its own, "...and then DMbot said...", a player's line, or a line the
Cleaner tagged as in character never starts one. The phrases live here, in one place, with
tests.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass

# Said first, so the DM is clearly stepping out of the game for a moment.
_LEAD = (
    r"(?:hold on|hang on|hold up|one (?:sec|second|moment|minute)|just (?:a )?"
    r"(?:sec|second|moment|minute)|give me (?:a )?(?:sec|second|moment|minute))"
)
_FILLER = r"(?:sorry|okay|ok|so|um+|uh+|alright|right)"
_NEED = r"(?:i need to|i have to|i've got to|i gotta|i got to|let me|lemme)"
_VERB = r"(?P<verb>find(?: out)?|look up|check|look into)"
# Bounded repeats: a long or garbled line can't make the match slow (it is also cut to
# MAX_LINE_CHARS first).
_SEP = r"[\s,.;:!-]{1,6}"
_REQUEST = re.compile(
    rf"\b{_LEAD}{_SEP}(?:{_FILLER}{_SEP}){{0,2}}{_NEED}\s{{1,3}}{_VERB}\s{{1,3}}(?P<rest>.+)",
    re.IGNORECASE | re.DOTALL,
)
# What follows is about the DM's own things, not the game: "check my notes", "find the map",
# "check the module", "find the page" (housekeeping), or nothing in particular ("find it").
_NOT_A_LOOKUP = re.compile(
    r"^(?:my|our)\b"
    r"|^(?:the |this |that |a |an )?(?:map|maps|page|pages|module|modules|book|books|notes?"
    r"|sheet|sheets|screen|door|time|bathroom|snacks?|dice|pencil|pen|laptop|phone)\b",
    re.IGNORECASE,
)
_NOTHING_IN_PARTICULAR = frozenset(
    {"it", "that", "this", "something", "someone", "anything", "them", "those"}
    | {"these", "one", "there", "here"}
)
# How speech-to-text writes "DMbot": one letter-by-letter spelling per alternative, matched
# only at the very start of the line (so the name in the middle of a sentence never wakes it).
_NAME = (
    r"(?:d\.?\s?m\.?[\s-]?bot"  # DMbot, dmbot, DM bot, D.M. bot, D M bot, DM-bot
    r"|dee[\s,.-]{1,3}em[\s,.-]{1,3}bot"  # Dee em bot
    r"|the\s+m\s+bot)"  # "the M bot"
)
_WAKE = re.compile(
    rf"^\s{{0,3}}(?:(?:hey|hi|okay|ok)\b[\s,.!-]{{0,4}})?{_NAME}\b(?P<sep>[\s,.:;!?-]{{1,6}})(?P<rest>.+)",
    re.IGNORECASE | re.DOTALL,
)
# "DMbot said the goblin ..." (no comma) is a story about DMbot, not a question to it.
_STORY_VERBS = re.compile(
    r"^(?:said|says|told|tells|was|were|had|has|did|thinks|thought|wants|wanted|will|would)\b",
    re.IGNORECASE,
)
MIN_WORDS = 1
MAX_QUESTION_CHARS = 240
MAX_LINE_CHARS = 600  # a line is cut here before looking: speech never needs more
# The safety limit on questions (#1040), per listening session: a clear wake phrase makes an
# accidental question unlikely, so the old one-a-minute limit is gone, but a looping or stuck
# line must not be able to run up AI cost.
ASK_PER_MINUTE = 6
ASK_PER_SESSION = 120


@dataclass(frozen=True, slots=True)
class Request:
    verb: str  # "find", "find out", "look up", "check", "look into"; "" after "Hey DMbot"
    rest: str  # what follows: "if you need line of sight for fireball"

    @property
    def question(self) -> str:
        """What goes to the answer engine and the transcript: "find if you need line of
        sight for fireball", or just the question after "Hey DMbot"."""
        return f"{self.verb} {self.rest}".strip()


def request_in(text: str, *, in_character: bool = False) -> Request | None:
    """The look-up the DM asked for in this line, or None. `in_character`: the line is an
    NPC's or a character's (the Cleaner tagged it so): never a request."""
    if in_character:
        return None
    woken = _WAKE.match(text[:MAX_LINE_CHARS])
    if woken is not None:
        return _called(woken)
    found = _REQUEST.search(text[:MAX_LINE_CHARS])
    if found is None:
        return None
    rest = found.group("rest").strip()
    rest = re.split(r"(?<=[.!?])\s", rest, maxsplit=1)[0]  # up to the end of the sentence
    rest = rest.strip(" ,;:-.!?")
    words = rest.split()
    if len(words) < MIN_WORDS or _NOT_A_LOOKUP.match(rest):
        return None
    if len(words) == 1 and words[0].casefold() in _NOTHING_IN_PARTICULAR:
        return None  # a one-word topic ("fireball") is fine; "it" is not
    if len(rest) > MAX_QUESTION_CHARS:
        rest = rest[:MAX_QUESTION_CHARS].rsplit(" ", 1)[0]
    return Request(found.group("verb").casefold(), rest)


def _called(woken: re.Match[str]) -> Request | None:
    """The question after "Hey DMbot, ...", or None if there is none (a bare "Hey DMbot", one
    word like "it") or the line is a story about DMbot."""
    rest = woken.group("rest").strip()
    if not re.search(r"[,:;.!?-]", woken.group("sep")) and _STORY_VERBS.match(rest):
        return None
    rest = rest.strip(" ,;:-.!?")
    words = rest.split()
    if len(words) < MIN_WORDS:
        return None
    if len(words) == 1 and words[0].casefold() in _NOTHING_IN_PARTICULAR:
        return None
    if len(rest) > MAX_QUESTION_CHARS:
        rest = rest[:MAX_QUESTION_CHARS].rsplit(" ", 1)[0]
    return Request("", rest)


class AskLimiter:
    """The safety limit on one listening session's questions, spoken and typed alike: at most
    `per_minute` in any minute and `per_session` in all. It remembers only the last
    `per_minute` times and a count, and a refused question is not counted."""

    __slots__ = ("_count", "_per_minute", "_per_session", "_recent")

    def __init__(
        self, per_minute: int = ASK_PER_MINUTE, per_session: int = ASK_PER_SESSION
    ) -> None:
        self._per_minute = per_minute
        self._per_session = per_session
        self._recent: deque[float] = deque(maxlen=per_minute)
        self._count = 0

    def allow(self, now: float) -> bool:
        if self._count >= self._per_session:
            return False
        if len(self._recent) == self._per_minute and now - self._recent[0] < 60.0:
            return False
        self._recent.append(now)
        self._count += 1
        return True
