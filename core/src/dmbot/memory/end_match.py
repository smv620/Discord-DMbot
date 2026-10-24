"""Matching the end of a backstory connection to a name the way a reader would (#2642).

A backstory says "my brother Ulric", "Sister Mara" or "old Brannoc, my mentor" while the names
list has "Ulric Stonehand", "Mara Ashgrove" and "Brannoc". An exact comparison drops all of those,
so a backstory full of family and friends gave one connection. Here an end is matched, in order:
exactly (as before); with a leading title, relation or "my" taken off; then by its words: a first
or last name that fits exactly one name. A word that fits two names ("Mara") is not guessed: it is
`ambiguous`, and the caller leaves the connection out with the question "Which Mara?".

Pure. Only names DMbot will already have are candidates (this reading's public names and their
other spellings, and the campaign's confirmed public names), so a secret name is never matched by
its first word: a secret spelling matches only exactly, as before.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from dmbot.memory.models import name_key

# Words that come before a name without being part of it: a title, a relation, a pronoun. The end
# "my brother Ulric" is "Ulric". Kept short and plain on purpose; a word not here simply makes the
# end match by its other words.
FILLER = frozenset(
    {
        "my",
        "our",
        "his",
        "her",
        "their",
        "your",
        "the",
        "a",
        "an",
        "old",
        "young",
        "elder",
        "little",
        "great",
        "lord",
        "lady",
        "sir",
        "dame",
        "king",
        "queen",
        "prince",
        "princess",
        "duke",
        "duchess",
        "baron",
        "baroness",
        "captain",
        "commander",
        "general",
        "father",
        "mother",
        "brother",
        "sister",
        "son",
        "daughter",
        "uncle",
        "aunt",
        "cousin",
        "grandfather",
        "grandmother",
        "grandpa",
        "grandma",
        "nephew",
        "niece",
        "friend",
        "mentor",
        "master",
        "teacher",
        "apprentice",
        "rival",
        "enemy",
        "ally",
        "companion",
        "ward",
        "husband",
        "wife",
        "mistress",
        "saint",
        "doctor",
        "dr",
        "professor",
        "mr",
        "mrs",
        "ms",
        "miss",
    }
)
SHORTEST = 3  # a word shorter than this is no name's whole claim to a match ("of", "de")


@dataclass(frozen=True, slots=True)
class Matched:
    """`name` is the name the end stands for; `ambiguous` lists the names that fit equally well
    when none is chosen (at least two); both empty: it fits nothing."""

    name: str | None = None
    ambiguous: tuple[str, ...] = ()
    key: str = ""  # the spelling it matched exactly (title off), "" when matched by a word


def _words(text: str) -> list[str]:
    return name_key(text).split()


def head(end: str) -> str:
    """The name of an end that carries a trailing description (#2650): "Brannoc, my mentor" and
    "Brannoc (my mentor)" are "Brannoc", as "my mentor Brannoc" is. Only a comma or a bracket
    followed by filler counts, so "Frost, Ulfgar" is left alone. The caller tries the whole end
    exactly first, so a name that really holds a comma still matches."""
    for sep in (",", "("):
        before, found, after = end.partition(sep)
        first = name_key(after).split()[:1]
        if found and name_key(before) and first and first[0] in FILLER:
            return before.strip()
    return end


def stripped(end: str) -> list[str]:
    """The words of an end without the filler in front of it ("my brother Ulric" -> ulric)."""
    words = _words(end)
    while len(words) > 1 and words[0] in FILLER:
        words = words[1:]
    return words


def bare(end: str) -> str:
    """The name an end asks about, without its title or description, as a person would say the
    question: "Sister Mara" and "Mara, my cousin" are "Mara" (#2650)."""
    words = stripped(head(end))
    return " ".join(w.capitalize() for w in words) or end


class Ends:
    """The names an end may stand for. `exact` maps a name key to its name (this reading's names,
    their other public spellings, the campaign's confirmed names); `names` are the names whose
    words a shorter end may fit."""

    def __init__(self, exact: Mapping[str, str], names: Iterable[str]) -> None:
        self._exact = dict(exact)
        self._words = {n: set(_words(n)) for n in dict.fromkeys(names)}

    def match(self, end: str) -> Matched:
        exact = self._exact.get(name_key(end))
        if exact is not None:
            return Matched(exact, key=name_key(end))
        end = head(end)
        exact = self._exact.get(name_key(end))
        if exact is not None:
            return Matched(exact, key=name_key(end))
        words = stripped(end)
        if not words:
            return Matched()
        joined = self._exact.get(" ".join(words))
        if joined is not None:
            return Matched(joined, key=" ".join(words))
        # by its words: every word of the end is a word of the name, and one of them is a name
        # of some length (not just "of" or "the")
        if not any(len(w) >= SHORTEST and w not in FILLER for w in words):
            return Matched()
        fits = [n for n, have in self._words.items() if set(words) <= have]
        if len(fits) == 1:
            return Matched(fits[0])
        if len(fits) > 1:
            return Matched(None, tuple(sorted(fits)))
        return Matched()
