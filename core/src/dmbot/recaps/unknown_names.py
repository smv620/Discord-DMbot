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
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass

from dmbot.memory.sounds import sound_codes
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


def _put(spelled: str) -> Callable[[re.Match[str]], str]:
    return lambda _match: spelled


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
                    # a function, so a backslash in the name is not read as an escape
                    out = re.sub(rf"(?<![\w'’-]){re.escape(bare)}(?![\w-])", _put(spelled), out)
    return Respelled(out, tuple(refused))


MIN_ALIAS_LETTERS = 4  # a confirmed other spelling may be shorter than a guessed name
MAX_SOUND_DISTANCE = 3  # letters apart, besides sounding alike, for a name not on any list
MAX_START_DISTANCE = 2  # letters apart when only the first sound is the same


def canonical(
    text: str,
    aliases: Mapping[str, str],
    *,
    names: Collection[str],
    secret_names: Collection[str],
    ordinary_words: Collection[str] = (),
    sound_names: Collection[str] | None = None,
) -> str:
    """A character called by a spelling that is not its story-memory name, put right (#2573).

    `aliases`: another spelling the DM confirmed (or a correction the DM made) -> the name in the
    story memory. The table's speech to text hears "Tavin" as "Sarek" and the beats then carry
    "Sarek" through, so the draft's name check knew it and the recap named a main character by a
    stranger's name. In a run of capitalised words:
    1. a stretch spelt exactly like an alias is the name it stands for;
    2. a single word that sounds like exactly one known name or alias (the same sound code a few
       letters apart, or the same first sound two letters apart; the spelling's first letter is
       not compared, so a soft C and an S meet) is that name, unless it is an ordinary word or
       sounds like (or is spelt near) a secret name, which is left alone and never printed.
    Only names of the recap's own list (`names`) are put in, never a secret one. `sound_names`
    narrows the names rule 2 may turn a word into (the player characters and the alias targets, so
    a new NPC is never turned into one); a token that is part of a longer known name ("Sarek
    Dawnbringer") is left alone. Pure."""
    # `names` holds every spelling the recap may use, the other spellings among them: a spelling
    # that is another name's alias is not a name of its own to match against.
    # An alias that is a part of its own name (the first name, the surname) is the familiar form of
    # it, not a misheard one: the writer is told to use it (#2627), so that spelling itself is
    # never rewritten (#2949). A spelling that only sounds like it still is, as before.
    familiar = {
        _letters(a) for a, c in aliases.items() if words(a) and set(words(a)) <= set(words(c))
    }
    alias_letters = {_letters(a) for a in aliases}
    targets = {_letters(n): n for n in names if _letters(n) and _letters(n) not in alias_letters}
    real = set(targets.values())
    exact = {
        _letters(a): c
        for a, c in aliases.items()
        if len(_letters(a)) >= MIN_ALIAS_LETTERS and c in real and _letters(a) not in familiar
    }
    sounds: dict[str, set[tuple[str, str]]] = {}
    starts: dict[str, set[tuple[str, str]]] = {}  # by the first sound, for a close spelling
    sound_real = real if sound_names is None else real & set(sound_names)
    for spelled, name in [
        *((a, c) for a, c in aliases.items() if c in sound_real),
        *((n, n) for n in sound_real),
    ]:
        letters = _letters(spelled)
        if len(words(spelled)) == 1 and len(letters) >= MIN_LETTERS:
            for code in sound_codes(spelled):
                sounds.setdefault(code, set()).add((letters, name))
                starts.setdefault(code[0], set()).add((letters, name))
    secrets = [_letters(n) for n in secret_names if _letters(n)]
    secret_codes = {c for n in secret_names if len(words(n)) == 1 for c in sound_codes(n)}
    ordinary = set(ordinary_words)
    out = text
    seen: set[str] = set()
    longer = sorted({_letters(n) for n in real if len(words(n)) > 1})
    for run in _RUN.finditer(narration_of(text)):
        tokens = [(m.start(), m.end()) for m in re.finditer(r"\S+", run.group())]
        # Tokens that belong to a longer known name ("Sarek Dawnbringer") are not rewritten.
        inside: set[int] = set()
        for size in {len(words(n)) for n in real if len(words(n)) > 1}:
            for i in range(len(tokens) - size + 1):
                span = run.group()[tokens[i][0] : tokens[i + size - 1][1]]
                if _letters(_POSSESSIVE.sub("", span).strip(".,;:!?")) in longer:
                    inside.update(range(i, i + size))
        for size in {len(words(a)) for a in aliases} | {1}:
            for i in range(len(tokens) - size + 1):
                if inside.intersection(range(i, i + size)):
                    continue
                found = run.group()[tokens[i][0] : tokens[i + size - 1][1]]
                bare = _POSSESSIVE.sub("", found).strip(".,;:!?")
                joined = _letters(bare)
                if (
                    len(words(bare)) != size
                    or not joined
                    or bare in seen
                    or joined in targets
                    or joined in familiar
                ):
                    continue
                target = exact.get(joined)
                if target is None and size == 1 and len(joined) >= MIN_LETTERS:
                    if set(words(bare)) <= ordinary:
                        continue
                    codes = set(sound_codes(bare))
                    if codes & secret_codes or any(_close(joined, s) for s in secrets):
                        continue
                    hits = {
                        name
                        for c in codes
                        for letters, name in sounds.get(c, ())
                        if _distance(joined, letters) <= MAX_SOUND_DISTANCE
                    } | {
                        name  # the same first sound and two letters apart: Selwin for Celwyn
                        for c in codes
                        for letters, name in starts.get(c[0], ())
                        if _distance(joined, letters) <= MAX_START_DISTANCE
                    }
                    target = next(iter(hits)) if len(hits) == 1 else None
                if target is not None:
                    seen.add(bare)
                    flexible = r"\s+".join(re.escape(w) for w in bare.split())
                    out = re.sub(rf"(?<![\w'’-]){flexible}(?![\w-])", _put(target), out)
    return _undoubled(text, out)


_DOUBLED = re.compile(r"(?<![\w'’-])([^\W\d_][\w'’-]*)([ ]+)\1(?![\w'’-])")


def _undoubled(before: str, after: str) -> str:
    """A rewrite must never leave a name followed by its own last word ("Doran Ashvale
    Ashvale", #2949): a capitalised word doubled in the result that was not doubled in the text it
    came from is put back to one. A doubling the writer made itself ("Bora Bora") is left alone."""
    if after == before:
        return after

    def fix(m: re.Match[str]) -> str:
        return m.group(1) if m.group(1)[0].isupper() and m.group(0) not in before else m.group(0)

    return _DOUBLED.sub(fix, after)


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
