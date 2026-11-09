"""How two people know each other, read from the words a connection was found in (#3881). Pure.

"Knows" between two people used to read as if they were familiar, when the story often says only
that one has heard of the other. Supervisor's ruling: one `knows` relationship, plus how, in its
own column: "met" (they have been face to face), "aware" (a tale, a rumour, a letter, a vision) or
"" (the story doesn't say; shown as "knows of"). This reads it by fixed words, never by an AI, from
the quote a lore connection was found in or the session reader's own words for a claim, so the
same words always give the same answer and nothing is guessed: no cue, no answer. "Never met"
reads as aware, before any meeting word is looked for.
"""

from __future__ import annotations

import re

from dmbot.memory.models import KNOWS_AWARE, KNOWS_MET

# Said of two people who have not met: they know of each other at most.
_NOT_MET = re.compile(
    r"\b(?:never|not\s+yet|have\s+not|haven['’]t|has\s+not|hasn['’]t|had\s+not|hadn['’]t)"
    r"\s+(?:\w+\s+)?met\b",
    re.IGNORECASE,
)
# Face to face: a meeting, talk, a fight side by side or against each other, travel together.
_MET = re.compile(
    r"\b(?:met|meet|meets|meeting|face\s+to\s+face|introduc(?:e|ed|es|ing|tions?)|"
    r"crossed\s+paths|shook\s+hands|greet(?:s|ed)?|"
    r"(?:spoke|speaks|talked|talks|chatted|argued|negotiated|bargained|traded|fought|travel+ed|"
    r"worked|sat|dined|drank)\s+(?:with|to|alongside|beside|against)|"
    r"befriend(?:s|ed)?|hired|employs?|employed|rescued|saved|captured|attacked|ambushed|"
    # "a heated debate regarding mithril smelting with a dwarf named Brewner"
    r"(?:debate|argument|conversation|discussion|quarrel|talk)\b[^.!?]{0,80}?\bwith)\b",
    re.IGNORECASE,
)
# Knows of them without a meeting: told of them, a tale, a letter, a vision.
_AWARE = re.compile(
    r"\b(?:heard\s+(?:of|about|tales|stories|rumou?rs)|hears\s+(?:of|about)|"
    r"tales?\s+of|stories\s+(?:of|about)|rumou?rs?|legends?|reputation|"
    r"letters?\s+from|wrote\s+to|visions?|dream(?:s|ed|t)?\s+of|divination|divined|scry(?:ing|ed)?|"
    r"prophec(?:y|ies)|learn(?:s|ed|t)\s+(?:of|about)|knows?\s+of|word\s+of|"
    r"told\s+(?:of|about))\b",
    re.IGNORECASE,
)


def how_from(*texts: str) -> str:
    """ "met", "aware" or "" from the words given (a quote, a reader's words for a claim)."""
    words = " ".join(t for t in texts if t)
    if _NOT_MET.search(words):
        return KNOWS_AWARE
    if _MET.search(words):
        return KNOWS_MET
    if _AWARE.search(words):
        return KNOWS_AWARE
    return ""
