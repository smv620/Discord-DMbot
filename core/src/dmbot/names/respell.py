"""Speech sounds (IPA) to a plain respelling, and the check on any respelling (#2316, PLAN: "How to
say it"). Pure: no model, no Discord, no database.

The recogniser that listens to the DM's clip answers in IPA; the DM wants "kah-ZETH". The table
below maps each sound to plain letters, syllables are joined by hyphens, and the stressed one is in
capitals. Recognisers of this kind often leave the stress marks out, so without a mark the stress
falls on the first syllable of a short name and the next-to-last of a longer one; the DM sees the
result and edits it before it is saved, so a wrong guess costs one edit, never a wrong ruling.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MAX_CHARS = 80  # the same limit as the database's said-as check (#1451)
_ALLOWED = re.compile(r"[A-Za-z' -]+")

STRESS = "ˈ"
SECONDARY = "ˌ"
_IGNORED = set("͡‿ʰʲʷˠˤ̩̯̥̬̪̃̚ʼ")  # tie bars, aspiration, palatalisation, nasalisation...
DOT = "."  # the recogniser's own syllable break
_SEPARATORS = {"-"}

# Longest first, so an affricate or a diphthong is one sound, not two.
_CONSONANTS = {
    "ŋɡ": "ng", "ŋg": "ng", "tʃ": "ch", "dʒ": "j", "ts": "ts", "dz": "dz",
    "p": "p", "b": "b", "t": "t", "d": "d", "k": "k", "g": "g", "ɡ": "g", "q": "k", "c": "k",
    "ɟ": "j", "ʔ": "", "f": "f", "v": "v", "θ": "th", "ð": "th", "s": "s", "z": "z", "ʃ": "sh",
    "ʒ": "zh", "h": "h", "x": "kh", "χ": "kh", "ɣ": "g", "m": "m", "n": "n", "ŋ": "ng", "ɲ": "ny",
    "l": "l", "ɫ": "l", "ɭ": "l", "ʎ": "ly", "r": "r", "ɹ": "r", "ɾ": "d", "ʀ": "r", "ɻ": "r",
    "ʁ": "r", "j": "y", "w": "w", "ʍ": "wh", "β": "b", "ç": "h", "ɕ": "sh", "ʑ": "zh",
}  # fmt: skip
# Vowels: (letters in an open syllable, letters in a closed one).
_VOWELS = {
    "aɪ": ("eye", "eye"), "aʊ": ("ow", "ow"), "eɪ": ("ay", "ay"), "oʊ": ("oh", "oh"),
    "əʊ": ("oh", "oh"), "ɔɪ": ("oy", "oy"), "ɪə": ("eer", "eer"), "ɛə": ("air", "air"),
    "ʊə": ("oor", "oor"), "ɑ": ("ah", "ah"), "ɒ": ("ah", "o"), "a": ("ah", "ah"),
    "æ": ("a", "a"), "ɛ": ("eh", "e"), "e": ("ay", "ay"), "ɪ": ("ih", "i"), "i": ("ee", "ee"),
    "ɨ": ("ih", "i"), "ɔ": ("aw", "aw"), "o": ("oh", "oh"), "ʊ": ("oo", "oo"), "u": ("oo", "oo"),
    "ʌ": ("uh", "u"), "ə": ("uh", "uh"), "ɐ": ("uh", "uh"), "ɜ": ("ur", "ur"), "ɝ": ("ur", "ur"),
    "ɚ": ("er", "er"), "y": ("ew", "ew"), "ø": ("er", "er"), "œ": ("er", "er"), "ɯ": ("oo", "oo"),
    "ɤ": ("uh", "uh"), "ɘ": ("uh", "uh"), "ɵ": ("uh", "uh"), "ʉ": ("oo", "oo"), "ɞ": ("uh", "uh"),
}  # fmt: skip

_TOKENS = sorted({*_CONSONANTS, *_VOWELS}, key=len, reverse=True)
# What can start a syllable together (the English-sounding clusters): the longest tail of the
# consonants between two vowels that is in this set starts the next syllable.
_ONSETS = {
    ("s", "p"), ("s", "t"), ("s", "k"), ("s", "l"), ("s", "m"), ("s", "n"), ("s", "w"),
    ("p", "l"), ("p", "r"), ("b", "l"), ("b", "r"), ("t", "r"), ("d", "r"), ("k", "l"),
    ("k", "r"), ("g", "l"), ("g", "r"), ("f", "l"), ("f", "r"), ("θ", "r"), ("ʃ", "r"),
    ("t", "w"), ("k", "w"), ("d", "w"), ("s", "p", "l"), ("s", "p", "r"), ("s", "t", "r"),
    ("s", "k", "r"), ("s", "k", "w"),
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class _Sound:
    ipa: str
    vowel: bool


def _sounds(word: str) -> list[_Sound | str]:
    """The word as sounds, with STRESS / SECONDARY marks kept in place. Anything unknown is
    skipped (a recogniser's rare symbol is not worth failing the whole name)."""
    text = unicodedata.normalize("NFC", word).replace(":", "ː")
    out: list[_Sound | str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in (STRESS, SECONDARY, DOT):
            out.append(ch)
            i += 1
            continue
        if ch in _IGNORED or ch in _SEPARATORS or ch == "ː" or ch.isspace():
            i += 1
            continue
        for token in _TOKENS:
            if text.startswith(token, i):
                out.append(_Sound(token, token in _VOWELS))
                i += len(token)
                break
        else:
            i += 1
    return out


def _syllables(sounds: list[_Sound | str]) -> tuple[list[list[_Sound]], int | None]:
    """Split the sounds into syllables (each with exactly one vowel) and the index of the one
    marked as stressed, if any. Consonants before the first vowel start it; between two vowels the
    longest English-like onset goes with the next vowel and the rest closes the one before; a stress
    mark or a dot is a syllable boundary of its own."""
    syllables: list[list[_Sound]] = []
    stressed: int | None = None
    pending: list[_Sound] = []  # consonants waiting for the next vowel
    mark_next = False
    loud = False  # the pending boundary is a stress mark, not just a syllable dot
    for item in sounds:
        if isinstance(item, str):
            if item == SECONDARY:
                continue  # a secondary stress doesn't change which part is shouted
            if syllables and pending:  # the mark splits the cluster: before it closes the last
                syllables[-1].extend(pending)
                pending = []
            mark_next = True
            loud = loud or item == STRESS
            continue
        if not item.vowel:
            pending.append(item)
            continue
        if syllables and pending and not mark_next:
            split = _onset_start(pending)
            syllables[-1].extend(pending[:split])
            pending = pending[split:]
        syllables.append([*pending, item])
        pending = []
        if mark_next:
            if loud:
                stressed = len(syllables) - 1
            mark_next = loud = False
    if pending:
        if syllables:
            syllables[-1].extend(pending)
        else:
            syllables.append(pending)  # no vowel at all: spelled as it is
    return syllables, stressed


def _onset_start(cluster: list[_Sound]) -> int:
    """Where, in the consonants between two vowels, the next syllable begins."""
    for start in range(max(len(cluster) - 3, 0), len(cluster)):
        tail = tuple(s.ipa.replace("ɹ", "r").replace("ɡ", "g") for s in cluster[start:])
        if len(tail) == 1 or tail in _ONSETS:
            return start
    return len(cluster) - 1


def _letters(syllable: list[_Sound]) -> str:
    nucleus = next((i for i, s in enumerate(syllable) if s.vowel), None)
    if nucleus is None:
        return "".join(_CONSONANTS[s.ipa] for s in syllable)
    closed = any(not s.vowel and _CONSONANTS[s.ipa] for s in syllable[nucleus + 1 :])
    out = []
    for i, s in enumerate(syllable):
        out.append(_VOWELS[s.ipa][1 if closed else 0] if i == nucleus else _CONSONANTS[s.ipa])
    return "".join(out)


def _default_stress(count: int) -> int:
    return 0 if count <= 2 else count - 2


def respell_word(word: str) -> str:
    """One word of IPA as "kah-ZETH". "" when nothing in it is a sound."""
    syllables, stressed = _syllables(_sounds(word))
    parts = [_letters(s) for s in syllables]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if stressed is None or stressed >= len(parts):
        stressed = _default_stress(len(parts))
    return "-".join(p.upper() if i == stressed else p for i, p in enumerate(parts))


def respell(ipa: str) -> str:
    """The recogniser's IPA (words separated by spaces) as a plain respelling; "" when there is
    nothing usable to show (no sounds, or longer than MAX_CHARS)."""
    words = [respell_word(w) for w in ipa.split()]
    return clean(" ".join(w for w in words if w)) or ""


def clean(text: str) -> str | None:
    """A respelling the DM may see and save: only letters, hyphens, spaces and apostrophes, tidy
    spaces, at most MAX_CHARS. None when it is empty or cannot be made to fit those rules (an AI
    answer with a sentence or a symbol in it is dropped, never trimmed into something odd)."""
    tidy = " ".join(text.replace("’", "'").split())
    if not tidy or len(tidy) > MAX_CHARS or not _ALLOWED.fullmatch(tidy):
        return None
    if not any(c.isalpha() for c in tidy):
        return None
    return tidy
