"""Noticing a spell, a condition or a creature named at the table (#931).

Names only, no AI and no cost: a line of the transcript is looked at word by word and a
whole name from the free rules (the SRD) is picked out of it, for the campaign's own target
and fallback rulesets. Longest name first ("Hold Person" before "Hold"), a plural is the
same name ("goblins"), and a name is never found inside another word.

Plenty of names are everyday words. Said alone, "light", "fly", "shield", "bat", "prone"
would show a card every minute for nothing, so `EVERYDAY` lists them and they only count
with a lead-in: "cast", "casts" or "casting" in front of any of them, or "is" and its kin in
front of a condition ("the goblin is grappled"). Hold Person, Fireball and the like are not
everyday words and need nothing.

Pure: it reads a line and returns what it found. `dmbot.dm_screen.rules_cards` posts it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from dmbot.rules.index import Entry, normalize

# Names that are everyday words, as the index spells them. Every one of them must be a name
# in the index (a test checks), so a typo here can't hide. Said alone they show nothing.
EVERYDAY: frozenset[str] = frozenset(
    {
        # spells that are plain words
        "Aid", "Alarm", "Awaken", "Bane", "Bless", "Blight", "Blink", "Blur", "Command",
        "Commune", "Confusion", "Contagion", "Creation", "Darkness", "Daylight", "Demiplane",
        "Divination", "Dream", "Earthquake", "Entangle", "Fear", "Fly", "Gate", "Grease",
        "Guidance", "Harm", "Haste", "Heal", "Heroism", "Hex", "Identify", "Jump", "Knock",
        "Levitate", "Light", "Mending", "Message", "Resistance", "Sanctuary", "Seeming",
        "Sending", "Shatter", "Shield", "Silence", "Sleep", "Slow", "Suggestion", "Symbol",
        "Teleport", "Web", "Weird", "Wish",
        # conditions: all of them are plain words
        "Blinded", "Charmed", "Deafened", "Exhaustion", "Frightened", "Grappled",
        "Incapacitated", "Invisible", "Paralyzed", "Petrified", "Poisoned", "Prone",
        "Restrained", "Stunned", "Unconscious",
        # creatures: animals and people with a job
        "Ape", "Baboon", "Badger", "Bat", "Boar", "Camel", "Cat", "Crab", "Crocodile", "Deer",
        "Eagle", "Elephant", "Elk", "Frog", "Goat", "Hawk", "Hippopotamus", "Hyena", "Jackal",
        "Lion", "Lizard", "Mammoth", "Mastiff", "Mule", "Octopus", "Owl", "Panther", "Pony",
        "Rat", "Raven", "Rhinoceros", "Scorpion", "Seahorse", "Spider", "Tiger", "Vulture",
        "Warhorse", "Weasel", "Wolf",
        "Assassin", "Bandit", "Commoner", "Cultist", "Druid", "Gladiator", "Guard", "Knight",
        "Mage", "Noble", "Pirate", "Priest", "Scout", "Spy", "Tough",
        "Ghost", "Shadow",
    }
)  # fmt: skip

LEAD_CAST = frozenset({"cast", "casts", "casting"})  # in front of any everyday name
LEAD_IS = frozenset({"is", "are", "was", "were", "be", "been", "being", "gets", "got"})
WORDS_BEFORE = 3  # words of what was said shown before the name, and after it:
WORDS_AFTER = 2
HEARD_MAX = 120

_WORD = re.compile(r"[A-Za-z0-9’']+")
_EVERYDAY_KEYS: frozenset[str] = frozenset(normalize(name) for name in EVERYDAY)
_STOPS = " .,;:!?\"'’“”"  # what may trail a line without being more words


@dataclass(frozen=True, slots=True)
class Mention:
    """A name found in a line."""

    entry: Entry
    said: str  # the words that made the name, as said
    heard: str  # those words with a few around them, as said (for "Heard: …")

    @property
    def key(self) -> tuple[str, str]:
        """What "once per name" means: the entry, not how it was said (Goblin and Goblin
        Warrior are one)."""
        return (self.entry.kind, normalize(self.entry.name))


@dataclass(slots=True)
class Spotter:
    """The names of one ruleset order, ready to look for."""

    by_first: dict[str, list[tuple[tuple[str, ...], Entry]]] = field(default_factory=dict)

    @classmethod
    def from_pool(cls, pool: Mapping[str, Entry]) -> Spotter:
        spotter = cls()
        for key, entry in pool.items():
            words = tuple(key.split())
            if words and len(key) >= 3:
                spotter.by_first.setdefault(words[0], []).append((words, entry))
        for options in spotter.by_first.values():
            options.sort(key=lambda option: -len(option[0]))  # the longest name first
        return spotter

    def find(self, line: str) -> list[Mention]:
        """Every name in the line, in order, each entry once."""
        spans = [(m.start(), m.end(), normalize(m.group())) for m in _WORD.finditer(line)]
        tokens = [(start, end, word) for start, end, word in spans if word]
        found: list[Mention] = []
        seen: set[tuple[str, str]] = set()
        i = 0
        while i < len(tokens):
            hit = self._at(tokens, i)
            if hit is None:
                i += 1
                continue
            length, entry = hit
            key = (entry.kind, normalize(entry.name))
            if key not in seen:  # a repeat costs nothing more
                seen.add(key)
                found.append(Mention(entry, *_said(line, tokens, i, length)))
            i += length
        return found

    def _at(self, tokens: list[tuple[int, int, str]], i: int) -> tuple[int, Entry] | None:
        for first in _variants(tokens[i][2]):
            for words, entry in self.by_first.get(first, ()):
                n = len(words)
                if i + n > len(tokens):
                    continue
                if not _matches(tokens, i, words):
                    continue
                if not _allowed(entry, tokens, i):
                    continue
                return n, entry
        return None


def _variants(word: str) -> list[str]:
    """The word, and the word without a plural ending (goblins, wolves): a one-word name
    is found in the plural too."""
    found = [word]
    if word.endswith("ves") and len(word) > 4:
        found.append(word[:-3] + "f")
    if word.endswith("es") and len(word) > 4:
        found.append(word[:-2])
    if word.endswith("s") and len(word) > 3:
        found.append(word[:-1])
    return found


def _matches(tokens: list[tuple[int, int, str]], i: int, words: tuple[str, ...]) -> bool:
    for offset, wanted in enumerate(words):
        said = tokens[i + offset][2]
        last = offset == len(words) - 1
        if said != wanted and not (last and wanted in _variants(said)):
            return False
    return True


def _allowed(entry: Entry, tokens: list[tuple[int, int, str]], i: int) -> bool:
    """An everyday name only counts after its lead-in."""
    if normalize(entry.name) not in _EVERYDAY_KEYS:
        return True
    before = tokens[i - 1][2] if i > 0 else ""
    if before in LEAD_CAST:
        return True
    return entry.kind == "condition" and before in LEAD_IS


def _said(line: str, tokens: list[tuple[int, int, str]], i: int, length: int) -> tuple[str, str]:
    """The words of the name as said, and with a few words around them (never cut so that
    the name itself is lost)."""
    start, end = tokens[i][0], tokens[i + length - 1][1]
    said = line[start:end]
    lo = max(0, i - WORDS_BEFORE)
    hi = min(len(tokens), i + length + WORDS_AFTER)
    room = max(0, HEARD_MAX - len(said))
    first = max(tokens[lo][0], start - room // 2)
    last = min(tokens[hi - 1][1], end + room - room // 2)
    heard = line[first:last]
    if line[:first].strip(_STOPS):
        heard = "…" + heard
    if line[last:].strip(_STOPS):
        heard += "…"
    return said, heard
