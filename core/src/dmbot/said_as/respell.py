"""Speech sounds (IPA) in, a plain respelling out: "kɑzɛθ" becomes "kah-zeth" (#2316).

Pure code with no model and no network, so a table of made-up names can test it. The recogniser
(a model that hears a clip and writes IPA) is a separate step; this is the half that must be
exact, because its answer is what the DM reads, edits and saves. DMbot only suggests: the DM
decides.

How it reads the sounds:
- Each sound maps to plain English letters (`SOUNDS`). A sound the table doesn't know is dropped
  rather than guessed, and a clip with no vowel at all gives "" (the page then says DMbot couldn't
  tell, and the DM types it).
- Syllables follow the vowels. Between two vowels a single consonant starts the next syllable
  ("kah-zeth"); two or more split after the first, except a pair that can start a word in English
  ("s" or a stop before "l"/"r", "s" before a stop), which stay together ("ah-tree").
- The stressed syllable is written in capitals ("kah-ZETH"). The model's output usually has no
  stress marks; when none are given the first syllable of a word of two or more is stressed, the
  usual English choice for a name, and a one-syllable name has no capitals. The DM can change it.
- The result is at most `MAX_LENGTH` characters (the Names page's limit) and only letters, hyphens
  and spaces, so it always passes the same check as a typed spelling.
"""

from __future__ import annotations

MAX_LENGTH = 80  # the limit on a spelling everywhere (web/names.py SAID_AS_MAX)

# Longest first within each start, so "tʃ" is read before "t" and "aɪ" before "a".
_TWO = {
    "tʃ": "ch",
    "dʒ": "j",
    "aɪ": "eye",
    "aʊ": "ow",
    "ɔɪ": "oy",
    "eɪ": "ay",
    "oʊ": "oh",
    "əʊ": "oh",
    "ɪə": "eer",
    "ɛə": "air",
    "ʊə": "oor",
}
_VOWELS = {
    "ɑ": "ah",
    "a": "ah",
    "æ": "a",
    "ɛ": "eh",
    "e": "ay",
    "ə": "uh",
    "ɐ": "uh",
    "ʌ": "uh",
    "ɪ": "ih",
    "i": "ee",
    "ɔ": "aw",
    "ɒ": "aw",
    "o": "oh",
    "ʊ": "uu",
    "u": "oo",
    "ɜ": "er",
    "ɝ": "er",
    "ɚ": "er",
    "y": "ew",
}
_CONSONANTS = {
    "p": "p",
    "b": "b",
    "t": "t",
    "d": "d",
    "k": "k",
    "ɡ": "g",
    "g": "g",
    "f": "f",
    "v": "v",
    "θ": "th",
    "ð": "th",
    "s": "s",
    "z": "z",
    "ʃ": "sh",
    "ʒ": "zh",
    "h": "h",
    "x": "kh",
    "m": "m",
    "n": "n",
    "ŋ": "ng",
    "l": "l",
    "ɫ": "l",
    "ɹ": "r",
    "r": "r",
    "ɾ": "r",
    "j": "y",
    "w": "w",
    "ʔ": "",
}
SOUNDS = {**_TWO, **_VOWELS, **_CONSONANTS}
# Short vowels read better without the trailing h when a consonant closes the syllable: "zeth",
# "brin" (but "zeh" and "ih" where the syllable ends on the vowel). Spelled as plain English readers
# would say them.
_CLOSED = {"ɛ": "e", "ɪ": "i", "æ": "a", "ʌ": "u", "ɑ": "ah", "ɔ": "aw"}
_OPEN_END = {"aɪ": "y", "oʊ": "oh", "əʊ": "oh", "eɪ": "ay"}  # "my", "kay"
_DIPHTHONGS = frozenset({"aɪ", "aʊ", "ɔɪ", "eɪ", "oʊ", "əʊ", "ɪə", "ɛə", "ʊə"})
_STRESS = {"ˈ": True, "ˌ": False}  # primary is stress; secondary is read as unstressed
_SKIP = set("ːˑ̩̯̃ʰʲʷ˞ .‿|-‖")  # lengthening, tie and other marks that change no letter
# Pairs that can begin an English word, so they stay together as the next syllable's start.
_ONSET_PAIRS = frozenset(
    f"{a}{b}" for a in ("p", "b", "t", "d", "k", "g", "f", "ɡ") for b in ("l", "ɹ", "r")
) | frozenset({"sp", "st", "sk", "sm", "sn", "sl", "sw", "tw", "kw", "θɹ", "ʃɹ"})


def _sounds(ipa: str) -> list[tuple[str, bool, bool]]:
    """(symbol, is_vowel, stress_before) for each known sound, in order. Unknown signs are
    dropped."""
    out: list[tuple[str, bool, bool]] = []
    stressed = False
    i = 0
    while i < len(ipa):
        ch = ipa[i]
        if ch in _STRESS:
            stressed = _STRESS[ch]
            i += 1
            continue
        if ch in _SKIP:
            i += 1
            continue
        pair = ipa[i : i + 2]
        if pair in _TWO:
            sym, step = pair, 2
        elif ch in SOUNDS:
            sym, step = ch, 1
        else:
            i += 1  # not a sound DMbot knows: leave it out
            continue
        vowel = sym in _VOWELS or sym in _DIPHTHONGS
        out.append((sym, vowel, stressed and vowel))
        if vowel:
            stressed = False
        i += step
    return out


def _syllables(sounds: list[tuple[str, bool, bool]]) -> list[tuple[list[str], bool]]:
    """Syllables as (the symbols in it, whether it carries the stress mark)."""
    nuclei = [n for n, s in enumerate(sounds) if s[1]]
    if not nuclei:
        return []
    cuts = [0]
    for left, right in zip(nuclei, nuclei[1:], strict=False):
        between = [sounds[k][0] for k in range(left + 1, right)]
        if len(between) <= 1:
            cut = left + 1  # one consonant (or none): it starts the next syllable
        elif "".join(between[-2:]) in _ONSET_PAIRS and len(between) == 2:
            cut = left + 1
        elif "".join(between[-2:]) in _ONSET_PAIRS:
            cut = right - 2
        else:
            cut = right - 1
        cuts.append(cut)
    cuts.append(len(sounds))
    result = []
    for a, b in zip(cuts, cuts[1:], strict=False):
        part = sounds[a:b]
        result.append(([s[0] for s in part], any(s[2] for s in part)))
    return result


def _letters(symbols: list[str]) -> str:
    """One syllable's letters: the table's, with a short vowel's closed spelling when a consonant
    follows it in the syllable, and a final "aɪ" written "y"."""
    out = []
    for n, sym in enumerate(symbols):
        closed = n + 1 < len(symbols) and symbols[n + 1] not in _VOWELS
        last = n == len(symbols) - 1
        if sym in _CLOSED and closed:
            out.append(_CLOSED[sym])
        elif sym in _OPEN_END and last:
            out.append(_OPEN_END[sym])
        else:
            out.append(SOUNDS[sym])
    return "".join(out)


def respell(ipa: str) -> str:
    """The respelling for one word's speech sounds, or "" when no vowel was heard. Words are split
    on spaces and joined by a space, each respelled alone."""
    words = []
    for word in ipa.split():
        sylls = _syllables(_sounds(word))
        if not sylls:
            continue
        marked = [n for n, (_s, mark) in enumerate(sylls) if mark]
        loud = marked[0] if marked else (0 if len(sylls) > 1 else None)
        parts = []
        for n, (symbols, _mark) in enumerate(sylls):
            text = _letters(symbols)
            parts.append(text.upper() if n == loud else text)
        words.append("-".join(p for p in parts if p))
    return " ".join(words)[:MAX_LENGTH].rstrip("- ")
