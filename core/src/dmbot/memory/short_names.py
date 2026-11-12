"""A person's first name becomes their short name, by rule (#3880, Supervisor's ruling). Pure.

The lore says "Tran Vosk" once and "Tran" a dozen times; the session says "Tran". Names are matched
by name or other name only (`claims.names_of`), so unless "Tran" is stored as one of Tran Vosk's
other names, every run asks the DM whether "Tran" is a new NPC. The lore reader's
other names come from the AI and vary from run to run, so this is a rule in code, not a request to
the AI. A person's (a character's: player character, NPC or god) first word is their short name
only when all of these hold:

1. it is one word, starts with a capital and is at least `MIN_LETTERS` long;
2. it is not a title or form of address (`not_names.TITLE_WORDS`), a creature kind, an epithet
   ("Red", "Mad"), a rules word or a word the table says with a capital that is no name;
3. no other entry has that word as its name, other name or first word (two Toms: neither gets it);
4. the campaign's text uses the word alone at least once, in the middle of a sentence and not
   followed by another capitalised word (`used_alone`);
5. the entry doesn't already have it, and the DM never turned it down or undid it for this entry.

It is a short name, never a merge: the caller adds it as a proposed other name of the same entry,
in one undoable write (`MemoryStore.add_short_names`). Nothing here reads the database.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from dmbot.memory.models import name_key
from dmbot.memory.scan import COMMON, GAME_TERMS
from dmbot.names import not_names
from dmbot.names.not_names import CREATURE_KINDS

MIN_LETTERS = 3
# Words an epithet starts with ("Red Ilsa", "Black Grim", "Mad Tom"): said alone they name a colour
# or a mood, or another name that starts with them ("the Red Wizards"), never this one person.
EPITHETS = frozenset(
    {
        *("red", "black", "white", "grey", "gray", "green", "blue", "gold", "golden", "silver"),
        *("iron", "stone", "dark", "bright", "grim", "mad", "blind", "lame", "bald", "fat"),
        *("tall", "short", "one-eyed", "fair", "bloody", "brave", "wise", "cruel", "lucky"),
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
    | EPITHETS
)
_WORD = re.compile(r"[^\W\d_](?:[^\W\d_]|[-'’](?=[^\W\d_]))*")
_EDGE = "'’-"
# "Cerric's paternal grandfather" is described by someone else's name: "Cerric's" is not theirs.
_POSSESSIVE = re.compile(r"['’]s$")
# Where a sentence starts: a capital there says nothing about a name ("Will you come?").
_SENTENCE_END = frozenset('.!?:;"“”([\n')
_NEXT_WORD = re.compile(r"[ \t]+([^\W\d_])")  # the first letter of the word that follows
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


def could_be_short_name(word: str) -> bool:
    """Whether this first word is shaped like a name someone could be called alone (a capital,
    enough letters, not a title, creature kind, epithet or rules word). The caller decides the
    rest (#4224: the wizard's characters, whose names the DM typed)."""
    return _shaped(word)


def _owners(names: Iterable[tuple[str, str]]) -> dict[str, set[str]]:
    """Each name key, and the key of each name's first word, to the entries that use it."""
    owners: dict[str, set[str]] = {}
    for owner, text in names:
        for key in {name_key(text), name_key(first_word(text))} - {""}:
            owners.setdefault(key, set()).add(owner)
    return owners


def used_alone(word: str, text: str) -> bool:
    """Whether the text uses `word` (case kept) on its own, in the middle of a sentence: "Then
    Tran left", "we took Tran's horse". Not when another capitalised word follows ("Tran Vosk",
    or "Tran Hale", someone with no entry yet; "the Red Wizards"), and never at the start of a
    sentence or line, where a capital says nothing about a name ("Will you come?")."""
    pattern = re.compile(rf"(?<![\w{_EDGE}]){re.escape(word)}(?:['’]s)?(?![\w{_EDGE}])")
    for found in pattern.finditer(text):
        after = _NEXT_WORD.match(text, found.end())
        if after is not None and after.group(1).isupper():
            continue
        before = text[: found.start()].rstrip(" \t")
        if before and before[-1] not in _SENTENCE_END:
            return True
    return False


_NAME_AFTER = re.compile(r"[ \t]+([^\W\d_][\w'’-]*)")  # the word that follows, whole


def named_otherwise(word: str, name: str, text: str) -> bool:
    """Does the text also write the word as the start of another full name ("the smith Cerric
    Dorn" beside Cerric Vanya'elen)? Then it may be another person the text is about to bring in,
    who is not stored yet: before a read, nobody gets the word (#4224, the reviewer)."""
    own = name.split()[1] if len(name.split()) > 1 else ""
    pattern = re.compile(rf"(?<![\w{_EDGE}]){re.escape(word)}(?![\w'’{_EDGE}])")
    for found in pattern.finditer(text):
        after = _NAME_AFTER.match(text, found.end())
        if after is not None and after.group(1)[:1].isupper() and after.group(1) != own:
            return True
    return False


def short_names(
    people: Sequence[tuple[str, str]],
    names: Iterable[tuple[str, str]],
    texts: Sequence[str],
    *,
    unread: bool = False,
) -> list[tuple[str, str]]:
    """(entry id, short name) for each person who gets one. `people`: (id, name) of the campaign's
    characters. `names`: (entry id, text) of every name and other name the campaign's live entries
    have, rejected and undone ones included, so a name the DM turned down for an entry is never
    added to it again and a word another entry is called is never taken. `texts`: the campaign's
    text to look for the word in (the lore just read, or a session's lines, each its own line).
    `unread`: the text has not been read for names yet, so a person it is about to bring in is no
    owner yet: a word the text also writes before another capitalised name is held back."""
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
        if word in text and used_alone(word, text):  # (the plain look first: it's cheap)
            if unread and named_otherwise(word, name, text):
                continue  # (maybe a namesake the read has yet to store: the read after decides)
            found.append((person, word))
    return found
