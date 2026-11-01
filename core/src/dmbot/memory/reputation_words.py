"""How one character feels about another, as the readers say it (#3175, owner 2026-11-01).

Friend and enemy are no connection kinds any more: two characters just KNOW each other, and how
well and how kindly is a reputation (the scale DMbot calls Standing today, -100 to 100, whose five
steps the DM sets: Hostile -80, Unfriendly -40, Wary 0, Friendly 40, Devoted 80; `standing.py`).
A reader (the lore read or the session reader) returns one of NINE plain words for the feeling,
never a number, and this module is the one place the words become the DM's stored steps. The word
is only a proposal: it waits with the connection and is stored by the DM's press, and a standing
the DM set always wins (`standing.DM_WEIGHT`). Pure; nothing here touches the story.
"""

from __future__ import annotations

import re

from dmbot.memory import standing

SWORN_ENEMY, HOSTILE, DISTRUSTS, WARY, NEUTRAL = (
    "sworn enemy", "hostile", "distrusts", "wary", "neutral",
)  # fmt: skip
TRUSTS, FRIENDLY, CLOSE, DEVOTED = "trusts", "friendly", "close", "devoted"

# The nine words, worst first. They are what the readers' prompts offer.
WORDS = (SWORN_ENEMY, HOSTILE, DISTRUSTS, WARY, NEUTRAL, TRUSTS, FRIENDLY, CLOSE, DEVOTED)

# Nine words onto the five steps. "neutral" is no step: they merely know each other, and "no
# opinion yet" is how DMbot already shows that, so nothing is stored for it. "wary" keeps the
# meaning of the scale's own middle step (0).
_STEP = {
    SWORN_ENEMY: standing.HOSTILE,
    HOSTILE: standing.HOSTILE,
    DISTRUSTS: standing.UNFRIENDLY,
    WARY: standing.WARY,
    TRUSTS: standing.FRIENDLY,
    FRIENDLY: standing.FRIENDLY,
    CLOSE: standing.DEVOTED,
    DEVOTED: standing.DEVOTED,
}


def word_of(raw: object) -> str | None:
    """One of the nine words for what a reader returned, or None (a word off the list, or not a
    string): the caller drops it and counts it. Case, spaces, hyphens and underscores don't matter
    ("Sworn-Enemy" is "sworn enemy"); nothing is guessed from a near word."""
    if not isinstance(raw, str):
        return None
    word = re.sub(r"[\s_-]+", " ", raw).strip().casefold()
    return word if word in WORDS else None


def score_of(word: str) -> int | None:
    """The score the DM's step for this word is stored as, or None when nothing is stored
    (neutral, or a word off the list)."""
    step = _STEP.get(word)
    return standing.SCORE_OF[step] if step is not None else None


def step_of_word(word: str) -> str | None:
    """The DM's step name for the word ("Friendly"), or None as for `score_of`."""
    return _STEP.get(word)
