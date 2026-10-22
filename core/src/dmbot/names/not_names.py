"""Words that are never suggested as names, one list for every place DMbot finds names (#2358).

A capitalised pronoun at the start of a sentence ("We went in", "I'll look") is the usual way a
pronoun gets offered as a new name to check. So pronouns, their contractions (straight and curly
apostrophes) and, at the start of a sentence, a few words like "this", "someone" and "nobody" are
never names. The one exception is a plain naming cue in the same line, checked here in code: "my
name is We", "call me Us", "a gnome named She". The match is on whole words only, so Hermes, Ivy,
Usher and Theyra are untouched.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

PRONOUNS = frozenset(
    {
        *("i", "me", "my", "mine", "myself"),
        *("we", "us", "our", "ours", "ourselves"),
        *("you", "your", "yours", "yourself", "yourselves", "thou", "thee", "thy"),
        *("he", "him", "his", "himself"),
        *("she", "her", "hers", "herself"),
        *("it", "its", "itself"),
        *("they", "them", "their", "theirs", "themselves", "themself"),
        *("one", "oneself"),
        *("who", "whom", "whose"),
    }
)
# The words that do the acting in a sentence: a run they lead is not a name.
SUBJECTS = frozenset({"i", "we", "you", "he", "she", "it", "they", "who"})
# Said before a verb or "am": I'm, we'll, he's, they'd, it's, you've ... (and the same without the
# apostrophe, which speech to text drops: "im", "ill" is left out because it is a word).
_STEMS = (
    "i",
    "we",
    "you",
    "he",
    "she",
    "it",
    "they",
    "that",
    "there",
    "here",
    "who",
    "what",
    "let",
)
_ENDINGS = ("m", "ll", "d", "ve", "re", "s")
CONTRACTIONS = frozenset(
    f"{stem}'{end}"
    for stem in _STEMS
    for end in _ENDINGS
    if not (stem == "i" and end in ("s", "re"))
) | {"im", "ive", "id", "theyre", "youre", "weve", "theyll", "youll", "hes", "shes", "thats"}
# Words that only fail as a name when they start a sentence ("This is it", "Someone knocked").
SENTENCE_STARTERS = frozenset(
    {
        *("this", "that", "these", "those", "there", "here"),
        *("someone", "somebody", "something", "everyone", "everybody", "everything"),
        *("nobody", "noone", "nothing", "anyone", "anybody", "anything"),
    }
)

# Words an address ("Okay, Mike, ...") starts or ends with that are not a name: the table-names
# learner's own extras (#1188, #1208), kept here so there is one place to look.
ADDRESS_WORDS = frozenset(
    [
        "i",
        "ok",
        "okay",
        "so",
        "well",
        "yes",
        "no",
        "yeah",
        "hey",
        "hi",
        "hello",
        "right",
        "sure",
        "now",
        "then",
        "and",
        "but",
        "oh",
        "um",
        "uh",
        "alright",
        "all",
        "everyone",
        "everybody",
        "guys",
        "folks",
        "please",
        "thanks",
        "thank",
        "dm",
        "gm",
        "dungeon",
        "master",
        "game",
        "dmbot",
        "team",
        "listen",
        "look",
        "wait",
        "great",
        "good",
        "nice",
        "cool",
    ]
)
# Table talk spelled with letters that is not a name (acronyms the game uses, number words): the
# spelled-name reader's extras (#2168).
TABLE_TALK = frozenset(
    {
        *("npc", "pc", "pcs", "npcs", "bbeg", "tpk", "aoe", "dnd", "phb", "dmg", "xp", "hp"),
        *("ac", "dc", "dm", "gm", "ooc", "lfg", "ttrpg"),
        *("brb", "lol", "omg", "wtf", "tbh", "btw", "idk", "afk", "gg", "irl"),
        *("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"),
    }
)

_CURLY = str.maketrans({"’": "'", "‘": "'", "`": "'", "´": "'"})
_GAP = re.compile(r"\s+")


def _plain(word: str) -> str:
    return word.translate(_CURLY).strip(" .,;:!?\"“”()[]'").lower()


def _first(candidate: str) -> str:
    parts = _GAP.split(candidate.strip(), maxsplit=1)
    return _plain(parts[0]) if parts and parts[0] else ""


def _blocked_word(word: str, *, sentence_start: bool) -> bool:
    if word in PRONOUNS or word in CONTRACTIONS:
        return True
    return sentence_start and word in SENTENCE_STARTERS


def is_not_name(candidate: str, *, sentence_start: bool = False) -> bool:
    """Whether this candidate is a pronoun, a contraction or one of these words, or begins with
    one ("We Should", "He Said"). `sentence_start`: it began a sentence, so "This" counts too.
    Not the exception: see `keep`."""
    if not candidate or not candidate.strip():
        return False
    words = _GAP.split(candidate.strip())
    first = _plain(words[0])
    if len(words) == 1:
        return _blocked_word(first, sentence_start=sentence_start)
    # "We Should", "He Said": a run led by a word that does the acting. Possessives and objects
    # ("Our Lady", "His Grace", "Mine Hollow") can lead a real name, so they only fail alone.
    return first in SUBJECTS or first in CONTRACTIONS


# "my/his/her/their/your/its/our name is X", "(I'm|he's|they're|…) called X", "call me/him/… X",
# "named X", "goes by X". X is the one word straight after the cue.
_X = r"[\"“‘']?(?P<x>[^\W\d_][\w'’-]*?)(?:['’][\"”’]?)?(?![\w'’-])"
_CUES = [
    re.compile(p, re.IGNORECASE)
    for p in (
        rf"\b(?:my|his|her|their|your|its|our|whose)\s+name\s+(?:is|was)\s+{_X}",
        rf"\b(?:i['’]m|i\s+am|he['’]s|he\s+is|she['’]s|she\s+is|they['’]re|they\s+are|"
        rf"we['’]re|we\s+are|it['’]s|it\s+is|you['’]re|you\s+are)\s+called\s+{_X}",
        rf"\bcall(?:s|ed)?\s+(?:me|him|her|them|us|it)\s+{_X}",
        rf"\bnamed\s+{_X}",
        rf"\bgoes\s+by\s+{_X}",
    )
]


_NAMED = 3
_LOOSE = (2, _NAMED)  # the cues that take whatever word follows
_OBJECTS = frozenset({"me", "him", "her", "them", "it", "you"})
_FILLER = frozenset({"the", "a", "an", "after", "for", "by", "in", "on", "to", "of", "as"})


def cued_names(line: str) -> frozenset[str]:
    """The words a plain naming cue in this one line says are names, lower case."""
    found: set[str] = set()
    for at, pattern in enumerate(_CUES):
        for match in pattern.finditer(line or ""):
            word = _plain(match.group("x"))
            # "They named him Bob", "He was named after the king": the word after "named" or
            # "call" is not always the name.
            if at in _LOOSE and (word in _FILLER or (at == _NAMED and word in _OBJECTS)):
                continue
            found.add(word)
    return frozenset(found)


def cued_in(text: str) -> frozenset[str]:
    """The same, over a whole transcript: a cue counts only within its own line."""
    found: set[str] = set()
    for line in (text or "").splitlines():
        found |= cued_names(line)
    return frozenset(found)


def allowed(candidate: str, cued: frozenset[str]) -> bool:
    """`keep` for a candidate whose cues were already gathered (`cued_in`)."""
    return not is_not_name(candidate) or _first(candidate) in cued


def keep(candidate: str, line: str = "", *, sentence_start: bool = False) -> bool:
    """Whether to offer this candidate as a name: yes unless it is one of the words above, and
    even then yes when the same line plainly names someone by it ("My name is We")."""
    if not is_not_name(candidate, sentence_start=sentence_start):
        return True
    return _first(candidate) in cued_names(line)


def filtered(candidates: Iterable[str], line: str = "") -> list[str]:
    """The candidates that may be offered, in order."""
    return [c for c in candidates if keep(c, line)]
