"""How one character feels about another, as the readers say it (#3175).

Two characters KNOW each other, and how well and how kindly is a reputation: the scale DMbot calls
Standing today, -100 to 100, whose five steps the DM sets (Hostile -80, Unfriendly -40, Wary 0,
Friendly 40, Devoted 80; `standing.py`). A reader (the lore read or the session reader) returns
one of NINE plain words for the feeling, never a number, and this module is the one place the
words become the DM's stored steps. The word is only a proposal: it waits with the connection and
is stored by the DM's press, and a standing the DM set always wins (`standing.DM_WEIGHT`).
Pure; nothing here touches the story.
"""

from __future__ import annotations

import re

from dmbot.memory import standing

SWORN_ENEMY, HOSTILE, DISTRUSTS, WARY, NEUTRAL = (
    "sworn enemy", "hostile", "distrusts", "wary", "neutral",
)  # fmt: skip
TRUSTS, FRIENDLY, CLOSE, DEVOTED = "trusts", "friendly", "close", "devoted"

# These are the readers' lowercase WORDS, not the scale's capitalised step names in `standing`
# ("wary" is a word; "Wary" is the step). The nine words, worst first by what they say; "wary"
# stores a real opinion (0) while "neutral" stores none, so those two are not ordered by score.
# They are what the readers' prompts offer.
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
    (neutral, or a word off the list). Takes a word already passed through `word_of`: it does no
    tidying of its own, so "Friendly" is off the list here."""
    step = _STEP.get(word)
    return standing.SCORE_OF[step] if step is not None else None


def step_of_word(word: str) -> str | None:
    """The DM's step name for the word ("Friendly"), or None as for `score_of` (also a word
    already passed through `word_of`)."""
    return _STEP.get(word)


def old_kind_of(word: str) -> str | None:
    """The kind an old friend or enemy was stored as that this feeling is the same fact as
    ("ally_of" for a good word, "enemy_of" for a bad one), or None for neutral or a word off the
    list. Rows stored before friend and enemy became knows (#3175) stay as they are, so a reader
    comparing a new connection with what the campaign holds asks this."""
    score = score_of(word)
    if score is None or score == 0:
        return None
    return "ally_of" if score > 0 else "enemy_of"


def opposite_old_kind_of(word: str) -> str | None:
    """The old kind that cannot stand beside this feeling ("enemy_of" against a good word)."""
    kind = old_kind_of(word)
    return None if kind is None else ("enemy_of" if kind == "ally_of" else "ally_of")


def sign_of(detail: str) -> int:
    """-1, 0 or 1 for a connection's detail read as a feeling: 0 for none, neutral or a word that
    is not a feeling. For comparing a held `knows` with a waiting one."""
    word = word_of(detail)
    score = score_of(word) if word is not None else None
    return 0 if not score else (score > 0) - (score < 0)


# The strong words need a cue in the line the reader quoted (#3606): a heated debate is "wary" at
# most, not "hostile". The cues are plain stems, read in the quote only (never in the reader's own
# word), so a model that over-reads is still held. They cannot read a negation ("never hated him"):
# that errs toward keeping the strong word, and the DM's Reputation has the last word anyway.
_CUES = {
    SWORN_ENEMY: re.compile(
        r"\bsworn\s+(?:enem|foe)|\bvow\w*\s+(?:to\s+)?(?:kill|destroy|avenge|against)|"
        r"\bnemesis|\barch-?enem|\bblood feud|\bvendetta",
        re.I,
    ),
    HOSTILE: re.compile(
        r"\bhate|\bhatr|\benem|\bfoe|\bhostil|\bnemesis|\bkill|\bmurder|\bfight(?!er)|"
        r"\bfought|\bwar\b|\battack|\bhunt(?!er)|\bdespis|\bloath|\bfeud|\brevenge|"
        r"\bvengeance|\bthreat|\bassault|\bbetray",
        re.I,
    ),
    DEVOTED: re.compile(r"\blove|\bloyal|\bdevot|\badore|\bworship|\bfaithful|\bfealty", re.I),
}
_DEMOTED = {SWORN_ENEMY: HOSTILE, HOSTILE: DISTRUSTS, DEVOTED: CLOSE}


def held_to_the_quote(word: str, quote: str) -> tuple[str, bool]:
    """(the word to keep, whether it was demoted): a strong word (sworn enemy, hostile, devoted)
    stays only when the reader's quote holds one of its cues, else it steps down toward the
    milder word, and that is checked in turn. Any other word is kept as it is."""
    demoted = False
    while word in _CUES and not _CUES[word].search(quote):
        word = _DEMOTED[word]
        demoted = True
    return word, demoted
