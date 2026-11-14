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
- "some" and "traces" are the DM's words only: no phrase gives them.
"""

from __future__ import annotations

import re
from typing import NamedTuple

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
# "almost all": nowhere is every last one of a people.
CULTURE_HOWS = ("strong", "some", "traces", "once")
POPULATION_HOWS = ("all", "most", "many", "some", "few")
HOW_WORDS = {"all": "almost all", "most": "mostly", "many": "many", "some": "some", "few": "a few"}

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
    """One row a phrase gives: its layer, lineage, share ('' when none was said) and part."""

    layer: str
    value: str
    how: str = ""
    part: str = ""


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
_POPULATION = (
    re.compile(_LEAD + rf"(?:home to|populated by|inhabited by)\s+(?:{_SHARE}\s+)?{_PEOPLE}\b"),
    re.compile(_LEAD + rf"(?:populated|inhabited)\s+{_SHARE}\s+by\s+{_PEOPLE}\b"),
    re.compile(_LEAD + rf"{_SHARE}\s+{_PEOPLE}\b"),
    # (a number is never kept: "a town of 2,000 dwarves" says who, not how many)
    re.compile(
        _LEAD + r"an?\s+(?:" + "|".join(_SETTLEMENTS) + r")\s+of\s+(?:[\d,]+\s+)?" + rf"{_PEOPLE}\b"
    ),
)
_SHARES = {
    "almost entirely": "all", "almost all": "all", "mostly": "most", "mainly": "most",
    "largely": "most", "many": "many", "a few": "few",
}  # fmt: skip


def read(name: str, text: str) -> list[Found]:
    """The culture and the people the words right beside a place's name state, by the set phrases
    above only; at most one of each. Anything else says nothing: a missing row is the safe side."""
    around = _around(name, text)
    if around is None:
        return []
    before, after = around
    out: list[Found] = []
    back = _clean(before).split()
    # "the dwarven hold of Mithral Hall", "the dwarven quarter of Waterdeep": the phrase, then "of"
    if len(back) >= 4 and back[-1] == "of":
        for length in (4, 3):  # ("the old dwarven hold" before "the dwarven hold", then "of")
            found = _culture(back[-length - 1 : -1])
            if found is not None:
                out.append(found)
                break
    tail = _clean(after) if after.lstrip().startswith(",") else ""
    if tail and not out:  # "Mithral Hall, a dwarven hold"
        found = _culture(tail.split()[:4])
        if found is not None and not found.part:  # ("Waterdeep, the dwarven quarter": not said)
            out.append(found)
    said = " ".join(after.split()).casefold()
    for shape in _POPULATION:
        m = shape.match(said)
        if m is not None:
            share = m.groupdict().get("share")
            out.append(Found(POPULATION, _PLURALS[m["people"]], _SHARES.get(share or "", "")))
            break
    return out


__all__ = [
    "CULTURE", "CULTURE_HOWS", "HOW_WORDS", "LINEAGES", "OTHER", "POPULATION", "POPULATION_HOWS",
    "Found", "read", "words_of",
]  # fmt: skip
