"""A name spelled out at the table (#2168; docs/PLAN.md, "Names"). Pure: no Discord, no database,
no AI, so every rule here is unit-tested.

Someone asks "how is that spelled?" and someone answers "B-R-A-N-N-O-C-H", "B R A N N O C H",
"bee are ay double n oh see aitch", or "it's Brannoch with two Ns". This turns what was said into
the spelling, and says whether it counts: only when it follows an ask (about 20 seconds), or is
said straight after a capitalised word (a spelling given unprompted). It never decides anything
about a name: the caller offers it to the DM, and uses it as a speech hint, nothing more.

Speech-to-text writes letters in several ways. The names of letters that are also ordinary words
("a", "i", "you", "are", "see", "be", "oh", "tea") only count inside a run that has enough
unmistakable letters, so "are you a" is never "RUA".
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass

ASK_WINDOW_S = 20.0  # an answer this soon after an ask belongs to it
UNPROMPTED_WINDOW_S = 10.0  # a spelling said right after a capitalised word, with no ask
MIN_LETTERS = 3  # fewer is not a spelling
MAX_LETTERS = 24
MAX_WORD = 40
RECENT_LINES = 6

# Letter names no ordinary sentence uses: always a letter.
STRONG_NAMES = {
    "bee": "b", "cee": "c", "dee": "d", "ef": "f", "eff": "f", "gee": "g", "aitch": "h",
    "jay": "j", "kay": "k", "ee": "e", "el": "l", "ell": "l", "em": "m", "en": "n", "ess": "s",
    "tee": "t", "vee": "v", "ex": "x", "zed": "z", "zee": "z", "ay": "a", "dubya": "w",
}  # fmt: skip
# Letter names that are also words: a letter only inside a run with enough strong letters.
WEAK_NAMES = {
    "are": "r", "you": "u", "see": "c", "sea": "c", "be": "b", "oh": "o", "eye": "i",
    "why": "y", "tea": "t", "queue": "q", "cue": "q", "pee": "p", "a": "a", "i": "i",
}  # fmt: skip
SYMBOLS = {"apostrophe": "'", "dash": "-", "hyphen": "-"}
NUMBERS = {"double": 2, "triple": 3}

_TOKEN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?|[.,;:!?]")
_ASKS = [
    re.compile(p, re.I)
    for p in (
        r"\bhow\s+(?:do|did|would|should|can)\s+(?:you|we|i)\s+spell\b",
        r"\bhow\s+(?:is|'?s|was)\s+(?:that|it|this|his|her|their)\b[^.?!]*\bspell(?:ed|t)?\b",
        r"\bhow(?:'s|\s+is)\s+that\s+spell(?:ed|t)\b",
        r"\bhow\s+d(?:o\s+)?you\s+spell\b",
        r"\b(?:can|could)\s+you\s+spell\b",
        r"\bspell\s+(?:that|it|this|his\s+name|her\s+name|their\s+name|the\s+name)\b",
        r"\bwhat(?:'s|\s+is)\s+the\s+spelling\b",
    )
]
_SAID = re.compile(
    r"\b(?:it(?:'s|\s+is)|that(?:'s|\s+is)|spelled|spelt|spelled\s+like|it\s+is\s+spelled)\s+"
    r"(?:spelled\s+|spelt\s+)?([A-Z][A-Za-z]+(?:['-][A-Za-z]+)*)"
    r"(?:\s+with\s+(?:a|an|one|two|double|2)\s+[A-Za-z]'?s?)?",
)


@dataclass(frozen=True, slots=True)
class Spelling:
    word: str  # as spelled: "Brannoch"
    start: int  # where the letters begin in the line
    end: int


def is_ask(text: str) -> bool:
    """Someone asks how something is spelled."""
    return any(p.search(text) for p in _ASKS)


@dataclass(slots=True)
class _Tok:
    text: str
    start: int
    end: int
    letter: str = ""  # the letter it stands for, if any
    strong: bool = False
    joined: bool = False  # written with a hyphen to the next ("B-R-A")


def _tokens(text: str) -> list[_Tok]:
    out: list[_Tok] = []
    for m in re.finditer(r"[A-Za-z]+(?:'[A-Za-z]+)?|[-.,;:!?]", text):
        out.append(_Tok(m.group(), m.start(), m.end()))
    return out


def _classify(toks: list[_Tok], text: str) -> None:
    for i, t in enumerate(toks):
        low = t.text.lower()
        joined_next = (i + 1 < len(toks) and text[t.end : toks[i + 1].start].strip() == "-") or (
            i > 0 and text[toks[i - 1].end : t.start].strip() == "-"
        )
        if len(low) == 1 and low.isalpha():
            t.letter = low
            # "B", "R" are unmistakable; "a" and "i" are words unless hyphen-joined or capital.
            t.strong = low not in "ai" or joined_next  # a lone "I" or "A" is a word
            t.joined = joined_next
        elif low in STRONG_NAMES:
            t.letter, t.strong = STRONG_NAMES[low], True
        elif low in WEAK_NAMES:
            t.letter, t.strong = WEAK_NAMES[low], False
        elif low in SYMBOLS:
            t.letter, t.strong = SYMBOLS[low], True
        elif low in NUMBERS or low == "capital" or low == "double":
            t.letter, t.strong = "", True  # modifiers, handled in the run


def _double_u(toks: list[_Tok]) -> list[_Tok]:
    """ "double u" and "double you" are the letter W, not a doubled U."""
    out: list[_Tok] = []
    for t in toks:
        if out and out[-1].text.lower() == "double" and t.text.lower() in ("u", "you"):
            before = out.pop()
            out.append(_Tok(f"{before.text} {t.text}", before.start, t.end, "w", True))
            continue
        out.append(t)
    return out


def _runs(toks: list[_Tok]) -> list[list[_Tok]]:
    """Stretches of tokens that can be letters, with the modifiers between them."""
    runs: list[list[_Tok]] = []
    cur: list[_Tok] = []
    for t in toks:
        ok = bool(t.letter) or t.text.lower() in ("double", "triple", "capital") or t.text == "-"
        if t.text in ",.;:!?" and cur:
            cur.append(t)  # punctuation between letters ("B, R, A") does not end a run
            continue
        if ok:
            cur.append(t)
        else:
            if cur:
                runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return [[t for t in r] for r in runs]


def _word_of(run: list[_Tok]) -> tuple[str, int, int, int] | None:
    """(word, start, end, strong letters) for one run, or None."""
    letters: list[str] = []
    capital = False
    repeat = 1
    strong = 0
    first = last = None
    for t in run:
        low = t.text.lower()
        if t.text in ",.;:!?-" and not t.letter:
            continue
        if low == "capital":
            capital = True
            continue
        if low in NUMBERS:
            repeat = NUMBERS[low]
            continue
        if not t.letter:
            continue
        ch = t.letter
        if first is None:
            first = t
        last = t
        if t.strong:
            strong += 1
        piece = ch * repeat
        repeat = 1
        if capital:
            piece = piece.upper()
            capital = False
        letters.append(piece)
    if first is None or last is None:
        return None
    count = sum(len(x) for x in letters)
    if count < MIN_LETTERS or count > MAX_LETTERS:
        return None
    if any(
        len(a) == 1 and a == b == c
        for a, b, c in zip(letters, letters[1:], letters[2:], strict=False)
    ):
        return None  # "I I I", "a a a": stuttering, unless "double" or "triple" was said
    word = "".join(letters)
    # Title case, but keep a capital the speaker asked for ("capital b" inside a word).
    word = word[0].upper() + word[1:] if word[0].islower() else word
    return word, first.start, last.end, strong


def run_tokens(run: list[_Tok]) -> list[_Tok]:
    return [t for t in run if t.text not in ",.;:!?-"]


def parse(text: str, *, asked: bool = False) -> list[Spelling]:
    """The words spelled out letter by letter in `text`. `asked`: the line follows an ask, so a
    run with fewer unmistakable letters is believed."""
    toks = _tokens(text)
    _classify(toks, text)
    toks = _double_u(toks)
    out: list[Spelling] = []
    for run in _runs(toks):
        found = _word_of(run)
        if found is None:
            continue
        word, start, end, strong = found
        need = 2 if asked else 3
        if strong < need or len(word) > MAX_WORD:
            continue
        spoken = len([t for t in toks if t.text not in ",.;:!?-"])
        if asked and len(run_tokens(run)) * 10 < spoken * 8:
            continue  # an answer is mostly letters, not a sentence with a few in it
        out.append(Spelling(word, start, end))
    return out


def said_spelling(text: str) -> str | None:
    """ "it's spelled Brannoch" / "that's Brannoch with two Ns": the word, as written."""
    m = _SAID.search(text)
    if m is None:
        return None
    word = m.group(1)
    return word if 3 <= len(word) <= MAX_WORD else None


@dataclass(frozen=True, slots=True)
class SpelledOut:
    word: str
    asked: bool  # it answered an ask (otherwise it followed a capitalised word)
    user_id: int
    near: tuple[str, ...]  # capitalised words from the last few lines, closest first


_CAPITALISED = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{2,}\b")


def capitalised_words(text: str) -> list[str]:
    """Capitalised words not at the start of a sentence: likely names."""
    return [w for w in _CAPITALISED.findall(text)]


class SpellingWatcher:
    """Watches a table's lines for spelled-out names. In memory, bounded."""

    def __init__(self) -> None:
        self._ask_at: float | None = None
        self._lines: deque[tuple[float, int, str]] = deque(maxlen=RECENT_LINES)

    def note(self, user_id: int, text: str, now: float) -> list[SpelledOut]:
        """One cleaned line, in order. Returns what it spelled out (usually nothing)."""
        asked = self._ask_at is not None and now - self._ask_at <= ASK_WINDOW_S
        found: list[SpelledOut] = []
        spoken = [s.word for s in parse(text, asked=asked)]
        said = said_spelling(text)
        if said is not None and (asked or "spell" in text.lower()):
            spoken.append(said)
        near = tuple(self._near(now))
        for word in dict.fromkeys(spoken):
            if asked:
                found.append(SpelledOut(word, True, user_id, near))
            elif self._after_capitalised(text, now):
                found.append(SpelledOut(word, False, user_id, near))
        if is_ask(text):
            self._ask_at = now
        elif found:
            self._ask_at = None  # answered
        self._lines.append((now, user_id, text))
        return found

    def _near(self, now: float) -> list[str]:
        words: list[str] = []
        for at, _uid, text in reversed(self._lines):
            if now - at > ASK_WINDOW_S * 2:
                break
            words.extend(capitalised_words(text) or [])
            words.extend(w for w in re.findall(r"\b[A-Za-z]{3,}\b", text) if w not in words)
        return words[:40]

    def _after_capitalised(self, text: str, now: float) -> bool:
        """A spelling said with no ask counts right after a capitalised word: earlier in the same
        line, or in the line just before."""
        first = parse(text)
        if first:
            before = text[: first[0].start]
            if capitalised_words(before) or re.search(r"(?<!^)\b[A-Z][a-z]{2,}", before):
                return True
        if self._lines:
            at, _uid, previous = self._lines[-1]
            if now - at <= UNPROMPTED_WINDOW_S and capitalised_words(previous):
                return True
        return False
