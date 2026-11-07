"""Descriptions are not names (#3431): "the woman", "beggar woman" and "a hooded figure" say what
someone is, not who, so no other name made of one may place a line or a connection on its own.
After a merge, "the woman" would otherwise match every woman in the story. Pure.

A spelling is a description when it has no capitalised word once a leading article or owner word is
off it. The entry keeps the spelling (it shows on the entry and in "Where was it said?"); only the
matching leaves it out. A DM who types an other name by hand still gets it matched: that is a
choice, not a guess."""

from __future__ import annotations

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
PERSON_WORDS_WITH_A_MODIFIER = frozenset(["barbarian", "raider", "bandit", "cultist", "assassin"])


def is_described_person(text: str) -> bool:
    """A description whose last word is a person word: someone the story never names (#3431)."""
    if not is_description(text):
        return False
    words = [w for w in text.replace("'s", " ").split() if w[:1].isalpha()]
    if not words:
        return False
    last = words[-1].casefold()
    return last in PERSON_WORDS or (
        last in PERSON_WORDS_WITH_A_MODIFIER
        and len([w for w in words if w.casefold() not in _LEADING]) >= 2
    )
