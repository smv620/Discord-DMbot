"""What the DM says that starts, moves or ends combat (#1127): no AI, a small and strict phrase
list, as in `phrases.py`. A line that sounds like a question, a wish, a condition or a maybe is
never one, and a player's words never reach here (the caller checks who is speaking).

Only whole lines count: "roll initiative" is an order, but "he'll roll initiative when the door
opens" is the story, and "the goblin dies next round" is a ruling about a monster, not the round
changing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from dmbot.timebot.clock import MAX_ROUND

Kind = Literal["start", "round", "next", "end"]

MAX_WORDS = 10  # an order, not a story
# Words that make a line a question, a wish, a condition, a refusal or a telling.
_NOT_A_THING = re.compile(
    r"\b(?:if|when|whenever|once|before|after|until|unless|can|can't|cannot|could|couldn't|"
    r"should|shouldn't|would|wouldn't|will|won't|don't|do not|not|never|no|maybe|might|may|"
    r"want|wants|wanna|need|needs|let's|lets|why|how|what|whether|try|tried|going|gonna|"
    r"said|says|told|tells|yesterday|ago|last|then)\b",
    re.IGNORECASE,
)
_LEAD = (
    r"^(?:(?:and|so|okay|ok|alright|all right|right|now|well|um|uh|everyone|everybody|folks|guys)"
    r"[, ]+){0,3}"
)
_START = re.compile(
    rf"{_LEAD}(?:(?:roll|make) (?:for )?initiative"
    r"|combat (?:starts|begins|starts now|begins now))[.!]?$",
    re.IGNORECASE,
)
_ONES = {
    w: n
    for n, w in enumerate(
        ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"], start=1
    )
}
_NUMBER = r"\d{1,2}|" + "|".join(_ONES)
_ROUND = re.compile(
    rf"{_LEAD}(?:(?:it'?s|it is|this is|we'?re in|we are in|now)\s+)?"
    rf"(?:(?:the\s+)?(?P<next>next|new)\s+round|round\s+(?P<n>{_NUMBER}))[.!]?$",
    re.IGNORECASE,
)
_END = re.compile(
    rf"{_LEAD}(?:combat(?:'?s| is| has)?\s+(?:over|ends?|ended|done)"
    r"|(?:the\s+)?(?:fight|battle)(?:'?s| is| has)?\s+(?:over|ends?|ended|done)"
    r"|end of (?:combat|the fight|the battle))[.!]?$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CombatSaid:
    """A combat change the DM clearly said. `number` is for "round" (a named round)."""

    kind: Kind
    number: int | None = None

    @property
    def key(self) -> str:
        """What the same thing said twice in a few seconds is counted once by."""
        return f"combat-{self.kind}-{self.number or ''}"


def find_combat(line: str) -> CombatSaid | None:
    """The combat change a DM's line plainly says, or None."""
    text = " ".join(line.split())
    if not text or "?" in text or len(text.split()) > MAX_WORDS or _NOT_A_THING.search(text):
        return None
    if _START.match(text):
        return CombatSaid("start")
    found = _ROUND.match(text)
    if found is not None:
        if found.group("next"):
            return CombatSaid("next")
        word = found.group("n").lower()
        number = int(word) if word.isdigit() else _ONES[word]
        return CombatSaid("round", number) if 1 <= number <= MAX_ROUND else None
    if _END.match(text):
        return CombatSaid("end")
    return None
