"""Asking the DM sidebar out loud at the table (docs/PLAN.md, "DM sidebar"; #935). Pure: no
Discord, no database, no AI.

The DM says they need to look something up ("hold on, I need to find if you need line of
sight for fireball") and the part after that becomes a sidebar question. Only a clear
request counts: a hold-on lead-in, then "I need to / I have to / let me", then find, look up
or check, then something. So "I need to find the map" said on its own, a player's line, or
a line the Cleaner tagged as in character never starts one. The phrases live here, in one
place, with tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Said first, so the DM is clearly stepping out of the game for a moment.
_LEAD = (
    r"(?:hold on|hang on|hold up|wait|one (?:sec|second|moment|minute)|just (?:a )?"
    r"(?:sec|second|moment|minute)|give me (?:a )?(?:sec|second|moment|minute)|sorry|okay|ok|so"
    r"|um+|uh+)"
)
_NEED = (
    r"(?:i need to|i have to|i've got to|i gotta|i got to|i want to|let me|lemme|i'm going to|i'll)"
)
_VERB = r"(?P<verb>find(?: out)?|look up|check|look into)"
_REQUEST = re.compile(
    rf"\b{_LEAD}[\s,.;:!-]+(?:{_LEAD}[\s,.;:!-]+)*{_NEED}\s+{_VERB}\s+(?P<rest>.+)",
    re.IGNORECASE | re.DOTALL,
)
# What follows is about the DM's own things, not the game ("check my notes", "find my dice").
_NOT_A_LOOKUP = re.compile(r"^(?:my|our|the (?:time|bathroom|snacks?))\b", re.IGNORECASE)
MIN_WORDS = 2
MAX_QUESTION_CHARS = 240
ASK_EVERY_S = 60.0  # at most one question a minute from a table


@dataclass(frozen=True, slots=True)
class Request:
    verb: str  # "find", "find out", "look up", "check", "look into"
    rest: str  # what follows: "if you need line of sight for fireball"

    @property
    def question(self) -> str:
        """What goes to the answer engine and the transcript: "find if you need line of
        sight for fireball"."""
        return f"{self.verb} {self.rest}"


def request_in(text: str, *, in_character: bool = False) -> Request | None:
    """The look-up the DM asked for in this line, or None. `in_character`: the line is an
    NPC's or a character's (the Cleaner tagged it so): never a request."""
    if in_character:
        return None
    found = _REQUEST.search(text)
    if found is None:
        return None
    rest = found.group("rest").strip()
    rest = re.split(r"(?<=[.!?])\s", rest, maxsplit=1)[0]  # up to the end of the sentence
    rest = rest.strip(" ,;:-.!?")
    if len(rest.split()) < MIN_WORDS or _NOT_A_LOOKUP.match(rest):
        return None
    if len(rest) > MAX_QUESTION_CHARS:
        rest = rest[:MAX_QUESTION_CHARS].rsplit(" ", 1)[0]
    return Request(found.group("verb").casefold(), rest)


class AskLimiter:
    """At most one question per `every` seconds for a table, so a rambling DM can't spend
    a pile of AI calls. Remembers only the last time."""

    __slots__ = ("_every", "_last")

    def __init__(self, every: float = ASK_EVERY_S) -> None:
        self._every = every
        self._last: float | None = None

    def allow(self, now: float) -> bool:
        if self._last is not None and now - self._last < self._every:
            return False
        self._last = now
        return True
