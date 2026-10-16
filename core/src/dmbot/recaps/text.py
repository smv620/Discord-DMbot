"""Small text helpers the recap stages share (#1382): the words of a text, the cues, the proper
nouns of a narration and the secret-name check. Pure functions; nothing here touches Discord or
the database."""

from __future__ import annotations

import re
from collections.abc import Collection

_CUE = re.compile(r"《[^《》]{1,60}》")
_PERFORMANCE = re.compile(r"\(\([^()]{1,60}\)\)")
_WORD = re.compile(r"[\w'’]+")
_CAPITAL = re.compile(r"(?<![\w'’-])([A-ZÀ-Þ][\w'’-]*)")
_POSSESSIVE = re.compile(r"['’]s$", re.IGNORECASE)
# Small words that are part of names ("Son of Ash") and say nothing on their own.
SMALL_WORDS = frozenset({"the", "and", "for", "son", "von", "van", "der", "del", "los", "las"})
NOT_NAMES = frozenset({"i", "the", "a", "an", "and", "but", "or", "as", "day", "night"})

CUE = _CUE
PERFORMANCE = _PERFORMANCE


def words(text: str) -> list[str]:
    """The words of a text, lower case, possessives taken off ("Garrick's" is "garrick")."""
    return [_POSSESSIVE.sub("", w).lower().strip("'’-") for w in _WORD.findall(text)]


def strip_cues(text: str) -> str:
    """The text as the table reads it: no 《emotion》 and no ((performance)) cue."""
    text = _PERFORMANCE.sub("", _CUE.sub("", text))
    return re.sub(r"[ \t]+([,.;:!?])", r"\1", re.sub(r"[ \t]{2,}", " ", text)).strip()


def narration_of(text: str) -> str:
    """The text with its cues taken out, a space where each was."""
    return _PERFORMANCE.sub(" ", _CUE.sub(" ", text))


def proper_nouns(text: str, *, sentence_starts: bool = False) -> list[str]:
    """Capitalised words in the text. A sentence's first word is skipped (a sentence starts with
    a capital anyway) unless `sentence_starts`; the names a beat names are checked from its `who`
    list instead."""
    plain = narration_of(text)
    found: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", plain):
        first = _WORD.search(sentence)
        for match in _CAPITAL.finditer(sentence):
            if not sentence_starts and first is not None and match.start() == first.start():
                continue
            found.append(match.group(1))
    return found


def is_known_name(name: str, allowed_names: Collection[str]) -> bool:
    """The name is one of the confirmed names (all its words are in one of them)."""
    mine = set(words(name))
    return bool(mine) and any(mine <= set(words(a)) for a in allowed_names)


def names_a_secret(
    text: str, secret_names: Collection[str], allowed_names: Collection[str]
) -> bool:
    """Whether the text names anything the DM keeps secret, in any form: the whole name, its
    possessive or plural ("Vex's", "the Vexes"), a word of it longer than three letters (except
    one the confirmed names also use), a spelling with hyphens, and a name no word pattern can
    read (a name in symbols), which is looked for as written. Used on the draft, on the DM's
    edit, and again when the DM posts, with the secret names read fresh each time."""
    padded = f" {' '.join(words(text))} "
    folded = text.casefold()
    open_words = {w for n in allowed_names for w in words(n)}
    for secret in secret_names:
        tokens = words(secret)
        if not tokens:  # nothing a word regex can read: look for it as written
            if secret.strip() and secret.strip().casefold() in folded:
                return True
            continue
        plain = " ".join(tokens)
        if any(f" {plain}{end} " in padded for end in ("", "s", "es")):
            return True
        for token in tokens:
            if len(token) >= 3 and token not in open_words | SMALL_WORDS and f" {token} " in padded:
                return True
    return False
