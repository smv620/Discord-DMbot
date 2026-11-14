"""How two people know each other, read from the words a connection was found in (#3881). Pure.

"Knows" between two people used to read as if they were familiar, when the story often says only
that one has heard of the other. Supervisor's ruling: one `knows` relationship, plus how, in its
own column: "met" (they have been face to face), "aware" (only heard of them: a tale, a rumour, a
vision) or "" (the story doesn't say). This reads it with fixed word lists run over the words the
connection came with: the lore's own quote, or the session reader's (an AI's) words for a claim.
The same words always give the same answer, and when unsure it gives "": a wrong "met" is the
worst mistake here, since only the DM can take it back down. So:

- only sentences that name both people are read, when their names are given;
- only words that say the two were together count as a meeting ("met", "spoke with", "crossed
  paths"); hiring, attacking or saving someone can be done from afar and is left out;
- "met with resistance" and "met his end" are no meeting;
- "never met" with no meeting word reads as aware; both in one sentence is unsure ("").
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
# "met" that is no meeting of two people: "met with resistance", "met his end".
_MET_NOT_A_MEETING = re.compile(
    r"\bmet\s+(?:with\s+(?:resistance|silence|approval|disapproval|success|failure|defeat)|"
    r"(?:his|her|their|its)\s+(?:end|fate|doom|death|match))\b",
    re.IGNORECASE,
)
# Face to face: they met, spoke, travelled or fought side by side.
_MET = re.compile(
    r"\b(?:met|meet|meets|face\s+to\s+face|introduc(?:ed|es|ing)|introductions|"
    r"crossed\s+paths|shook\s+hands|greet(?:s|ed)|befriend(?:s|ed)|"
    r"(?:spoke|speaks|talked|talks|chatted|argued|negotiated|bargained|dined|drank|sat|"
    r"travel+ed|fought)\s+(?:with|alongside|beside)|"
    # "a heated debate regarding mithril smelting with a dwarf named Brewner"
    r"(?:debate|argument|conversation|quarrel)\b[^.;!?]{0,80}?\bwith)\b",
    re.IGNORECASE,
)
# Knows of them without a meeting: told of them, a tale, a vision.
_AWARE = re.compile(
    r"\b(?:heard\s+(?:of|about|tales|stories|rumou?rs)|hears\s+(?:of|about)|"
    r"tales?\s+of|stories\s+(?:of|about)|rumou?rs?\s+(?:of|about|that)|"
    r"visions?|dream(?:s|ed|t)?\s+of|divination|divined|scry(?:ing|ed)?|"
    r"prophec(?:y|ies)|learn(?:s|ed|t)\s+(?:of|about)|knows?\s+of|told\s+(?:of|about))\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.;!?])\s+|\n+")


def _key(name: str) -> str:
    """The first word of a name, lower case: how a sentence is matched to a person."""
    parts = name.split()
    return parts[0].strip(".,;:!?\"'“”()").casefold() if parts else ""


def _about_both(text: str, a: str, b: str) -> str:
    """The sentences of the text that name both people (by their first words), or the whole text
    when no names are given or no sentence names both (a pronoun, a claim's own words)."""
    ka, kb = _key(a), _key(b)
    if not ka or not kb:
        return text
    both = [s for s in _SENTENCE.split(text) if ka in s.casefold() and kb in s.casefold()]
    return " ".join(both) or text


def how_from(*texts: str, a: str = "", b: str = "") -> str:
    """ "met", "aware" or "" from the words given (a quote, a reader's words for a claim). `a`
    and `b` are the two people's names, when known: only sentences naming both are read."""
    words = _about_both(" ".join(t for t in texts if t), a, b)
    words = _MET_NOT_A_MEETING.sub(" ", words)
    not_met, met = bool(_NOT_MET.search(words)), bool(_MET.search(_NOT_MET.sub(" ", words)))
    if not_met and met:
        return ""  # "never met the king, but met Bruno": unsure, so nothing is claimed
    if not_met:
        return KNOWS_AWARE
    if met:
        return KNOWS_MET
    if _AWARE.search(words):
        return KNOWS_AWARE
    return ""
