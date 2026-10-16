"""Noticing the campaign's own homebrew named at the table (#1524), like `dmbot.rules.spotter`
does for the free rules: names only, no AI, no cost. The whole name of an entry (case and
punctuation ignored, the last word may be a plural) is picked out of a line, longest first.

A name that is an everyday word of the free rules (Light, Fly, Shield, Wolf…) counts only with
the same lead-in the book's spotter wants ("cast light", "is prone"), and a homebrew creature
with such a name never makes a card, since a false card counts as a bug. Hidden or not, an entry
is carded: the card goes to the DM screen only.

Pure: it reads a line and returns what it found. `DMBot._note_rules` posts it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from dmbot.rules.homebrew import Homebrew
from dmbot.rules.index import normalize
from dmbot.rules.spotter import (
    _EVERYDAY_KEYS,
    _WORD,
    LEAD_CAST,
    LEAD_IS,
    _matches,
    _said,
    _variants,
)

MIN_KEY = 3  # characters of a normalized name


@dataclass(frozen=True, slots=True)
class HomebrewMention:
    """An entry's name found in a line (the same shape as `spotter.Mention`)."""

    name: str
    kind: str
    said: str  # the words that made the name, as said
    heard: str  # those words with a few around them, as said

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, normalize(self.name))


@dataclass(slots=True)
class HomebrewSpotter:
    """One campaign's homebrew names, ready to look for."""

    by_first: dict[str, list[tuple[tuple[str, ...], Homebrew]]] = field(default_factory=dict)

    @classmethod
    def from_entries(cls, entries: Iterable[Homebrew]) -> HomebrewSpotter:
        spotter = cls()
        for entry in entries:
            key = normalize(entry.name)
            words = tuple(key.split())
            if words and len(key) >= MIN_KEY:
                spotter.by_first.setdefault(words[0], []).append((words, entry))
        for options in spotter.by_first.values():
            options.sort(key=lambda option: -len(option[0]))  # the longest name first
        return spotter

    def entry(self, name: str) -> Homebrew | None:
        """The entry with this name (case and punctuation ignored), if the spotter looks for it.
        A campaign's entries have one name each whatever their kind (the table is unique on the
        name), so the name alone picks one."""
        key = normalize(name)
        words = tuple(key.split())
        for w, entry in self.by_first.get(words[0] if words else "", ()):
            if w == words:
                return entry
        return None

    @property
    def names(self) -> frozenset[str]:
        """The normalized names it looks for (the book's cards leave these to the homebrew)."""
        return frozenset(
            " ".join(words) for options in self.by_first.values() for words, _ in options
        )

    def find(self, line: str) -> list[HomebrewMention]:
        """Every name in the line, in order, each entry once."""
        tokens = [(m.start(), m.end(), normalize(m.group())) for m in _WORD.finditer(line)]
        tokens = [(start, end, word) for start, end, word in tokens if word]
        found: list[HomebrewMention] = []
        seen: set[tuple[str, str]] = set()
        i = 0
        while i < len(tokens):
            hit = self._at(tokens, i)
            if hit is None:
                i += 1
                continue
            length, entry = hit
            key = (entry.kind, normalize(entry.name))
            if key not in seen:
                seen.add(key)
                found.append(
                    HomebrewMention(entry.name, entry.kind, *_said(line, tokens, i, length))
                )
            i += length
        return found

    def _at(self, tokens: list[tuple[int, int, str]], i: int) -> tuple[int, Homebrew] | None:
        for first in _variants(tokens[i][2]):
            for words, entry in self.by_first.get(first, ()):
                n = len(words)
                if i + n > len(tokens) or not _matches(tokens, i, words):
                    continue
                if not _allowed(entry, " ".join(words), tokens, i):
                    continue
                return n, entry
        return None


def _allowed(entry: Homebrew, key: str, tokens: list[tuple[int, int, str]], i: int) -> bool:
    if key not in _EVERYDAY_KEYS:
        return True
    if entry.kind not in ("spell", "condition"):
        return False  # an everyday word as a creature, item or rule's name: never from the table
    before = tokens[i - 1][2] if i > 0 else ""
    if entry.kind == "condition":
        return before in LEAD_IS or before in LEAD_CAST
    return before in LEAD_CAST
