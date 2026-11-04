"""A person the story describes but never names (#2790, section 3). Pure.

"Ken went to meet his sister at the lake" says Ken has a sister and nothing about who she is.
The relationship is a stated fact, so it is kept, with a new entry for her that has no name: it
hangs off Ken (`anchor`), the story's word for her is "sister" (`what`) and the relationship is
sibling (`role`). The label shown to the DM is built from those parts every time, so renaming or
merging Ken never leaves "Old Ken's sister" behind. Nothing here guesses a name: the DM gives one
later ("Give them a name"), and DMbot only ever proposes it as a question.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from dmbot.memory.models import name_key

SUFFIX = "(name unknown)"
NAME_FIRST = "Give them a name first: DMbot doesn't know who they are yet."
# Showing a connection to an end the story never named (#3426); the website says what to do.
# (`web_actions` matches this text to give the website its own refusal word: reword both together.)
NAME_BEFORE_SHOWING = "Give the unnamed person a name first, then show this connection."
STORED_MAX = 100  # the name column's limit
ANCHOR_MAX = 40  # a long anchor name is cut in the label, never the suffix
GROUP = "Not named yet"  # the heading on the name list (slice 4b)
MAX_WHAT = 100
MAX_ROLE = 40
_ARTICLE = re.compile(r"^(?:a|an|the)\s+", re.IGNORECASE)
# A description that already says whose it is ("his child", "Hobb's daughter") would be shown as
# "Hobb's Hobb's daughter": the label supplies the owner, so a leading owner is dropped.
_PRONOUN_OWNER = re.compile(r"^(?:his|her|their|its|my|our|your)\s+(?=\S)", re.IGNORECASE)
_POSSESSIVE_ENDS = ("'s ", "’s ", "' ", "’ ")
# A title is not the owner when it is only the first word of a longer name ("Lord's heir").
_TITLES = frozenset(
    [
        "the",
        "old",
        "lord",
        "lady",
        "sir",
        "king",
        "queen",
        "prince",
        "princess",
        "master",
        "captain",
        "brother",
        "sister",
        "father",
        "mother",
        "saint",
        "elder",
        "chief",
    ]
)


def tidy(what: str) -> str:
    """The story's word for them, in one line: "sister", "a hooded figure"."""
    return " ".join(what.split())[:MAX_WHAT].strip()


def possessive(name: str) -> str:
    return f"{name}'" if name.endswith(("s", "S")) else f"{name}'s"


def _without_owner(word: str, anchor_name: str) -> str:
    """`word` without a leading owner that is a possessive pronoun or the anchor's own name (or
    its first word: "Ivo's child" for "Ivo Rand"), compared to the name as the label
    shows it. Another owner stays: "the king's guard" is not "the guard"."""
    pronoun = _PRONOUN_OWNER.match(word)
    if pronoun is not None:
        return word[pronoun.end() :]
    shown = anchor_name[:ANCHOR_MAX].rstrip()
    first = shown.split()[0] if shown.split() else ""
    owners = {shown, "" if first.casefold() in _TITLES else first} - {""}
    folded = word.casefold()
    for owner in sorted(owners, key=len, reverse=True):
        for end in _POSSESSIVE_ENDS:
            if (
                folded.startswith((owner + end).casefold())
                and word[len(owner) + len(end) :].strip()
            ):
                return word[len(owner) + len(end) :]
    return word


def label(what: str, anchor_name: str | None = None) -> str:
    """ "Ken's sister (name unknown)", or "a hooded figure (name unknown)" with nobody to hang
    them off."""
    word = tidy(what)
    if anchor_name:
        whose = possessive(anchor_name[:ANCHOR_MAX].rstrip())
        return f"{whose} {_ARTICLE.sub('', _without_owner(word, anchor_name))} {SUFFIX}"
    return f"{word} {SUFFIX}"


def stored(what: str, anchor_name: str | None = None) -> str:
    """The copy kept in the name column (it must fit 100 characters and still end in the suffix,
    so the word is shortened, never the suffix): lists that show the stored name stay right."""
    full = label(what, anchor_name)
    if len(full) <= STORED_MAX:
        return full
    room = STORED_MAX - len(label("", anchor_name)) - 1
    return label(tidy(what)[: max(room, 1)].rstrip(), anchor_name)


def same_person_key(what: str, role: str | None) -> str:
    """What makes two descriptions the same person when they hang off the same entry: the same
    relationship and the same word, ignoring case, an article and punctuation. "His other sister"
    is a different word, so it is a different person."""
    return f"{(role or '').casefold()}|{name_key(_ARTICLE.sub('', tidy(what)))}"


def shown(what: str, anchor_id: str | None, names: Mapping[str, str]) -> str:
    """The label for display, with the anchor's name as it is now (`names`: entry id -> name). An
    anchor that cannot be found is left out rather than named wrongly."""
    return label(what, names.get(anchor_id) if anchor_id else None)
