"""Descriptions are not names (#3431): "the woman", "beggar woman" and "a hooded figure" say what
someone is, not who, so no other name made of one may place a line or a connection on its own.
After a merge, "the woman" would otherwise match every woman in the story. Pure.

A spelling is a description when it has no capitalised word once a leading article or owner word is
off it. The entry keeps the spelling (it shows on the entry and in "Where was it said?"); only the
matching leaves it out. A DM who types an other name by hand still gets it matched: that is a
choice, not a guess."""

from __future__ import annotations

from dmbot.memory.scan import GAME_TERMS
from dmbot.memory.short_names import CREATURE_KINDS

_LEADING = frozenset({"the", "a", "an", "my", "our", "his", "her", "their", "your", "this", "that"})


def is_description(text: str) -> bool:
    """No proper-noun word in it: every word after a leading article or owner is lower case."""
    words = [w for w in text.replace("'s", " ").split() if w[:1].isalpha()]
    while words and words[0].casefold() in _LEADING:
        words = words[1:]
    # (any capital in a word makes it a name: "d'Artagnan", "al-Rashid"; a caseless script has none
    # to find, so it is never taken for a description)
    return bool(words) and all(
        any(c.islower() for c in w) and not any(c.isupper() for c in w) for w in words
    )


def matches_alone(text: str, kind: str, source: str) -> bool:
    """Whether another name may place a line or a connection by itself. A description may not,
    unless the DM typed it by hand (any kind). A caller that does not know the source passes ""
    and gets the strict answer, so the safe side wins. A real name written all in lower case is
    taken for a description until the DM types it by hand; Title Case descriptions ("Beggar Woman")
    are not caught, a limit of looking at capitals only."""
    if not is_description(text):
        return True
    return source == "dm"


# The last word of a description that names a person ("the woman", "a hooded figure"). Short and
# plain on purpose: no race, class or job ("a dwarf", "a wizard", "a guard" say what someone is, and
# "Bob is a dwarf" is not a tie to a person), and no thing or place ("the cave").
PERSON_WORDS = frozenset(
    [
        "man",
        "woman",
        "girl",
        "boy",
        "lady",
        "gentleman",
        "stranger",
        "figure",
        "child",
        "baby",
        "infant",
        "beggar",
        "traveler",
        "traveller",
        "wanderer",
        "hermit",
        "villager",
        "peasant",
        "maid",
        "servant",
        "crone",
        "hag",
        "widow",
        "orphan",
        "prisoner",
        "captive",
        "messenger",
        "courier",
        "mother",
        "father",
        "sister",
        "brother",
        "son",
        "daughter",
        "sibling",
        "patron",
        "attacker",
    ]
)
# A class word names a person only with a word before it that says which one ("maul barbarian": the
# one with the maul, #3634); alone it is a kind of person ("barbarians"), not someone.
PERSON_WORDS_WITH_A_MODIFIER = frozenset(
    ["barbarian", "raider", "bandit", "cultist", "assassin",
     # (#3736) a job names one person only when the description says which one: "the guard at the
     # gate", "the red-haired barmaid"; alone ("a guard") it says what someone is, not who
     "barmaid", "guard", "innkeeper", "sailor", "soldier", "merchant", "priest", "priestess",
     "monk", "knight", "hunter", "miner", "fisherman", "smith", "blacksmith", "bard", "cook",
     "farmer", "shopkeeper", "trader", "guide", "driver", "rider", "captain", "sergeant",
     "dockhand", "smuggler", "thug", "mercenary", "noble", "nobleman", "noblewoman", "drunk",
     "elder", "healer", "acolyte", "pilgrim", "warrior", "worker", "clerk", "scribe"]
)  # fmt: skip


# The words that start the tail of a description: "the barmaid with red hair", "the guard at the
# gate", "the sailors sitting in the corner". What the description is about is the word before the
# tail (its head), so "hair" never makes "the barmaid with red hair" a thing (#3736).
_TAIL_STARTS = frozenset(
    ["with", "at", "in", "on", "by", "near", "from", "of", "who", "whom", "that", "sitting",
     "standing", "wearing", "carrying", "holding"]
)  # fmt: skip


# Words before a head that say how many or which share, never which ones ("all the guards", "these
# people", "the other men"): a crowd, not a particular group (#3736).
_NOT_WHICH = frozenset(
    ["all", "some", "most", "many", "few", "several", "other", "others", "these", "those", "both",
     "every", "each", "any", "no", "more", "local", "two", "three", "four", "five", "six", "seven",
     "eight", "nine", "ten", "dozen", "hundred", "half"]
)  # fmt: skip


def _words(text: str) -> list[str]:
    return [w for w in text.replace("'s", " ").split() if w[:1].isalpha()]


def _split(text: str) -> tuple[list[str], list[str]]:
    """(the head phrase, the tail) of a description: "the barmaid" and "with red hair". The tail
    may hold a name ("at the Three Flags"): only the head phrase must be a description."""
    words = _words(text)
    tail = next((i for i, w in enumerate(words) if i and w.casefold() in _TAIL_STARTS), None)
    return (words, []) if tail is None else (words[:tail], words[tail:])


# Kinds of creature and people, lower case (the species and classes the table says, and kinds of
# creature): a capital on one BEFORE the head word is no proper name (#3431).
_KINDS = frozenset(GAME_TERMS | CREATURE_KINDS)


def _kind_words_lowered(head: list[str]) -> list[str]:
    """A capitalised kind of creature or people before the head word is no proper name ("the
    Minotaur captain", "the Dwarf smith", #3431): it is read in lower case, so the description is
    still found. The head word itself keeps its capital: "the Hag" or "the Bard of Waterdeep" is
    how someone is known, a title, and stays a name for the DM. Only the described-person and group
    checks read this; `is_description` (another name matching alone) still goes by capitals."""
    return [w.casefold() if w.casefold() in _KINDS else w for w in head[:-1]] + head[-1:]


def _head(head: list[str], tail: list[str], *, group: bool = False) -> tuple[str, bool] | None:
    """The head word ("barmaid" in "the barmaid with red hair") and whether something says which
    one: a word before it that is not an article, owner or a word of how many ("red-haired
    barmaid"), or a tail ("with red hair"; for a group, never "of": "the women of the town" is a
    crowd). None when the head phrase is no description: a capital in it ("Mira's sister Ren"), or
    an article in its middle ("bram the guard": a name and a job, never a description)."""
    head = _kind_words_lowered(head)
    if not head or not is_description(" ".join(head)):
        return None
    if any(w.casefold() in ("the", "a", "an") for w in head[1:]):
        return None
    before = [w for w in head[:-1] if w.casefold() not in _LEADING]
    which = any(w.casefold() not in _NOT_WHICH for w in before) if group else bool(before)
    told = bool(tail) and not (group and tail[0].casefold() == "of")
    return head[-1].casefold(), which or told


def is_described_person(text: str) -> bool:
    """A description whose head word is a person word: someone the story never names (#3431).
    The head is the word before a tail, so "the barmaid with red hair" is a person (#3736)."""
    found = _head(*_split(text))
    if found is None:
        return False
    last, which = found
    return last in PERSON_WORDS or (last in PERSON_WORDS_WITH_A_MODIFIER and which)


# The head words of a particular group the story never names ("the loud sailors in the corner",
# owner, #3736): people in the plural ("people" and "folk" are too general to be one). A group word
# alone ("the sailors", "men"), or with only a word of how many ("all the guards"), is a crowd or a
# kind of people, not one group: it needs a word or a tail that says which ones.
GROUP_WORDS = frozenset(
    ["men", "women", "strangers", "figures", "travelers", "travellers",
     "villagers", "peasants", "children", "sailors", "soldiers", "guards", "riders", "pilgrims",
     "bandits", "raiders", "cultists", "thugs", "mercenaries", "hunters", "miners", "fishermen",
     "merchants", "traders", "drinkers", "gamblers", "dancers", "musicians", "monks", "priests",
     "acolytes", "knights", "warriors", "workers", "dockhands", "smugglers", "brothers",
     "sisters", "twins"]
)  # fmt: skip


def is_described_group(text: str) -> bool:
    """A description of one particular group of people the story never names (#3736): its head
    word is people in the plural, with a word before it or a tail that says which ones ("the loud
    sailors in the corner", "three hooded figures"), never the word alone ("the sailors")."""
    found = _head(*_split(text), group=True)
    return found is not None and found[0] in GROUP_WORDS and found[1]


def head_phrase(text: str) -> str:
    """The words before a description's tail: "the barmaid" of "the barmaid with red hair"."""
    return " ".join(_split(text)[0])


def is_described(text: str) -> bool:
    """A person or a particular group the story describes but never names (#3431, #3736)."""
    return is_described_person(text) or is_described_group(text)
