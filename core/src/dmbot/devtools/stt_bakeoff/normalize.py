"""Text normalizing for scoring, with each token keeping its word's confidence.

The rules match docs/test-scripts: ignore capitals, punctuation, hyphens and
apostrophes; expand common contractions; spell numbers as digits ("twenty three" and
"23" both become "23"; "d twenty" and "D 20" become "d20").
"""

from __future__ import annotations

import re
from collections.abc import Iterable

Token = tuple[str, float | None]  # (token, confidence of the word it came from)

CONTRACTIONS = {
    "it's": "it is",
    "you're": "you are",
    "he'll": "he will",
    "she'll": "she will",
    "they'll": "they will",
    "we'll": "we will",
    "i'll": "i will",
    "she's": "she is",
    "he's": "he is",
    "that's": "that is",
    "what's": "what is",
    "let's": "let us",
    "anyone's": "anyone is",
    "i'm": "i am",
    "i've": "i have",
    "we've": "we have",
    "don't": "do not",
    "can't": "cannot",
    "won't": "will not",
}
_UNITS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_DIE = re.compile(r"^d(\d+)$")


def _min_conf(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _word_tokens(word: str, conf: float | None) -> list[Token]:
    w = word.casefold().replace("’", "'").replace("‘", "'")
    out: list[Token] = []
    for part in re.split(r"[\s\-–—/]+", w):
        part = part.strip('.,!?;:"()[]{}…')
        if not part:
            continue
        part = CONTRACTIONS.get(part, part)
        for piece in part.split():
            piece = re.sub(r"[^a-z0-9]", "", piece)  # apostrophes and stray marks
            if piece:
                out.append((piece, conf))
    return out


def _merge_numbers(tokens: list[Token]) -> list[Token]:
    out: list[Token] = []
    i = 0
    while i < len(tokens):
        tok, conf = tokens[i]
        if tok in _TENS:
            value = _TENS[tok]
            if i + 1 < len(tokens) and tokens[i + 1][0] in _UNITS and _UNITS[tokens[i + 1][0]] < 10:
                value += _UNITS[tokens[i + 1][0]]
                conf = _min_conf(conf, tokens[i + 1][1])
                i += 1
            out.append((str(value), conf))
        elif tok in _UNITS:
            out.append((str(_UNITS[tok]), conf))
        else:
            out.append((tok, conf))
        i += 1
    # Dice: "d" followed by a number becomes one token ("d 20" -> "d20").
    merged: list[Token] = []
    j = 0
    while j < len(out):
        tok, conf = out[j]
        if tok == "d" and j + 1 < len(out) and out[j + 1][0].isdigit():
            merged.append(("d" + out[j + 1][0], _min_conf(conf, out[j + 1][1])))
            j += 2
            continue
        merged.append((tok, conf))
        j += 1
    return merged


def normalize_words(words: Iterable[tuple[str, float | None]]) -> list[Token]:
    """Normalize a service's words, keeping each word's confidence on its tokens."""
    tokens: list[Token] = []
    for word, conf in words:
        tokens.extend(_word_tokens(word, conf))
    return _merge_numbers(tokens)


def normalize(text: str) -> list[str]:
    """Normalize plain text to scoring tokens."""
    return [t for t, _ in normalize_words([(text, None)])]


def is_die(token: str) -> bool:
    return bool(_DIE.match(token))
