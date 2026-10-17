"""Recognising "what's next?" in the DM sidebar (#1739; docs/PLAN.md, "DM sidebar"). Pure: no
Discord, no database, no AI. A strict list of whole questions, so nothing else is taken for it:
"what's next in initiative", "what time is it" and "if they go north, what's next" are not.

The question is first cleaned of the wake phrase and politeness ("hey DMbot", "please"), and what
is left must be exactly one of the phrases below, with or without "DMbot" in front.
"""

from __future__ import annotations

import re

_PHRASES = frozenset(
    {
        "what's next",
        "what is next",
        "what happens next",
        "what happens next in the adventure",
        "what happens next in the book",
        "what does the book have next",
        "what does the adventure have next",
        "where are we in the adventure",
        "where are we in the book",
        "where are we in the story",
        "where are we in the module",
        # "next in" only with a story word: "what's next in initiative" stays out.
        *(
            f"{lead} in the {noun}"
            for lead in ("what's next", "what is next", "what happens next", "what comes next")
            for noun in ("story", "adventure", "book", "module")
        ),
        # The next five are broad: "what comes next" can be about a turn or a spell. Remove any
        # that a live test shows catching the wrong question (the usual answer then takes over).
        "what comes next",
        "what's coming up",
        "what is coming up",
        "what's up next",
        "what's the next scene",
        "what's the next part",
        "what chapter are we in",
        "what chapter are we on",
        "what part of the book are we in",
        "what part of the adventure are we in",
        "what does the book say happens next",
        "what does the book say next",
    }
)
_FILLER = r"(?:(?:so|um+|uh+|and|alright|all right|okay|ok)\s+)"
_ASKING = r"(?:(?:can|could) you (?:tell|say) me|can you say|tell me)\s+"
_NAME = r"(?:(?:hey|hi|okay|ok)\s+)?(?:d\.?\s?m\.?[\s-]?bot|d m bot|deem bot|dee em bot)\s+"
_LEAD = re.compile(rf"^{_FILLER}*(?:{_NAME})?{_FILLER}*(?:{_ASKING})?")
_TAIL = re.compile(r"\s+(?:please|dmbot|dm bot|now|right now)$")


def normal(text: str) -> str:
    """Lower case, one apostrophe, no punctuation, single spaces."""
    text = text.casefold().replace("’", "'").replace("‘", "'")
    text = re.sub(r"[^\w\s']", " ", text)
    text = re.sub(r"\bwhats\b", "what's", text)
    return " ".join(text.split())


def is_whats_next(question: str) -> bool:
    """Is the whole question one of the phrases (after the wake phrase and politeness)?"""
    text = normal(question)
    for _ in range(3):  # "hey dmbot please what's next" and "what's next now dmbot please"
        text = _LEAD.sub("", text, count=1).strip()
        text = re.sub(r"^please\s+", "", text)
        text = _TAIL.sub("", text).strip()
    return text in _PHRASES
