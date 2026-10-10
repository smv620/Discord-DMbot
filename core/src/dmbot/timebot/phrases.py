"""Rests said at the table (#965): a DM's clear "we take a short rest" or "you take a long
rest" moves the game clock. No AI: a small, strict phrase list, and a line that sounds like
a question, a wish or a maybe is never one. Only a campaign's DM's own lines are looked at
(the caller checks); players never move the clock.
"""

from __future__ import annotations

import re
from typing import Literal

Rest = Literal["short", "long"]

_SAYS = re.compile(
    r"\b(?:we|you|you all|you guys|everyone|the party|the group)\s+"
    r"(?:(?:all|now|then|each)\s+)?"
    r"(?:take|took|taking|finish|finished|have|had|settle in for|settled in for|get|got)\s+"
    r"(?:a|an|your)\s+(short|long)\s+rest\b",
    re.IGNORECASE,
)
# Words that make it a question, a wish, a condition or a refusal: not a rest that happened.
_NOT_A_REST = re.compile(
    r"\b(?:if|can|can't|cannot|couldn't|could|can not|don't|do not|won't|will not|unless|"
    r"want|wants|wanna|should|shouldn't|would|wouldn't|when|before|after|until|need|needs|"
    r"may|might|maybe|let's|lets|why|how|what|whether|try|tried|trying|no|not|never|"
    r"instead of|rather than)\b",
    re.IGNORECASE,
)
MAX_WORDS = 14  # a long ramble that mentions a rest isn't a ruling


def find(line: str) -> Rest | None:
    """ "short" or "long" if the line plainly says the party took that rest."""
    text = " ".join(line.split())
    if not text or "?" in text or len(text.split()) > MAX_WORDS:
        return None
    if _NOT_A_REST.search(text):
        return None
    found = _SAYS.search(text)
    if found is None:
        return None
    return "short" if found.group(1).lower() == "short" else "long"
