"""A person's first name becomes their short name, by rule (#3880, Supervisor's ruling). Pure.

The lore says "Claude Ambere" once and "Claude" a dozen times; the session says "Claude". Names are
matched by name or other name only (`claims.names_of`), so unless "Claude" is stored as one of
Claude Ambere's other names, every run asks the DM whether "Claude" is a new NPC. The lore reader's
other names come from the AI and vary from run to run, so this is a rule in code, not a request to
the AI. A person's (a character's: player character, NPC or god) first word is their short name
only when all of these hold:

1. it is one word, starts with a capital and is at least `MIN_LETTERS` long;
2. it is not a title or form of address (`not_names.TITLE_WORDS`, the list #3385 uses too), a
   creature kind, a rules word or a word the table says with a capital that is no name;
3. no other entry has that word as its name, other name or first word (two Toms: neither gets it);
4. the campaign's text uses the word alone at least once (not only as the start of the full name),
   and a word that is also an everyday word ("Will", "Hope") must be used alone mid-sentence;
5. the entry doesn't already have it, and the DM never turned it down for this entry.

It is a short name, never a merge: the caller adds it as a proposed other name of the same entry,
in one undoable write (`MemoryStore.add_short_names`). Nothing here reads the database.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from dmbot.memory.models import name_key
from dmbot.memory.scan import COMMON, GAME_TERMS
from dmbot.names import not_names

MIN_LETTERS = 3
# What a creature is, said with a capital before a name ("Goblin Grik", "Dragon Queen"): the kind,
# never the person's own name. The species and classes the table says are in scan.GAME_TERMS.
CREATURE_KINDS = frozenset(
    {
        *("aberration", "beast", "celestial", "construct", "dragon", "elemental", "fey", "fiend"),
        *("giant", "humanoid", "monstrosity", "ooze", "plant", "undead", "goblin", "hobgoblin"),
        *("bugbear", "kobold", "ogre", "troll", "yeti", "wolf", "bear", "duergar", "drow", "lich"),
        *("vampire", "zombie", "skeleton", "ghost", "ghoul", "demon", "devil", "golem", "hag"),
    }
)
_NOT_A_SHORT_NAME = (
    not_names.TITLE_WORDS
    | not_names.PRONOUNS
    | not_names.ADDRESS_WORDS
    | not_names.TABLE_TALK
    | not_names.NUMBER_WORDS
    | not_names.UNITS
    | not_names.TIME_WORDS
    | not_names.SENTENCE_STARTERS
    | COMMON
    | GAME_TERMS
    | CREATURE_KINDS
)
_WORD = re.compile(r"[^\W\d_](?:[^\W\d_]|[-'’](?=[^\W\d_]))*")
_EDGE = "'’-"
# "Cerric's paternal grandfather" is described by someone else's name: "Cerric's" is not theirs.
_POSSESSIVE = re.compile(r"['’]s$")
# Where a sentence starts: a capital there says nothing about a name ("Will you come?").
_SENTENCE_END = frozenset('.!?:;"“”([')
_AROUND = '.,;:!?"“”()[]'  # punctuation written against a word, not part of it


def first_word(name: str) -> str:
    """The first word of a name as written, without the punctuation around it ("" if none)."""
    parts = name.split()
    return parts[0].strip(_AROUND) if parts else ""


def _shaped(word: str) -> bool:
    return (
        len(word) >= MIN_LETTERS
        and word[0].isupper()
        and _WORD.fullmatch(word) is not None
        and _POSSESSIVE.search(word) is None
        and word.casefold() not in _NOT_A_SHORT_NAME
    )


def _owners(names: Iterable[tuple[str, str]]) -> dict[str, set[str]]:
    """Each name key, and the key of each name's first word, to the entries that use it."""
    owners: dict[str, set[str]] = {}
    for owner, text in names:
        for key in {name_key(text), name_key(first_word(text))} - {""}:
            owners.setdefault(key, set()).add(owner)
    return owners


def used_alone(word: str, rest: str, text: str) -> bool:
    """Whether the text uses `word` (case kept) on its own: not followed by `rest`, the next word of
    the full name. "Claude's" counts. When the text also has the word in lower case (an everyday
    word such as "will"), only a use in the middle of a sentence counts."""
    everyday = re.search(rf"(?<![\w{_EDGE}]){re.escape(word.lower())}(?![\w{_EDGE}])", text)
    pattern = re.compile(rf"(?<![\w{_EDGE}]){re.escape(word)}(?:['’]s)?(?![\w{_EDGE}])")
    full = re.compile(rf"\s+{re.escape(rest)}(?![\w{_EDGE}])", re.IGNORECASE) if rest else None
    for found in pattern.finditer(text):
        if full is not None and full.match(text, found.end()):
            continue  # "Claude Ambere": the full name, not the word alone
        if not everyday:
            return True
        before = text[: found.start()].rstrip(" \t")
        if before and before[-1] not in _SENTENCE_END and before[-1] != "\n":
            return True
    return False


def short_names(
    people: Sequence[tuple[str, str]],
    names: Iterable[tuple[str, str]],
    texts: Sequence[str],
) -> list[tuple[str, str]]:
    """(entry id, short name) for each person who gets one. `people`: (id, name) of the campaign's
    characters. `names`: (entry id, text) of every name and other name the campaign has, rejected
    ones included, so a name the DM turned down for an entry is never added to it again and a word
    another entry was called is never taken. `texts`: the campaign's text to look for the word in
    (the lore just read, or a session's lines)."""
    names = list(names)
    owners = _owners(names)
    has = {(owner, name_key(text)) for owner, text in names}
    text = "\n".join(texts)
    found: list[tuple[str, str]] = []
    for person, name in people:
        parts = name.split()
        if len(parts) < 2:
            continue  # a one-word name is its own short name
        word = first_word(name)
        key = name_key(word)
        if not _shaped(word) or not key or (person, key) in has:
            continue
        if owners.get(key, set()) - {person}:
            continue  # another entry is called that, or starts with it: neither gets it
        if used_alone(word, parts[1].strip(_AROUND), text):
            found.append((person, word))
    return found
