"""Sound codes: a rough key for how a name sounds, so spellings that sound alike meet.

"Belleros", "Bellaros" and "bell or us" all code to PLRS; "Cerric" and "sair ick" to SRK.
Used to *find* candidate names quickly (the Transcript Cleaner scores them properly
afterwards), so it errs on the side of matching.

Modelled on Double Metaphone, simplified and tuned for invented fantasy names read the
English way: vowels are dropped except at the start, silent letters (initial "h" in
"Hrothgar", "w" in "Sorrowmere") are dropped, and letters with two likely sounds give
more than one code: "th" at the start ("thin" or "Thomas"), a soft "g" ("Gerald" or
"Gerda"), "ch" ("chair" or "chorus"), and a "y"
before a vowel inside a word, which may or may not be heard. Words are joined first,
because speech-to-text often splits a name it doesn't know ("Ka Zeth").

Latin letters only: other scripts give no code (exact spelling still matches them).
Pure Python, no database: the in-memory lookup codes every heard word with the same
function as the stored names, so both sides always agree.
"""

from __future__ import annotations

import unicodedata

VOWELS = frozenset("aeiou")
_SOFT = frozenset("eiy")  # c and g before these sound like s and j
MAX_CODES = 4
# Letters NFKD doesn't split into a plain letter plus an accent.
_SPELLED_OUT = str.maketrans(
    {"æ": "ae", "œ": "oe", "ø": "o", "ł": "l", "đ": "d", "ð": "th", "þ": "th", "ı": "i", "ß": "ss"}
)


def _letters(text: str) -> str:
    folded = text.casefold().translate(_SPELLED_OUT)
    decomposed = unicodedata.normalize("NFKD", folded)
    return "".join(c for c in decomposed if "a" <= c <= "z")


def _is_vowel(word: str, i: int) -> bool:
    """A vowel, counting "y" when no vowel follows it ("Brynwater", "Ysolde")."""
    if i >= len(word):
        return False
    c = word[i]
    if c in VOWELS:
        return True
    return c == "y" and not (i + 1 < len(word) and word[i + 1] in VOWELS)


def _vowel_after_h(word: str, i: int) -> bool:
    """Is the next letter that isn't "h" a vowel ("wh" in "when")?"""
    while i < len(word) and word[i] == "h":
        i += 1
    return i < len(word) and word[i] in VOWELS


class _Codes:
    """Every spelling variant so far (at most MAX_CODES), extended one sound at a time."""

    def __init__(self) -> None:
        self.variants = [""]
        self.after_vowel = True  # a vowel since the last sound

    def add(self, *options: str) -> None:
        """Append one sound; several options mean "could be any of these"."""
        grown: list[str] = []
        for variant in self.variants:
            for option in options:
                # The same sound twice with no vowel between is heard once ("dt").
                if option and not self.after_vowel and variant.endswith(option):
                    option = ""
                grown.append(variant + option)
        self.variants = list(dict.fromkeys(grown))[:MAX_CODES]
        self.after_vowel = False

    def vowel(self) -> None:
        self.after_vowel = True


def sound_codes(text: str) -> tuple[str, ...]:
    """Up to four codes for how `text` sounds, the most likely first."""
    word = _letters(text)
    if not word:
        return ()
    for prefix in ("kn", "gn", "pn", "wr", "ps"):
        if word.startswith(prefix):
            word = word[1:]
            break
    if word.startswith("x"):
        word = "s" + word[1:]
    if len(word) > 1 and word[0] == "h":
        word = word[1:]  # "Hrothgar" = "Rothgar", and "Hal" sounds much like "Al"
    elif word.startswith("rh"):
        word = "r" + word[2:]

    codes = _Codes()
    i = 0
    if _is_vowel(word, 0):
        codes.add("A")  # every name that starts with a vowel sound starts the same way
        codes.vowel()
        i = 1
    while i < len(word):
        c = word[i]
        nxt = word[i + 1] if i + 1 < len(word) else ""
        after = word[i + 2 : i + 3]
        if _is_vowel(word, i):
            codes.vowel()
            i += 1
            continue
        if c == nxt and after == "h" and c in "pts":
            i += 1  # "Sapphire", "Matthew": the pair sounds as the "ph" or "th" alone
            continue
        pair_starts_digraph = c in "pts" and nxt == "h"
        if (
            i > 0
            and c == word[i - 1]
            and not (c == "c" and nxt in _SOFT)
            and not pair_starts_digraph
        ):
            i += 1  # doubled letters sound once ("Belleros"), but "accent" is k-s
            continue
        step = 1
        if c == "b":
            codes.add("P")
        elif c == "c":
            if nxt == "h":
                codes.add("X", "K")  # "chair" or "chorus"
                step = 2
            elif nxt == "k":
                codes.add("K")
                step = 2
            elif nxt in _SOFT:
                # Always "s": a "k" option too made Cerric sound like Gorrak.
                codes.add("S")
            else:
                codes.add("K")
        elif c == "d":
            if nxt == "g" and after in _SOFT:
                codes.add("J")
                step = 2
            else:
                codes.add("T")
        elif c in "fv":
            codes.add("F")
        elif c == "g":
            if nxt == "h":
                if i == 0:
                    codes.add("K")
                step = 2  # "gh" inside a word is silent or "f"; leave it out
            elif nxt == "n":
                codes.add("N")
                step = 2
            elif nxt in _SOFT:
                codes.add("J", "K")  # "Gerald" or "Gerda"
            else:
                codes.add("K")
        elif c == "h":
            pass  # silent after the first letter, or merged with the letter before
        elif c == "j":
            codes.add("J")
        elif c == "k":
            codes.add("K")
            if nxt == "w":
                step = 2  # "kw" is "qu"
        elif c == "l":
            codes.add("L")
        elif c == "m":
            codes.add("M")
        elif c == "n":
            codes.add("N")
        elif c == "p":
            if nxt == "h":
                codes.add("F")
                step = 2
            else:
                codes.add("P")
        elif c == "q":
            codes.add("K")
            if nxt == "u":
                step = 2
        elif c == "r":
            codes.add("R")
        elif c == "s":
            if nxt == "c" and after == "h":
                codes.add("SK")  # "Schmidt", "school"
                step = 3
            elif nxt == "h":
                codes.add("X")
                step = 2
            else:
                codes.add("S")
        elif c == "t":
            if nxt == "h":
                # "thin", or at the start "Thomas"; inside names it's nearly always "th"
                codes.add(*(("0", "T") if i == 0 else ("0",)))
                step = 2
            elif i > 0 and word[i + 1 : i + 3] in ("io", "ia"):
                codes.add("X")  # "Horatio"; but "Tiamat" starts with t
            elif nxt == "c" and after == "h":
                codes.add("X")
                step = 3
            else:
                codes.add("T")
        elif c == "w":
            if _vowel_after_h(word, i + 1):
                codes.add("W")
                if nxt == "h":
                    step = 2
        elif c == "x":
            codes.add("KS")
        elif c == "y":
            # A vowel follows (or _is_vowel would have skipped it): "Yara" has a y
            # sound, but mid-word it's often just a vowel ("leery el" for "Lirael").
            codes.add(*(("Y",) if i == 0 else ("Y", "")))
        elif c == "z":
            codes.add("S")
        i += step
    return tuple(v for v in codes.variants if v)


def word_runs(words: list[str], longest: int = 3) -> list[tuple[int, int, str]]:
    """Every run of 1 to `longest` neighbouring words, joined, as (start, end, text):
    a name split by speech-to-text ("Bell or us") is coded as one."""
    runs = []
    for start in range(len(words)):
        for end in range(start + 1, min(len(words), start + longest) + 1):
            runs.append((start, end, "".join(words[start:end])))
    return runs
