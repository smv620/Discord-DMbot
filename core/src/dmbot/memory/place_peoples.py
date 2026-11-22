"""A place's culture and its people (#4257, MapBot layers slice 3a; design
`docs/design/mapbot_layers.md` §6 and §7.2, Supervisor's ruling on #4257): the lineage words, the
share words, and the set phrases the lore reader turns into a culture or a population row. Pure:
no Discord, no database.

The value of both layers is a lineage: one of the 2024 Player's Handbook species the rules data
holds (`rules/data/srd52/origins.json`), or "other" with the text's own words, which only the DM
types. A named culture from #4229's catalogue comes in slice 3b. People see the plain adjective or
plural ("dwarven", "halflings"), never the word "species" or "lineage" (Supervisor's ruling).

What is read, and what never is:
- set phrases beside a named place only, the place the reader already chose: "the dwarven hold of
  Mithral Hall", "Mithral Hall, a dwarven hold", "Bree, home to many halflings";
- a culture from a settlement word ("an elven city"), "once" for ruins ("old dwarven ruins"), and
  the part of a place for a quarter or a district ("the dwarven quarter of Waterdeep");
- a population from who lives there, with its share word when one is said ("mostly halflings");
- never a number or a percentage (§10): "a town of 2,000 dwarves" keeps the dwarves and drops the
  number, with no share word;
- never from a character's own lineage: a dwarf living somewhere doesn't make it dwarven, so a
  phrase whose noun is a person ("a dwarven smith of Bree") says nothing about Bree;
- "some" and "traces" are the DM's words only: no phrase gives them;
- a named culture (#4257 slice 3b, Supervisor's ruling): the same culture shapes with a capitalised
  name where the lineage word goes ("the Illuskan town of Luskan", "old Netherese ruins"), only when
  the name is no entry the campaign knows, no lineage, and not on the stop list below of ordinary
  words that also come before a settlement word ("the Northern town of Bree" says nothing).
"""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import NamedTuple

from dmbot.memory import hedges

CULTURE, POPULATION = "culture", "population"
OTHER = "other"

# key → (adjective, plural): what the card says. The keys are the rules data's species, lowercased.
LINEAGES: dict[str, tuple[str, str]] = {
    "dragonborn": ("dragonborn", "dragonborn"),
    "dwarf": ("dwarven", "dwarves"),
    "elf": ("elven", "elves"),
    "gnome": ("gnomish", "gnomes"),
    "goliath": ("goliath", "goliaths"),
    "halfling": ("halfling", "halflings"),
    "human": ("human", "humans"),
    "orc": ("orcish", "orcs"),
    "tiefling": ("tiefling", "tieflings"),
}
# The shares (§6), as stored (one word: the column takes letters only) and as said. "all" is
# "almost entirely": nowhere is every last one of a people (Supervisor, #4413: share words go
# before the group, "almost entirely halflings").
CULTURE_HOWS = ("strong", "some", "traces", "once")
POPULATION_HOWS = ("all", "most", "many", "some", "few")
HOW_WORDS = {
    "all": "almost entirely",
    "most": "mostly",
    "many": "many",
    "some": "some",
    "few": "a few",
}

_ADJECTIVES = {adj: key for key, (adj, _) in LINEAGES.items()}
_PLURALS = {plural: key for key, (_, plural) in LINEAGES.items()}
# A settlement or a land, so the culture is the place's own; a quarter or a district is part of it
_SETTLEMENTS = ("city", "town", "village", "hold", "fortress", "stronghold", "settlement",
                "kingdom", "realm", "citadel", "port", "outpost")  # fmt: skip
_PARTS = ("quarter", "district", "ward")
_RUINS = ("ruins", "ruin")
_GONE = ("old", "ancient", "ruined", "abandoned", "former")
_SENTENCE_END = re.compile(r"[.!?;:\n]")


class Found(NamedTuple):
    """One row a phrase gives: its layer, lineage, share ('' when none was said) and part; for a
    named culture, `value` is '' and `culture` is its name as the text wrote it ("Illuskan")."""

    layer: str
    value: str
    how: str = ""
    part: str = ""
    culture: str = ""


# Capitalised words that come before a settlement word and are no culture: directions, sizes, ages,
# colours and other plain describing words. A missing culture is the safe side, so the list is wide.
STOP = frozenset(
    w.casefold()
    for w in (
        "North", "South", "East", "West", "Northern", "Southern", "Eastern", "Western",
        "Northeast", "Northwest", "Southeast", "Southwest", "Upper", "Lower", "Inner", "Outer",
        "Central", "Middle", "Far", "Near", "Great", "Grand", "Little", "Small", "Big", "High",
        "Low", "Long", "Old", "Ancient", "Elder", "New", "Young", "First", "Last", "Lost",
        "Forgotten", "Hidden", "Sunken", "Buried", "Fallen", "Ruined", "Abandoned", "Former",
        "Free", "Royal", "Imperial", "Holy", "Sacred", "Cursed", "Haunted", "Dark", "Bright",
        "Black", "White", "Red", "Blue", "Green", "Grey", "Gray", "Golden", "Silver", "Iron",
        "Stone", "Frozen", "Burning", "Broken", "Twin", "Walled", "Fortified", "Capital", "Port",
        "Border", "Frontier", "Mountain", "River", "Sea", "Forest", "Desert", "Island", "Hill",
        "Coastal", "Trade", "Trading", "Mining", "Fishing", "Farming", "Merchant", "Temple",
        "Market", "Dock", "Castle", "Field", "Noble", "Trades", "Harbor", "Harbour", "Crown",
        "Pirate", "Jewel", "Quiet", "Sleepy", "Busy", "Rich", "Poor", "Wealthy",
        "Elven", "Dwarven", "Human", "Gnomish", "Orcish", "Halfling", "Tiefling", "Goliath",
        "Dragonborn",
    )
)  # fmt: skip
_NAME = re.compile(r"[A-Z][a-z]+(?:['-][A-Za-z]+)?")


def culture_of_possessive(word: str) -> bool:
    """ "Smuggler's", "Kings'": whose it is, never a culture."""
    return word.endswith(("'s", "'S", "’s", "'"))


def culture_key(name: str) -> str:
    """A named culture's key, one per campaign: its words with any run of spaces as one, in lower
    case (migration 0496 makes the same key from 3a's "other" words)."""
    return " ".join(name.split()).lower()


def culture_name(name: str) -> str:
    """A named culture's name as kept: its words with any run of spaces as one."""
    return " ".join(name.split())


def words_of(value: str, words: str = "", *, plural: bool) -> str:
    """What the card says for a value: "dwarven" or "dwarves", or the DM's own words for other."""
    if value == OTHER or value not in LINEAGES:
        return words or value
    adjective, many = LINEAGES[value]
    return many if plural else adjective


def _clean(text: str) -> str:
    return " ".join(re.sub(r"[^\w'\s-]", " ", text).split()).casefold()


def _around(name: str, text: str) -> tuple[str, str] | None:
    """(the words just before the name, the words just after it), within its sentence, at its first
    whole mention: the place the reader already found."""
    found = re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.IGNORECASE)
    if not name.strip() or found is None:
        return None
    before = _SENTENCE_END.split(text[max(0, found.start() - 120) : found.start()])[-1]
    after = _SENTENCE_END.split(text[found.end() : found.end() + 160])[0]
    return before, after


def _culture(words: list[str]) -> Found | None:
    """A culture from "[an|the] [old] <adjective> <settlement|quarter|ruins>" (nearest first in
    `words` when read backwards is the caller's): None for any other shape."""
    if len(words) < 3 or words[0] not in ("a", "an", "the"):
        return None
    rest = words[1:]
    gone = bool(rest) and rest[0] in _GONE
    if gone:
        rest = rest[1:]
    if len(rest) < 2 or rest[0] not in _ADJECTIVES:
        return None
    key, noun = _ADJECTIVES[rest[0]], rest[1]
    if noun in _RUINS:
        return Found(CULTURE, key, "once")
    if noun in _PARTS:
        part = " ".join(
            words[: words.index(noun) + 1]
        )  # (the text's own words, "the dwarven quarter")
        return Found(CULTURE, key, "once" if gone else "strong", part)
    if noun in _SETTLEMENTS:
        return Found(CULTURE, key, "once" if gone else "strong")
    return None  # ("a dwarven smith": a person, never the place's culture)


# Who lives there, right after the place's name: "Bree, home to many halflings", "Bree is mostly
# halflings", "Bree, populated mainly by humans", "Bree, a town of 2,000 dwarves".
_SHARE = r"(?P<share>almost entirely|almost all|mostly|mainly|largely|many|a few)"
_PEOPLE = r"(?P<people>" + "|".join(sorted(_PLURALS, key=len, reverse=True)) + r")"
_LEAD = r"^,?\s*(?:(?:is|was|are|were)\s+)?"
# A bare share and plural needs "is" or "was": after a comma it is the next clause's subject
# ("After the battle at Bree, many dwarves fled north" says nothing of who lives in Bree)
_VERB = r"^\s*(?:is|was|are|were)\s+"
_POPULATION = (
    re.compile(_LEAD + rf"(?:home to|populated by|inhabited by)\s+(?:{_SHARE}\s+)?{_PEOPLE}\b"),
    re.compile(_LEAD + rf"(?:populated|inhabited)\s+{_SHARE}\s+by\s+{_PEOPLE}\b"),
    re.compile(_VERB + rf"{_SHARE}\s+{_PEOPLE}\b"),
    # (a number is never kept: "a town of 2,000 dwarves" says who, not how many)
    re.compile(
        _LEAD + r"an?\s+(?:" + "|".join(_SETTLEMENTS) + r")\s+of\s+(?:[\d,]+\s+)?" + rf"{_PEOPLE}\b"
    ),
)
# "<place> is a(n) …" or "<place> was a(n) …": the same culture shapes after "is" (Supervisor's
# ruling A on #4982); the article stays in what follows, as `_culture` reads it
_IS_A = re.compile(r"^\s+(?P<verb>is|was)\s+(?=(?:an?|the)\b)", re.I)
# A past state the text ends in the same clause ("… until the flood"): who lives there no longer.
# (`said` is casefolded already; re.I as its neighbours, so another caller is safe too, #4997)
_NO_LONGER = re.compile(r"\b(?:until|till|before)\b", re.I)
_SHARES = {
    "almost entirely": "all", "almost all": "all", "mostly": "most", "mainly": "most",
    "largely": "most", "many": "many", "a few": "few",
}  # fmt: skip


def _named(words: list[str], known: Collection[str]) -> Found | None:
    """A named culture from "[an|the] [old] <Name> <settlement|quarter|ruins>", in the text's own
    case: None unless the name is capitalised, no lineage, not on STOP and not in `known` (the
    campaign's entry names, casefolded)."""
    if len(words) < 3 or words[0].casefold() not in ("a", "an", "the"):
        return None
    rest = words[1:]
    gone = bool(rest) and rest[0].casefold() in _GONE
    if gone:
        rest = rest[1:]
    if len(rest) < 2 or _NAME.fullmatch(rest[0]) is None:
        return None
    if not rest[1][:1].islower() or culture_of_possessive(rest[0]):
        return None  # ("The Market Town of Bree": a heading's capitals; "Smuggler's": whose)
    culture, noun = rest[0], rest[1].casefold()
    lowered = culture.casefold()
    if lowered in STOP or lowered in known or lowered in _ADJECTIVES or lowered in _PLURALS:
        return None
    if lowered in LINEAGES:
        return None
    if noun in _RUINS:
        return Found(CULTURE, "", "once", culture=culture)
    if noun in _PARTS:
        part = " ".join(words[: words.index(rest[1]) + 1])  # ("the Illuskan quarter")
        return Found(CULTURE, "", "once" if gone else "strong", part, culture)
    if noun in _SETTLEMENTS:
        return Found(CULTURE, "", "once" if gone else "strong", culture=culture)
    return None


def _words(text: str) -> list[str]:
    """The text's words in their own case, punctuation off (so "Hall," is "Hall")."""
    return [w for w in (re.sub(r"[^\w'-]", "", w) for w in text.split()) if w]


def read(name: str, text: str, known: Collection[str] = ()) -> list[Found]:
    """The culture and the people the words right beside a place's name state, by the set phrases
    above only; at most one of each. Anything else says nothing: a missing row is the safe side.
    `known`: the campaign's entry names, so a named culture is never one of them. A sentence that
    asks, supposes or reports someone's view says nothing (`hedges`, #4982)."""
    first = re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.IGNORECASE)
    if first is not None and hedges.hedged(text, first.start(), first.end()):
        return []
    around = _around(name, text)
    if around is None:
        return []
    before, after = around
    known = {k.casefold() for k in known}
    out: list[Found] = []
    back = _clean(before).split()
    named = _words(before)
    # "the dwarven hold of Mithral Hall", "the dwarven quarter of Waterdeep": the phrase, then "of"
    if len(back) >= 4 and back[-1] == "of":
        for length in (4, 3):  # ("the old dwarven hold" before "the dwarven hold", then "of")
            found = _culture(back[-length - 1 : -1])
            if found is None and len(named) >= 4 and named[-1].casefold() == "of":
                found = _named(named[-length - 1 : -1], known)  # ("the Illuskan town of")
            if found is not None:
                out.append(found)
                break
    tail = _clean(after) if after.lstrip().startswith(",") else ""
    if tail and not out:  # "Mithral Hall, a dwarven hold"
        found = _culture(tail.split()[:4]) or _named(_words(after)[:4], known)
        if found is not None and not found.part:  # ("Waterdeep, the dwarven quarter": not said)
            out.append(found)
    linked = _IS_A.match(after)
    if linked is not None and not out:  # "Greywatch is an orcish fortress" (ruling A on #4982)
        rest = after[linked.end() :]
        found = _culture(_clean(rest).split()[:4]) or _named(_words(rest)[:4], known)
        if found is not None and not found.part:
            # ("was an elven town": once, as ruins are; the text says it is no longer so)
            out.append(found._replace(how="once") if linked["verb"] == "was" else found)
    said = " ".join(after.split()).casefold()
    for shape in _POPULATION:
        m = shape.match(said)
        if m is not None and _NO_LONGER.search(said, m.end()):
            break  # ("was mostly dwarves until the flood": not so now, #4982)
        if m is not None:
            share = m.groupdict().get("share")
            out.append(Found(POPULATION, _PLURALS[m["people"]], _SHARES.get(share or "", "")))
            break
    return out


__all__ = [
    "CULTURE", "CULTURE_HOWS", "HOW_WORDS", "LINEAGES", "OTHER", "POPULATION", "POPULATION_HOWS",
    "STOP", "Found", "culture_key", "read", "words_of",
]  # fmt: skip
