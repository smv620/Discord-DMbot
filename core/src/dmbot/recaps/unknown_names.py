"""Names a recap draft uses that DMbot's list doesn't have (#2027, owner decision 2026-10-19).

Three cases, in this order, for a name in the draft that no list knows:
1. It is close to a known name (speech to text misheard it: "Care Dinval" for "Caer-Dineval"):
   the known spelling is put in, in code (`respell`, here).
2. It was said in the session's kept lines: the draft may keep it, and it is queued for the DM
   to confirm (`draft.kept_names`). Nothing is ever written to story memory from here.
3. Neither: it is left alone and the draft's name check fails it, as before.
A secret name is never matched, kept or printed: a word whose closest name is secret is refused.
Pure functions; nothing here touches Discord or the database."""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from dmbot.recaps.text import narration_of, words

MIN_LETTERS = 5  # shorter names are too easy to confuse with each other or with a word
MAX_DISTANCE = 2  # letters apart for a long name, ignoring spaces, hyphens and case
SHORT_NAME = 8  # letters
MAX_TO_CONFIRM = 10  # the list holds at most this many names

_RUN = re.compile(r"[A-ZÀ-Þ][\w'’-]*(?:[ ]+[A-ZÀ-Þ][\w'’-]*)*")
_POSSESSIVE = re.compile(r"(['’]s)$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Respelled:
    text: str  # the text with near misses put right
    refused: tuple[str, ...]  # joined letters of words too close to a secret name


def _letters(text: str) -> str:
    return "".join(words(text))


def _distance(a: str, b: str) -> int:
    """Letters apart, where swapping two neighbours counts once: speech to text swaps sounds
    ("Care" for "Caer")."""
    rows = [list(range(len(b) + 1))]
    for i, x in enumerate(a, start=1):
        row = [i]
        for j, y in enumerate(b, start=1):
            cost = min(rows[-1][j] + 1, row[j - 1] + 1, rows[-1][j - 1] + (x != y))
            if i > 1 and j > 1 and x == b[j - 2] and a[i - 2] == y:
                cost = min(cost, rows[-2][j - 2] + 1)
            row.append(cost)
        rows.append(row)
    return rows[-1][-1]


def _close(a: str, b: str) -> bool:
    """Names of eight letters or fewer may differ by one letter, longer ones by two: with two,
    a short name matches too many ordinary words ("Brave" for "Brand")."""
    if a == b or len(a) < MIN_LETTERS or len(b) < MIN_LETTERS or a[0] != b[0]:
        return False
    return _distance(a, b) <= (1 if max(len(a), len(b)) <= SHORT_NAME else MAX_DISTANCE)


def respell(
    text: str,
    *,
    names: Collection[str],
    secret_names: Collection[str],
    known_words: Collection[str],
    ordinary_words: Collection[str] = (),
) -> Respelled:
    """Near misses put right. `names`: the spellings to put in (the campaign's confirmed names
    and the beats' names). `known_words`: every word of those lists and the earlier recap, lower
    case: a stretch made only of these is already right. A stretch has as many words as the name
    it is compared with, begins with a capital, and shares the name's first letter."""
    known = set(known_words)
    ordinary = set(ordinary_words)  # a stretch of only these is plain English, never a name
    targets = {_letters(n): n for n in names if len(_letters(n)) >= MIN_LETTERS}
    counts = {len(words(n)) for n in targets.values()}
    secrets = [_letters(n) for n in secret_names if _letters(n)]
    out = text
    refused: list[str] = []
    for run in _RUN.finditer(narration_of(text)):
        tokens = [(m.start(), m.end()) for m in re.finditer(r"\S+", run.group())]
        for size in sorted(counts, reverse=True):
            for i in range(len(tokens) - size + 1):
                found = run.group()[tokens[i][0] : tokens[i + size - 1][1]]
                bare = _POSSESSIVE.sub("", found).strip(".,;:!?")
                mine = words(bare)
                if len(mine) != size or set(mine) <= known | ordinary:
                    continue
                joined = _letters(bare)
                near = [(_distance(joined, s), s) for s in secrets if _close(joined, s)]
                best = [
                    (_distance(joined, letters), spelled)
                    for letters, spelled in targets.items()
                    if len(words(spelled)) == size and _close(joined, letters)
                ]
                if near and (not best or min(near)[0] <= min(best)[0]):
                    refused.append(joined)  # the closest name is secret: say nothing about it
                elif best:
                    spelled = min(best)[1]
                    out = re.sub(
                        rf"(?<![\w'’-]){re.escape(bare)}(?![\w-])", lambda _m, s=spelled: s, out
                    )
    return Respelled(out, tuple(refused))


def to_confirm(*groups: Sequence[str]) -> list[str]:
    """The names to show the DM, once each (any case), at most `MAX_TO_CONFIRM`."""
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for name in group:
            key = " ".join(words(name))
            if key and key not in seen:
                seen.add(key)
                out.append(name)
    return out[:MAX_TO_CONFIRM]
