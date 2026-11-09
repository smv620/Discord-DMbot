"""A character's lineage and class, as a lore text states them (#2959 item 3, owner decision
2026-10-29). Pure: no AI, no database.

"Torvin is a dwarf", "a cleric of Lathander", "a half-elf paladin" are properties of the character,
not connections. Pass 1 lists each under a TRAITS heading as
`- <name> | lineage or class | <the text's words> | quote: "…"`. Every line is checked the way the
read's other lines are, so nothing is invented: the quote is in the text and holds the name (or a
word of it) and the trait's own words. A class is one of the classes the rules know by name; a
lineage is looked up in the rules data by the caller (`resolve`), and one the rules don't have keeps
the text's words, marked not known ("not in DMbot's rules"). A deity ("of Lathander") is not a
class: it stays a connection the relationships give. Counts only are logged.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass

from dmbot.memory import background as bg
from dmbot.memory.models import (
    CLASS,
    LINK_EDITIONS,
    LINK_SOURCE_MAX,
    SPECIES,
    Trait,
    name_key,
)

# The classes the rules give by name (SRD 5.2, CC-BY-4.0), plus the one a published adventure often
# names that the SRD lacks; "not in DMbot's rules" for that one. A word that is not one of these is
# not a class here ("a warrior", "a scholar" are descriptions, kept in the entry's own words).
SRD_CLASSES = (
    "Barbarian", "Bard", "Cleric", "Druid", "Fighter", "Monk", "Paladin", "Ranger", "Rogue",
    "Sorcerer", "Warlock", "Wizard",
)  # fmt: skip
OTHER_CLASSES = ("Artificer",)
CLASS_SOURCE = "SRD 5.2.1"  # where a class named by the rules is found
LINEAGE_WORDS = (
    3  # "half-elf", "wood elf", "hill dwarf": a lineage is a word or two, never a phrase
)
MAX_TRAITS = 120  # a guard against a runaway read: a document's own characters fit well inside it
_LINE = re.compile(
    r'^-\s*(?P<name>[^|]+?)\s*\|\s*(?P<kind>[^|]+?)\s*\|\s*(?P<words>[^|]+?)\s*\|\s*quote:\s*"(?P<quote>.+)"\s*$'
)
_KINDS = {"lineage": SPECIES, "species": SPECIES, "race": SPECIES, "class": CLASS}
_CLASSES = {c.casefold(): c for c in (*SRD_CLASSES, *OTHER_CLASSES)}
_WORD = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*")
_ARTICLES = frozenset({"a", "an", "the"})

# What the caller's rules lookup gives for a lineage's words: (the entry's name, its source, its
# edition), or None when the rules data does not have it.
Resolve = Callable[[str], tuple[str, str, str | None] | None]


def rules_resolver(target: str, fallback: str) -> Resolve:
    """A lineage's words looked up in the rules DMbot ships, newest first: the campaign's target
    ruleset, then its fallback. A 2014-only entry keeps its edition, so it shows `[Legacy 2014]`."""
    from dmbot.rules import index as rules_index  # (the index is built once, at the bot's start)

    rules = rules_index.srd()

    def resolve(words: str) -> tuple[str, str, str | None] | None:
        hit = rules.lookup(words, target, fallback, kind=rules_index.SPECIES)
        if hit is None:
            return None
        edition = hit.entry.edition if hit.entry.edition in LINK_EDITIONS else None
        return hit.entry.name, hit.entry.source[:LINK_SOURCE_MAX], edition

    return resolve


@dataclass(frozen=True, slots=True)
class Found:
    """One checked trait: whose (the name as the read wrote it) and what to link."""

    name: str
    trait: Trait


def _plain(words: str) -> list[str]:
    return [w for w in _WORD.findall(words) if w.casefold() not in _ARTICLES]


def _class(words: str) -> Trait | None:
    for word in _plain(words):
        found = _CLASSES.get(word.casefold())
        if found is not None:
            known = found in SRD_CLASSES
            return Trait(CLASS, found, CLASS_SOURCE if known else "", None, known)
    return None


def _lineage(words: str, resolve: Resolve) -> Trait | None:
    plain = _plain(words)
    if not 0 < len(plain) <= LINEAGE_WORDS:
        return None
    said = " ".join(plain)
    hit = resolve(said)
    if hit is None:
        return Trait(SPECIES, said[:1].upper() + said[1:], "", None, False)
    name, source, edition = hit
    return Trait(SPECIES, name, source, edition, True)


def read_traits(
    lines: Iterable[str],
    text: str,
    names: Collection[str],
    resolve: Resolve,
    dropped: Counter[str] | None = None,
    existing: Collection[str] = (),
) -> list[Found]:
    """The traits the text really states, for the caller to store with each new character. `names`:
    the read's own names and the campaign's (as written; matched by key). A line is dropped, and
    counted under its reason, when it can't be read (`trait_unreadable`), is no lineage or class
    (`trait_kind`, `trait_not_a_class`), is about no name of the read (`trait_no_name`), or its
    quote is not in the text or doesn't hold the name and the words (`trait_not_in_text`).
    `existing`: the keys of names the campaign already has. Their traits are counted
    (`trait_existing_name`) and left alone: a change to an existing character is the DM's to
    make, never a silent one (Supervisor's ruling on #2959)."""
    counts = dropped if dropped is not None else Counter()
    haystack = bg._squash(text)
    keys = {name_key(n) for n in names}
    found: list[Found] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in lines:
        m = _LINE.match(raw.strip())
        if m is None:
            counts["trait_unreadable"] += 1
            continue
        name, words, quote = m["name"].strip(), m["words"].strip(), m["quote"].strip()
        kind = _KINDS.get(m["kind"].strip().casefold())
        if kind is None:
            counts["trait_kind"] += 1
            continue
        if name_key(name) in existing:
            counts["trait_existing_name"] += 1
            continue
        if name_key(name) not in keys:
            counts["trait_no_name"] += 1
            continue
        first, said = name.split()[0], bg._squash(quote)  # (squashed as the text is)
        if (
            not bg._has(haystack, quote)
            or not (bg._in_quote(said, name) or bg._in_quote(said, first))
            or not all(bg._in_quote(said, w) for w in _plain(words))
        ):
            counts["trait_not_in_text"] += 1
            continue
        trait = _class(words) if kind == CLASS else _lineage(words, resolve)
        if trait is None:
            counts["trait_not_a_class" if kind == CLASS else "trait_kind"] += 1
            continue
        key = (name_key(name), trait.kind, name_key(trait.name))
        if key in seen:
            continue
        seen.add(key)
        found.append(Found(name, trait))
        if len(found) >= MAX_TRAITS:
            break
    return found
