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
from collections.abc import Collection, Mapping
from datetime import UTC, datetime

from dmbot.memory.models import name_key

# Every person or group the story never named still has a name (owner, 2026-11-07, #3736):
# "unnamed - " and the most distinguishing description the story gives ("unnamed - Ken's sister",
# "unnamed - barmaid at Three Flags Sailing with red hair"). Never a made-up name: DMbot never
# invents story. It replaces the old "<description> (name unknown)" label everywhere.
PREFIX = "unnamed - "
SOMEONE = PREFIX + "someone"  # one whose description this reader may not be told
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


# The label before #3736: "<description> (name unknown)". A chain held it twice ("Ken's sister (name
# unknown)'s son (name unknown)"), and an anchor cut to `ANCHOR_MAX` could leave it half there
# ("… (name unk's son (name unknown)"). Read only to bring old rows and old backups forward.
OLD_SUFFIX = " (name unknown)"
_OLD_WHOLE = re.compile(r" \(name unknown\)")
_OLD_CUT = re.compile(
    r" \(n(?:a(?:m(?:e(?: (?:u(?:n(?:k(?:n(?:o(?:w(?:n)?)?)?)?)?)?)?)?)?)?)?(?='s )"
)


def is_old_label(text: str) -> bool:
    """A name written in the old form, in a row or a backup from before #3736."""
    return text.endswith(OLD_SUFFIX)


def from_old_label(text: str) -> str:
    """ "Ken's sister (name unknown)'s son (name unknown)" as it is called now: "unnamed - Ken's
    sister's son". Migration 0378 does the same in SQL; a backup is brought forward with this."""
    return PREFIX + _OLD_CUT.sub("", _OLD_WHOLE.sub("", text))


def is_label(text: str) -> bool:
    """Is this an unnamed person's name ("unnamed - Ken's sister"), not a name the story gave?"""
    return text.startswith(PREFIX)


def description(text: str) -> str:
    """The description in an unnamed person's name ("Ken's sister"); any other text as it is."""
    return text[len(PREFIX) :] if is_label(text) else text


def label(what: str, anchor_name: str | None = None) -> str:
    """ "unnamed - Ken's sister", or "unnamed - a hooded figure" with nobody to hang them off. An
    anchor that is itself unnamed lends its description, so a chain reads "unnamed - Ken's
    sister's son", never "unnamed - unnamed - …"."""
    word = tidy(what)
    if anchor_name:
        anchor_name = description(anchor_name)
        whose = possessive(anchor_name[:ANCHOR_MAX].rstrip())
        return f"{PREFIX}{whose} {_ARTICLE.sub('', _without_owner(word, anchor_name))}"
    return f"{PREFIX}{word}"


def stored(what: str, anchor_name: str | None = None) -> str:
    """The copy kept in the name column (it must fit 100 characters and keep its "unnamed - ", so
    the word is shortened, never the prefix): lists that show the stored name stay right."""
    full = label(what, anchor_name)
    if len(full) <= STORED_MAX:
        return full
    room = STORED_MAX - len(label("", anchor_name)) - 1
    return label(tidy(what)[: max(room, 1)].rstrip(), anchor_name)


# The last resort that keeps a name unique (#3736). English month names, fixed, so the words are the
# same whatever the server's locale, and the same as migration 0378's SQL `Mon`.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_FIRST_SEEN = re.compile(r"\s*\(first seen [^)]*\)$")


def without_tag(what: str) -> str:
    """The description without its "(first seen …)": that tells two people apart in their name,
    but is no word of the story, so it never decides whether two descriptions are one person."""
    return _FIRST_SEEN.sub("", what)


def first_seen(seen_at: int, *, with_time: bool = False) -> str:
    """ "7 Nov 2026", or "7 Nov 2026 19:05": when they were first seen, in UTC so every reader of
    the name sees the same words."""
    when = datetime.fromtimestamp(seen_at, tz=UTC)
    day = f"{when.day} {_MONTHS[when.month - 1]} {when.year}"
    return f"{day} {when:%H:%M}" if with_time else day


def unique_what(what: str, anchor_name: str | None, taken: Collection[str], seen_at: int) -> str:
    """The description to keep, so the name is unique in the campaign (#3736). `taken` holds the
    `name_key` of every live unnamed name. When the story gives nothing more to tell two people
    apart, the last resort is when this one was first seen, from the session's own date, then its
    time (Supervisor's ruling on #3736): a fact of the table, never invented story. The words are
    cut to leave room for the tag inside the stored name, so the tag is never the part cut off."""
    word = tidy(what)
    if name_key(stored(word, anchor_name)) not in taken:
        return word
    room = min(MAX_WHAT, STORED_MAX - len(label("", anchor_name)) - 1)
    more = word
    for with_time in (False, True):
        tag = f" (first seen {first_seen(seen_at, with_time=with_time)})"
        more = word[: max(room - len(tag), 1)].rstrip() + tag
        if name_key(stored(more, anchor_name)) not in taken:
            return more
    return more  # (two in the same minute with the same words: a second look will ask the DM)


def same_person_key(what: str, role: str | None) -> str:
    """What makes two descriptions the same person when they hang off the same entry: the same
    relationship and the same word, ignoring case, an article and punctuation. "His other sister"
    is a different word, so it is a different person."""
    return f"{(role or '').casefold()}|{name_key(_ARTICLE.sub('', tidy(without_tag(what))))}"


def shown(what: str, anchor_id: str | None, names: Mapping[str, str]) -> str:
    """The label for display, with the anchor's name as it is now (`names`: entry id -> name). An
    anchor that cannot be found is left out rather than named wrongly."""
    return label(what, names.get(anchor_id) if anchor_id else None)
