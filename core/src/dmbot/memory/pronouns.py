"""Who "I" and "my" are in a backstory (#2650). Pure.

A backstory written by a character says "my brother Ulric" and "I serve the Order". DMbot may not
guess whose backstory it is: the DM says so once when they upload it ("Whose backstory is this?"),
or the connections that need it are left out and counted. "I", "my", "me" then mean the chosen
character. "his" and "her" are never read as the character (a pick is only asked for in a
first-person text, where they mean someone else); a third-person pick can come later.
"""

from __future__ import annotations

import re

from dmbot.memory.models import name_key

FIRST = frozenset({"i", "me", "my", "mine", "myself"})
# The words that stand for a name already said (#2757): a quote is widened to the sentence before
# only when it has one, and a "Which one?" about one of them can never lose its connection (#2790).
PRONOUNS = frozenset(
    {"he", "she", "his", "her", "hers", "him", "himself", "herself", "they", "their", "theirs",
     "them", "its", "who", "whose", "whom"}
)  # fmt: skip


def is_pronoun(word: str) -> bool:
    return " ".join(word.split()).strip(".,;:!?\"'“”‘’()").casefold() in PRONOUNS


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


def speaker_end(end: str, speaker: str | None) -> tuple[str, bool]:
    """(the end to match, whether it is a pronoun nobody can resolve). With a chosen character
    "I", "me", "my", "mine" and "myself" are that character; with none, they are the unanswerable
    ones. "his" and "her" are never mapped (Supervisor, #2651): in a first-person text they mean
    someone else, so such an end is left to fail as any unknown name."""
    if is_first(end):
        return (speaker, False) if speaker else (end, True)
    return end, False
