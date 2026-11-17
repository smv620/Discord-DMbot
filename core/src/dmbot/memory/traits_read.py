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
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dmbot.memory import background as bg
from dmbot.memory.models import (
    CLASS,
    LINK_EDITIONS,
    LINK_SOURCE_MAX,
    SPECIES,
    Trait,
    name_key,
)
from dmbot.names import not_names

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
# A language the character speaks (#4229 slice 1b): a TRAITS line too, but never a rules link. The
# languages live beside the sheets, so `read_languages` reads these lines and `read_traits` leaves
# them alone.
LANGUAGE_KINDS = frozenset({"language", "languages", "speaks", "tongue"})
LANGUAGE_WORDS = 6  # "the old tongue of Netheril": a name, never a sentence
MAX_LANGUAGES = 120  # the same guard as MAX_TRAITS
_CLASSES = {c.casefold(): c for c in (*SRD_CLASSES, *OTHER_CLASSES)}
_WORD = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*")
_ARTICLES = frozenset({"a", "an", "the"})

if TYPE_CHECKING:
    from dmbot.rules.homebrew import Homebrew

_BRACKET = re.compile(r"\s*\([^)]*\)\s*$")  # "Frost Elf (Homebrew)" is matched as "Frost Elf"

# What the caller's rules lookup gives for a lineage's words: (the entry's name, its source, its
# edition), or None when the rules data does not have it.
Resolve = Callable[[str], tuple[str, str, str | None] | None]


HOMEBREW_SOURCE = "homebrew"  # the rules link's source for a lineage from the campaign's homebrew
HOMEBREW_SPECIES = "species"  # the homebrew kind that is a lineage


def _lineage_keys(name: str) -> tuple[str, str]:
    """The forms a lineage's name is matched by: the rules lookup's own key (`rules.index`:
    case, punctuation and a last bracket ignored, a hyphen read as a space), and the same with no
    spaces, so "Half Elf", "half-elf" and "Halfelf", or "Dragon-born" and "Dragonborn", meet."""
    from dmbot.rules import index as rules_index

    key = rules_index.normalize(_BRACKET.sub("", name))
    return key, key.replace(" ", "")


def homebrew_lineages(entries: Iterable[Homebrew]) -> dict[str, str]:
    """match key -> the entry's name, for the campaign's homebrew lineages (entries of kind
    `species`). `entries`: what the campaign reads, in its own order (`HomebrewReader.list`: its
    own usable entries, then the packs and shared sets it follows), so the first with a name wins.
    Pure."""
    out: dict[str, str] = {}
    for entry in entries:
        if entry.kind != HOMEBREW_SPECIES:
            continue
        for key in _lineage_keys(entry.name):
            if key and key not in out:
                out[key] = entry.name
    return out


def rules_resolver(
    target: str, fallback: str, homebrew: Mapping[str, str] | None = None
) -> Resolve:
    """A lineage's words looked up in CLAUDE.md's order (#3926): the campaign's homebrew first
    (`homebrew_lineages`; one this campaign reads, never another's), then the rules DMbot ships,
    newest first: the campaign's target ruleset, then its fallback. A homebrew lineage has no
    edition. A 2014-only entry keeps its edition, so it shows `[Legacy 2014]`."""
    from dmbot.rules import index as rules_index  # (the index is built once, at the bot's start)

    rules = rules_index.srd()
    own = homebrew or {}

    def resolve(words: str) -> tuple[str, str, str | None] | None:
        mine = next((own[k] for k in _lineage_keys(words) if k in own), None)
        if mine is not None:
            return mine, HOMEBREW_SOURCE, None
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
    quote: str = ""  # the reader's quote, kept for a question about an existing character (#3926)


def _plain(words: str) -> list[str]:
    return [w for w in _WORD.findall(words) if w.casefold() not in _ARTICLES]


def _class(words: str) -> Trait | None:
    for word in _plain(words):
        found = _CLASSES.get(word.casefold())
        if found is not None:
            known = found in SRD_CLASSES
            return Trait(
                CLASS, found, CLASS_SOURCE if known else "", "2024" if known else None, known
            )
    return None


def _lineage(words: str, resolve: Resolve) -> Trait | None:
    plain = _plain(words)
    if not 0 < len(plain) <= LINEAGE_WORDS or any(w.casefold() in _CLASSES for w in plain):
        return None  # (a phrase, or "half-elf paladin" written as one: the class is its own line)
    if not_names.is_language(words):  # "Elvish" is what someone speaks, never their lineage (#4229)
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
    offers: list[Found] | None = None,
) -> list[Found]:
    """The traits the text really states, for the caller to store with each new character. `names`:
    the read's own names and the campaign's (as written; matched by key). A line is dropped, and
    counted under its reason, when it can't be read (`trait_unreadable`), is no lineage or class
    (`trait_kind`, `trait_not_a_class`), is about no name of the read (`trait_no_name`), or its
    quote is not in the text or doesn't hold the name and the words (`trait_not_in_text`).
    `existing`: the keys of names the campaign already has. Their traits are never changed
    silently: one that passes the same checks goes into `offers` (Supervisor's ruling on #3926:
    the caller asks the DM about it, counted `trait_existing_checked`); without `offers` it is only
    counted
    (`trait_existing_name`)."""
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
        if m["kind"].strip().casefold() in LANGUAGE_KINDS:
            continue  # (`read_languages`'s)
        kind = _KINDS.get(m["kind"].strip().casefold())
        if kind is None:
            counts["trait_kind"] += 1
            continue
        is_existing = name_key(name) in existing
        if is_existing and offers is None:
            counts["trait_existing_name"] += 1
            continue
        if not is_existing and name_key(name) not in keys:
            counts["trait_no_name"] += 1
            continue
        said = bg._squash(quote)  # (squashed as the text is)
        # (the name whole or a word of its own, never a title alone: "the captain's first mate,
        # an elf" is not Captain Rell Dorn's lineage)
        if (
            not bg._has(haystack, quote)
            or not bg._in_quote(said, name)
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
        if is_existing and offers is not None:
            # (checked, not yet asked: the caller leaves out what changes nothing or is unsure)
            if len(offers) < MAX_TRAITS:  # (its own cap: never eats the new names' traits)
                counts["trait_existing_checked"] += 1
                offers.append(Found(name, trait, quote))
            continue
        found.append(Found(name, trait))
        if len(found) >= MAX_TRAITS:
            break
    return found


_ARTICLE = re.compile(r"^(?:a|an|the)\s+", re.I)
_TONGUE = frozenset({"tongue", "speech", "cant", "language", "dialect", "sign"})


def _names_a_language(part: str) -> bool:
    """Do these words name a language, not a way of speaking ("speaks softly", "speaks with
    animals")? One of the SRD's, a name written with a capital ("Netherese"), or a phrase with a
    language word in it ("the old tongue of Netheril"). The quote check still has to pass."""
    words = _plain(part)
    if " ".join(w.casefold() for w in words) in not_names.LANGUAGES:
        return True
    if any(w.casefold() in _TONGUE for w in words):
        return True
    return all(w[:1].isupper() for w in words if w.casefold() not in ("of", "the"))


@dataclass(frozen=True, slots=True)
class Spoken:
    """One checked language: whose (the name as the read wrote it) and the language, as the text
    names it (#4229)."""

    name: str
    language: str
    quote: str = ""  # the reader's quote, for a question about an existing character (#4229 1c)


def read_languages(
    lines: Iterable[str],
    text: str,
    names: Collection[str],
    dropped: Counter[str] | None = None,
    existing: Collection[str] = (),
) -> list[Spoken]:
    """The languages the text really says a character speaks (#4229 slice 1b): the TRAITS lines
    whose kind is a language, each checked as a trait is, so nothing is invented. The quote is in
    the text and holds the name (or a word of it) and the language's words. `names`: the read's own
    and the campaign's, matched by key; `existing`: the keys of names the campaign has already.
    "Elvish and Sylvan" on one line is two languages. A line is
    dropped, and counted under its reason, when it is about no name (`language_no_name`), its quote
    doesn't hold the name and the words (`language_not_in_text`), or the words are no name of a
    language (`language_words`: too long, or a way of speaking, `_names_a_language`). A language
    keeps the text's own spelling ("Thieves' Cant"). Pure; counts only."""
    counts = dropped if dropped is not None else Counter()
    haystack = bg._squash(text)
    keys = {name_key(n) for n in names} | set(existing)
    found: list[Spoken] = []
    seen: set[tuple[str, str]] = set()
    for raw in lines:
        m = _LINE.match(raw.strip())
        if m is None or m["kind"].strip().casefold() not in LANGUAGE_KINDS:
            continue  # (`read_traits` counts what it can't read)
        name, quote = m["name"].strip(), m["quote"].strip()
        if name_key(name) not in keys:
            counts["language_no_name"] += 1
            continue
        said = bg._squash(quote)
        if not bg._has(haystack, quote) or not bg._in_quote(said, name):
            counts["language_not_in_text"] += 1
            continue
        for part in re.split(r",|\band\b|\bor\b|/", m["words"]):
            words = _plain(part)
            if not words:
                continue
            if len(words) > LANGUAGE_WORDS or not _names_a_language(part):
                counts["language_words"] += 1
                continue
            if not all(bg._in_quote(said, w) for w in words):
                counts["language_not_in_text"] += 1
                continue
            language = _ARTICLE.sub("", part.strip(" .;:\"'")).strip()  # as written: Thieves' Cant
            language = language[:1].upper() + language[1:]
            key = (name_key(name), name_key(language))
            if key in seen:
                continue
            seen.add(key)
            found.append(Spoken(name, language, quote))
            if len(found) >= MAX_LANGUAGES:
                return found
    return found
