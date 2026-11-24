"""A waiting name that is a known one with or without a final "s" (#5215, slice 2): "Explorer
League" beside a known "Explorer's League" is joined, not asked again after every session that says
it the short way. Pure: the rule only picks; the store's `join_by_ai` is the one door, re-checks
every limit at the moment of use, and leaves the join waiting for the DM's ✅ or ↩️ Undo (#3602).

Only a name of two or more words: "Harper" (a person) and "the Harpers" (a group) are two things,
so a one-word name is always asked ("Sounds like **Harpers**. The same, or new?", slice 1). Never
when two known names fit, nor when the waiting spelling is itself a known name (both are entries:
two real groups). A secret name is never a candidate (`MemoryStore.sound_alikes` reads none), so it
is asked instead, as a new name, and nothing secret is shown."""

from __future__ import annotations

from collections.abc import Iterable

from dmbot.memory.models import name_key
from dmbot.memory.scan import S_FOLD_STEM, Candidate

JOIN_WORDS = 2  # (a one-word name is always asked)


def fold_key(text: str) -> str:
    """`name_key` with a final "s" taken off each word whose stem has 5 letters or more:
    "Explorer's League", "Explorers League" and "Explorer League" are one fold key; "Harps" and
    "Harp" are not (the stem is 4 letters). For this comparison only: `name_key` never changes."""
    words = []
    for word in name_key(text).split():
        stem = word[:-1] if word.endswith("s") else word
        words.append(stem if stem != word and len(stem) >= S_FOLD_STEM else word)
    return " ".join(words)


def join_target(
    name: str, candidates: Iterable[Candidate], secret: Iterable[Candidate] = ()
) -> str | None:
    """The one known entry `name` is, differing from it only by a final "s" (on any of its words),
    or None (asked as today). `candidates`: confirmed, never secret names (entity ID, a name of it
    as written, its own name), as `MemoryStore.sound_alikes` gives them for
    `scan.card_sound_keys(name)`; `secret`: the secret ones the same way, only counted: if one fits,
    the DM is asked (the name may really be the secret one), and nothing of it is shown."""
    if len(name.split()) < JOIN_WORDS:
        return None
    own, want = name_key(name), fold_key(name)
    if any(fold_key(text) == want for _e, text, _o in secret):
        return None
    fits: set[str] = set()
    exact: set[str] = set()
    for entity_id, text, _own in candidates:
        if name_key(text) == own:
            exact.add(entity_id)
        if fold_key(text) == want:
            fits.add(entity_id)
    if len(fits) != 1 or exact - fits:
        return None  # (two fit, or the spelling is another entry's own: both are entries)
    return next(iter(fits))
