"""Sound codes: a rough key for how a name sounds, so spellings that sound alike meet.

"Belleros", "Bellaros" and "bell or us" all code to PLRS; "Cerric" and "sair ick" to SRK.
Used to *find* candidate names quickly (the Transcript Cleaner scores them properly
afterwards), so it errs on the side of matching.

Modelled on Double Metaphone, simplified and tuned for invented fantasy names read the
English way: vowels are dropped except at the start, silent letters (initial "h" in
"Hrothgar", "w" in "Sorrowmere") are dropped, and letters with two likely sounds give a
second code ("th" at the start, as in "thin" or "Thomas"; a "y" before a vowel inside a
word, which may or may not be heard). Words are joined first, because
speech-to-text often splits a name it doesn't know ("Ka Zeth").

Pure Python, no database: the in-memory lookup codes every heard word with the same
function as the stored names, so both sides always agree.
"""

from __future__ import annotations

import unicodedata

VOWELS = frozenset("aeiou")
_SOFT = frozenset("eiy")  # c and g before these sound like s and j


def _letters(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if "a" <= c <= "z")


def _is_vowel(word: str, i: int) -> bool:
    """A vowel, counting "y" when no vowel follows it ("Brynwater", "Ysolde")."""
    if i >= len(word):
        return False
    c = word[i]
    if c in VOWELS:
        return True
    return c == "y" and not _is_vowel_letter(word, i + 1)


def _is_vowel_letter(word: str, i: int) -> bool:
    return i < len(word) and word[i] in VOWELS


def _next_skipping_h(word: str, i: int) -> int:
    while i < len(word) and word[i] == "h":
        i += 1
    return i


def sound_codes(text: str) -> tuple[str, ...]:
    """One or two codes for how `text` sounds (the second only when it differs)."""
    word = _letters(text)
    if not word:
        return ()
    for prefix in ("kn", "gn", "pn", "wr", "ps"):
        if word.startswith(prefix):
            word = word[1:]
            break
    if word.startswith("x"):
        word = "s" + word[1:]
    first = word[0]
    if first == "h" and len(word) > 1 and not _is_vowel(word, 1):
        word = word[1:]  # "Hrothgar" sounds like "Rothgar"
    elif first == "r" and word.startswith("rh"):
        word = "r" + word[2:]

    primary: list[str] = []
    alternate: list[str] = []

    def add(p: str, a: str | None = None) -> None:
        primary.append(p)
        alternate.append(p if a is None else a)

    i = 0
    if _is_vowel(word, 0):
        add("A")  # every name that starts with a vowel sound starts the same way
        i = 1
    while i < len(word):
        c = word[i]
        nxt = word[i + 1] if i + 1 < len(word) else ""
        if i > 0 and c == word[i - 1] and c != "c":
            i += 1  # doubled letters sound once ("Belleros")
            continue
        if _is_vowel(word, i):
            i += 1
            continue
        step = 1
        if c == "b":
            add("P")
        elif c == "c":
            if nxt == "h":
                add("X", "K")  # "chair" or "chorus"
                step = 2
            elif nxt == "k":
                add("K")
                step = 2
            elif nxt in _SOFT:
                add("S")
            else:
                add("K")
        elif c == "d":
            if nxt == "g" and word[i + 2 : i + 3] in _SOFT and word[i + 2 : i + 3]:
                add("J")
                step = 2
            else:
                add("T")
        elif c in "fv":
            add("F")
        elif c == "g":
            if nxt == "h":
                if i == 0:
                    add("K")
                step = 2  # "gh" inside a word is silent or "f"; leave it out
            elif nxt == "n":
                add("N")
                step = 2
            elif nxt in _SOFT:
                add("J", "K")  # "Gerald" or "Gerda"
            else:
                add("K")
        elif c == "h":
            pass  # silent after the first letter, or merged with the letter before
        elif c == "j":
            add("J")
        elif c == "k":
            add("K")
            if nxt == "w":
                step = 2  # "kw" is "qu"
        elif c == "l":
            add("L")
        elif c == "m":
            add("M")
        elif c == "n":
            add("N")
        elif c == "p":
            if nxt == "h":
                add("F")
                step = 2
            else:
                add("P")
        elif c == "q":
            add("K")
            if nxt == "u":
                step = 2
        elif c == "r":
            add("R")
        elif c == "s":
            if nxt == "h":
                add("X")
                step = 2
            else:
                add("S")
        elif c == "t":
            if nxt == "h":
                # "thin", or at the start "Thomas"; inside names it's nearly always "th"
                add("0", "T" if i == 0 else None)
                step = 2
            elif word[i + 1 : i + 3] in ("io", "ia"):
                add("X")
            elif nxt == "c" and word[i + 2 : i + 3] == "h":
                add("X")
                step = 3
            else:
                add("T")
        elif c == "w":
            if _is_vowel_letter(word, _next_skipping_h(word, i + 1)):
                add("W")
                if nxt == "h":
                    step = 2
        elif c == "x":
            add("KS")
        elif c == "y":
            # A vowel follows (or _is_vowel would have skipped it): "Yara" has a y
            # sound, but mid-word it's often just a vowel ("leery el" for "Lirael").
            if i == 0:
                add("Y")
            else:
                primary.append("Y")
        elif c == "z":
            add("S")
        i += step

    codes = ["".join(primary), "".join(alternate)]
    return tuple(dict.fromkeys(c for c in codes if c))


def word_runs(words: list[str], longest: int = 3) -> list[tuple[int, int, str]]:
    """Every run of 1 to `longest` neighbouring words, joined, as (start, end, text):
    a name split by speech-to-text ("Bell or us") is coded as one."""
    runs = []
    for start in range(len(words)):
        for end in range(start + 1, min(len(words), start + longest) + 1):
            runs.append((start, end, "".join(words[start:end])))
    return runs
