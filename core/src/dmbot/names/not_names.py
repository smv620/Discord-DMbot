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


# ---- shapes that are no name at all (#3394, #3385) ---------------------------------------------
# The session reader sometimes hands over a number, a measure, a time, the weather or a form of
# address as a subject or object. These are decided by their shape, not by capitals, so they are
# never asked about. Counts only: the caller logs the reason key, never the words.
NUMBER_WORDS = frozenset(
    {
        *("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"),
        *("eleven", "twelve", "twenty", "thirty", "fifty", "hundred", "thousand", "dozen"),
        *("half", "both", "several", "few", "many", "some", "any", "most", "each", "every"),
        *("first", "second", "third", "fourth", "fifth", "last", "next", "another", "other"),
    }
)
UNITS = frozenset(
    {
        *("mile", "miles", "hour", "hours", "minute", "minutes", "day", "days", "night", "nights"),
        *("foot", "feet", "yard", "yards", "inch", "inches", "gold", "silver", "copper", "gp"),
        *("sp", "cp", "pound", "pounds", "week", "weeks", "month", "months", "year", "years"),
        *("mph", "round", "rounds", "turn", "turns", "ago", "per"),
    }
)
TIME_WORDS = frozenset(
    {*("afternoon", "morning", "evening", "midnight", "noon", "dawn", "dusk", "tonight")}
)
# A form of address or a title said alone. "Captain Vel", "Speaker Masthew" and "Sister Nora" are
# names and are not caught: only the whole candidate being one of these is.
ADDRESS_ALONE = frozenset(
    {
        *("friend", "friends", "stranger", "lad", "lass", "traveller", "traveler", "boss", "mate"),
        *("sir", "madam", "ma'am", "miss", "mister", "buddy", "pal", "chief", "kid"),
        *("captain", "father", "mother", "sister", "brother", "speaker", "lord", "lady", "master"),
        *("elder", "priest", "priestess", "your grace", "my lord", "my lady", "your majesty"),
        *("your highness", "good sir", "young man", "old man"),
    }
)
# Titles and forms of address said before a name ("Lord Neverember", "Major Ursa", "Old Mother
# Ashe"): one list for every place that must not take such a word for the name itself, so the
# short-name rule (#3880) and the never-ask-about-a-title rule (#3385) never disagree. Single words,
# lower case; the one-word forms of ADDRESS_ALONE are all in it.
TITLE_WORDS = frozenset(
    {
        *(w for w in ADDRESS_ALONE if " " not in w),
        *("the", "old", "young", "high", "grand", "great", "little", "big", "good", "saint", "st"),
        *("king", "queen", "prince", "princess", "emperor", "empress", "regent", "chancellor"),
        *("duke", "duchess", "baron", "baroness", "count", "countess", "earl", "marquis", "jarl"),
        *("dame", "knight", "squire", "mistress", "lord", "lady", "sir", "madam", "mister", "miss"),
        *("mr", "mrs", "ms", "dr", "doctor", "professor", "sage", "archmage", "magister"),
        *("general", "commander", "major", "colonel", "sergeant", "lieutenant", "admiral"),
        *("marshal", "warden", "sheriff", "constable", "mayor", "speaker", "councillor"),
        *("chief", "chieftain", "elder", "abbot", "abbess", "bishop", "cardinal", "reverend"),
        *("friar", "brother", "sister", "father", "mother", "uncle", "aunt", "auntie", "cousin"),
        *("granny", "grandma", "grandpa", "grandmother", "grandfather", "captain", "master"),
    }
)


def is_title_word(word: str) -> bool:
    """Whether this one word is a title or a form of address (TITLE_WORDS), whatever its case or
    trailing full stop ("Dr.", "St.")."""
    return _plain(word) in TITLE_WORDS


_CLOCK = re.compile(r"\b\d{1,2}[:.]\d{2}\b|\b\d+\s*(?:am|pm|o'?clock)\b", re.IGNORECASE)
_ARTICLES = frozenset({"the", "a", "an", "of"})


def _words_of(candidate: str) -> list[str]:
    return [w for w in (_plain(x) for x in _GAP.split(candidate.strip())) if w]


# A clause the reader cut out of a sentence is no name (#3394): "twenty miles from Targos",
# "prophecy to Tavin", "in Elvish". A lower-case linking word after the first word, or one that
# starts it. "of" is left out on purpose ("Tower of Joy"), and "in", "at", "on", "by" and "for"
# count only at the start, so "Fort at the Pass" is a name.
CLAUSE_LINKS = frozenset(
    {*("to", "from", "past", "into", "toward", "towards", "who", "which", "that", "with", "about")}
)
CLAUSE_STARTS = CLAUSE_LINKS | frozenset({"in", "at", "on", "by", "for"})


def _clause(candidate: str) -> bool:
    tokens = candidate.split()
    if len(tokens) < 2:
        return False
    if tokens[0].islower() and _plain(tokens[0]) in CLAUSE_STARTS:
        return True
    return any(t.islower() and _plain(t) in CLAUSE_LINKS for t in tokens[1:])


def shape_of(candidate: str) -> str | None:
    """Why this candidate is no name by its shape alone: `number`, `measure`, `time`, `address` or
    `clause`;
    None when the shape says nothing. Capitals do not decide any of these."""
    words = _words_of(candidate)
    if not words:
        return None
    if " ".join(words) in ADDRESS_ALONE:
        return "address"
    if _clause(candidate):
        return "clause"
    # "Area 51", "Third Tower": a capitalised word that is not itself a number or unit word
    capitalised = any(
        w[:1].isupper() and _plain(w) not in NUMBER_WORDS | UNITS | TIME_WORDS
        for w in candidate.split()
    )
    if any(c.isdigit() for c in candidate) and not capitalised:
        return "time" if _CLOCK.search(candidate) else "number"
    raw = candidate.split()
    if _CLOCK.search(candidate) or any(
        _plain(w) in TIME_WORDS and w[:1].islower() for w in raw
    ):  # "in the afternoon"; "the Dawn Court" and a person called Dawn are names
        return "time"
    if words == ["current", "time"]:
        return "time"
    if capitalised:
        return None
    if len(words) == 1 and candidate[:1].isupper():
        return None  # a lone capitalised "Seven" or "Third" may be someone's name: ask
    if words[-1] == "time" and len(words) <= 2:
        return "time"
    if set(words) & UNITS and (words[0] in NUMBER_WORDS or "per" in words or "ago" in words):
        return "measure"
    if words[0] in NUMBER_WORDS:
        return "number"
    if words[0] == "a" and len(words) > 1 and words[1] in NUMBER_WORDS:
        return "number"  # "a few copper"
    return None


def _lower_everywhere(candidate: str, text: str) -> bool:
    """Is the candidate found in the text only ever spelled in lower case? Not found at all counts
    as no (it cannot be judged, so it is kept)."""
    words = _words_of(candidate)
    while words and words[0] in _ARTICLES:  # a leading "the" is not part of the name's spelling
        words.pop(0)
    if not words:
        return False
    spelled = re.compile(r"\b" + r"\W+".join(re.escape(w) for w in words) + r"\b", re.IGNORECASE)
    found = [m.group(0) for m in spelled.finditer(text or "")]
    return bool(found) and all(f == f.lower() for f in found)


def why_not(
    candidate: str,
    *,
    text: str = "",
    offered: int = 1,
    cued: frozenset[str] = frozenset(),
) -> str | None:
    """Why this candidate must not be asked about as a name, as a reason key for the log, or None.
    Shapes (number, measure, time, a lone form of address or title) decide by themselves. A
    common noun ("the dogs", "snow", "the lady") is dropped only when it is lower case in every
    cited line, the DM did not cue it as a name, fewer than two lines offer it, and the caller
    knows no entry by it (the caller passes only unknown names). Capitals are one sign, not the
    test: "Boy" capitalised, or "boy" offered by two lines, is kept."""
    shape = shape_of(candidate)
    if shape is not None:
        return shape
    words = _words_of(candidate)
    if not words or _first(candidate) in cued or any(w in cued for w in words):
        return None
    if offered < 2 and candidate == candidate.lower() and _lower_everywhere(candidate, text):
        return "common_noun"
    return None
