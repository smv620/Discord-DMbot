"""Who "I", "my" and "his" are in a backstory (#2650). Pure.

A backstory written by a character says "my brother Ulric" and "I serve the Order". DMbot may not
guess whose backstory it is: the DM says so once when they upload it ("Whose backstory is this?"),
or the connections that need it are left out and counted. "I", "my", "me" then mean the chosen
character; "his" and "her" do too, only when the DM chose a character (they pick for a
third-person backstory the same way), and only as a whole end ("her" alone), never inside a
longer end like "her mother Mara".
"""

from __future__ import annotations

import re

from dmbot.memory.models import name_key

FIRST = frozenset({"i", "me", "my", "mine", "myself"})
THIRD = frozenset({"he", "she", "him", "her", "his", "hers"})
# "I" and "my" in the text: a capital I on its own, or my/me/mine/myself, or I'm, I've, I'd, I'll
_SAYS_I = re.compile(r"\b(?:I|[Mm]y|[Mm]e|[Mm]ine|[Mm]yself)\b|\bI['’](?:m|ve|d|ll)\b")
MAX_CHOICES = 24  # one select holds 25 choices; one is "Not one character's"
CHARACTER_TYPES = frozenset({"character", "npc", "player_character"})


def says_i(text: str) -> bool:
    """Whether the text speaks in the first person (so the DM is asked whose it is). A line of
    dialogue in a third-person text counts too: the question is optional, and never answering it
    only leaves those connections out."""
    return _SAYS_I.search(text) is not None


def is_first(end: str) -> bool:
    return name_key(end) in FIRST


def is_third(end: str) -> bool:
    return name_key(end) in THIRD


def speaker_end(end: str, speaker: str | None) -> tuple[str, bool]:
    """(the end to match, whether it is a pronoun nobody can resolve). With a chosen character
    "I", "my" and a bare "his"/"her" are that character; with none, "I" and "my" are the
    unanswerable ones; a bare "his" or "her" is left to fail as any unknown name."""
    if is_first(end):
        return (speaker, False) if speaker else (end, True)
    if is_third(end) and speaker:
        return speaker, False
    return end, False
