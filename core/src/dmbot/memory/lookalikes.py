"""Names that may be one name written several ways (#2426): the finder behind "Look-alikes".

Pure: no database, no AI. It only *suggests* groups; the DM decides whether they are one (and
which spelling to keep), and a pair the DM said "Not the same" to is never offered again.
Two names look alike when, after tidying (lower case, a leading "the", "a" or "an" dropped,
spaces, hyphens and marks removed), they

- are the same letters ("Bel-Eros", "Bel Eros", "BelEros"), or
- are a letter or two apart (one up to eight letters, two beyond; never below five letters, so
  "Bryn" and "Bran" stay two people), or
- sound the same (the sound codes of `sounds.py` share one), or
- one is the other with more whole words ("Belleros" and "Belleros the Elder").

A group is every name joined to another by one of those, so "Belleros", "Beleros" and
"Bel Eros the Elder" can be one group though the last two are not close to each other.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from dmbot.memory.link import same_name, within
from dmbot.memory.sounds import sound_codes

MIN_LETTERS = 5  # shorter names are matched only when they are the same letters
SHORT_NAME = 8  # up to this long, one letter may differ; longer, two
MIN_SOUND_LETTERS = 4  # shorter names sound alike too easily ("Kai", "Kay")
ARTICLES = ("the", "a", "an")
SMALL_WORDS = frozenset(ARTICLES) | {"of", "and"}  # not enough to tie a name to another


@dataclass(frozen=True, slots=True)
class Name:
    """One confirmed name: its id, its spelling, and how often it was heard."""

    id: str
    name: str
    mentions: int = 0


def _fold(text: str) -> str:
    """Lower case, without accents."""
    plain = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in plain if not unicodedata.combining(c))


def words(name: str) -> tuple[str, ...]:
    """The name's words, lower case, letters and digits only, without a leading article."""
    found = [w for w in re.split(r"[^0-9a-z]+", _fold(name)) if w]
    if len(found) > 1 and found[0] in ARTICLES:
        found = found[1:]
    return tuple(found)


def squashed(name: str) -> str:
    """The name as one run of letters: spaces, hyphens and the leading article gone."""
    return "".join(words(name))


def _contained(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Whether the shorter name is the longer one's whole words, in a row ("belleros" in
    "belleros the elder"). Not when it is only small words, or one short word."""
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) == len(long) or all(w in SMALL_WORDS for w in short):
        return False
    if len(short) == 1 and len(short[0]) < MIN_SOUND_LETTERS:
        return False  # "Al" in "Al the Smith"
    n = len(short)
    return any(long[i : i + n] == short for i in range(len(long) - n + 1))


@dataclass(frozen=True, slots=True)
class _Tidy:
    words: tuple[str, ...]
    run: str
    # The whole name as one run and, when it has a "the" or "of" part, the run before it
    # ("Bel Eros the Elder" is also "beleros"), each with its sound codes.
    forms: tuple[tuple[str, frozenset[str]], ...]


def _form(run: str) -> tuple[str, frozenset[str]]:
    return run, frozenset(sound_codes(run)) if len(run) >= MIN_SOUND_LETTERS else frozenset()


LONGEST_SPELLED = 60  # longer runs are filed only as they are: the work grows with length squared
LONGEST_TWO_OFF = 24  # and two letters off is looked for only up to here
MAX_FILE = 200  # names under one spelling or sound; more than this is no clue to anything


def _spellings(run: str) -> set[str]:
    """The run itself and the runs with one letter taken out (and, for a run long enough to be
    two letters off a longer name, two letters out): two names a letter or two apart share one."""
    found = {run}
    if len(run) < MIN_LETTERS or len(run) > LONGEST_SPELLED:
        return found  # (a very long name is only ever the same letters as another)
    once = {run[:k] + run[k + 1 :] for k in range(len(run))}
    found |= once
    if SHORT_NAME - 1 <= len(run) <= LONGEST_TWO_OFF:
        found |= {r[:k] + r[k + 1 :] for r in once for k in range(len(r))}
    return found


def _tidy(name: str) -> _Tidy:
    ws = words(name)
    run = "".join(ws)
    forms = [_form(run)]
    for at, w in enumerate(ws):
        if at and w in SMALL_WORDS:
            head = "".join(ws[:at])
            if len(head) >= MIN_SOUND_LETTERS:  # ("Al the Smith" is not "Al")
                forms.append(_form(head))
            break
    return _Tidy(ws, run, tuple(forms))


def _close(a: tuple[str, frozenset[str]], b: tuple[str, frozenset[str]], *, loose: bool) -> bool:
    (x, xs), (y, ys) = a, b
    if not x or not y:
        return False
    if x == y:
        return True
    if not loose or x[0] != y[0] or any(c.isdigit() for c in x + y):
        return False
    if min(len(x), len(y)) < MIN_LETTERS:
        return False  # "Bryn" and "Bran" are two people
    if within(x, y, 1 if max(len(x), len(y)) <= SHORT_NAME else 2):
        return True
    return bool(xs & ys)


def _alike(a: _Tidy, b: _Tidy) -> bool:
    # The same number of words, more than one, with one of them different: only a likely
    # respelling of a long word counts ("Oskar Vance"/"Oskar Vence", not "Mira Stone"/"Kira
    # Stone", "Goblin Cave A"/"Goblin Cave B"), the rule the linker already uses.
    loose = not (
        len(a.words) == len(b.words) > 1 and not same_name(" ".join(a.words), " ".join(b.words))
    )
    if any(_close(x, y, loose=loose) for x in a.forms for y in b.forms):
        return True
    return bool(a.run) and bool(b.run) and _contained(a.words, b.words)


def look_alike(a: str, b: str) -> bool:
    """Whether two spellings may be one name."""
    return _alike(_tidy(a), _tidy(b))


def pair(a: str, b: str) -> tuple[str, str]:
    """The two ids in the order the "Not the same" answers keep them."""
    return (a, b) if a < b else (b, a)


def by_use(group: Iterable[Name]) -> list[Name]:
    """The most heard name first (the one suggested under "Keep which name?"), then A to Z."""
    return sorted(group, key=lambda n: (-n.mentions, _fold(n.name), n.id))


def find(names: Iterable[Name], *, apart: Collection[tuple[str, str]] = ()) -> list[list[Name]]:
    """Groups of two or more names that may be one: the biggest group first, and inside a group
    the most heard name first. `apart` holds the pairs (as `pair` orders them) the DM said are
    not the same: they are never joined directly, though two names may still land in one group
    through a third."""
    items = by_use(names)
    tidy = [_tidy(n.name) for n in items]
    parent = list(range(len(items)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # Only names that could match are compared (5000 names must stay quick). A letter or two
    # apart means the two share a spelling with one (or two) letters taken out ("symmetric
    # delete"), so names are filed under those spellings and under their sound codes; pairs
    # meet only inside a file. Containment is found by looking up each name's whole-word runs.
    candidates: set[tuple[int, int]] = set()
    files: dict[str, list[int]] = {}
    for i, t in enumerate(tidy):
        for run, sounds in t.forms:
            if not run:
                continue
            for key in _spellings(run):
                files.setdefault(key, []).append(i)
            for code in sounds:
                files.setdefault("~" + code, []).append(i)
    # Containment: look each name's whole-word runs up among the other names' words, so a
    # common word ("Captain") never makes thousands of pairs.
    by_words: dict[tuple[str, ...], list[int]] = {}
    for i, t in enumerate(tidy):
        by_words.setdefault(t.words, []).append(i)
    # A name inside two or more longer names is a hub ("Waterdeep" in "Lord of Waterdeep" and
    # "Waterdeep Harbor"; "Captain" in three captains), not a spelling of any of them, so it ties
    # none of them together. It links only when exactly one longer name holds it.
    holders: dict[int, set[int]] = {}
    for i, t in enumerate(tidy):
        n = len(t.words)
        for size in range(1, n):
            for at in range(n - size + 1):
                for j in by_words.get(t.words[at : at + size], ()):
                    if _contained(t.words, tidy[j].words):
                        holders.setdefault(j, set()).add(i)
    contained_pairs = [(i, j) for j, over in holders.items() if len(over) == 1 for i in over]

    def add(i: int, j: int) -> None:
        if i != j:
            candidates.add((i, j) if i < j else (j, i))

    for members in files.values():
        if len(members) > MAX_FILE:  # a spelling this common says nothing
            continue
        for x, i in enumerate(members):
            for j in members[x + 1 :]:
                add(i, j)
    for i, j in contained_pairs:
        add(i, j)
    for i, j in candidates:
        if pair(items[i].id, items[j].id) in apart:
            continue
        if _alike(tidy[i], tidy[j]):
            parent[root(i)] = root(j)
    together: dict[int, list[Name]] = {}
    for i, item in enumerate(items):
        together.setdefault(root(i), []).append(item)
    found = [g for g in together.values() if len(g) > 1]
    found.sort(key=lambda g: (-len(g), -sum(n.mentions for n in g), _fold(g[0].name)))
    return found
