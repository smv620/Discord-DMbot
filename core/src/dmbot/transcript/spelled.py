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

from dmbot.names import not_names

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
# Table talk that is spelled with letters but is not a name (acronyms the game uses).
NOT_NAMES = frozenset(
    {
        *("npc", "pc", "pcs", "npcs", "bbeg", "tpk", "aoe", "dnd", "phb", "dmg", "xp", "hp"),
        *("ac", "dc", "dm", "gm", "ooc", "lfg", "ttrpg"),
        *("brb", "lol", "omg", "wtf", "tbh", "btw", "idk", "afk", "gg", "irl"),
        *("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"),
    }
)
# Lead-ins and fillers before an answer: they do not count against "mostly letters".
FILLERS = frozenset(
    {
        *("uh", "um", "er", "erm", "yeah", "yep", "yes", "so", "well", "okay", "ok", "right"),
        *("and", "it", "its", "it's", "is", "that", "that's", "spelled", "spelt", "like", "then"),
    }
)
SYMBOLS = {"apostrophe": "'", "dash": "-", "hyphen": "-"}
NUMBERS = {"double": 2, "triple": 3}

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
    r"\b(?i:it(?:'s|\s+is)|that(?:'s|\s+is)|spelled|spelt|spelled\s+like|it\s+is\s+spelled)\s+"
    r"(?i:spelled\s+|spelt\s+)?([A-Z][A-Za-z]+(?:['-][A-Za-z]+)*)"
    r"(?:\s+(?i:with)\s+(?i:(?P<count>a|an|one|two|double|2))\s+(?P<letter>[A-Za-z])'?s?)?",
)
_BARE = re.compile(  # "Cael with a K", with no lead-in (only believed after an ask)
    r"\b([A-Z][A-Za-z]+(?:['-][A-Za-z]+)*)\s+(?i:with)\s+(?i:(?P<count>a|an|one|two|double|2))"
    r"\s+(?P<letter>[A-Za-z])'?s?\b"
)
_COUNTS = {"two": 2, "double": 2, "2": 2}
_DIGRAPHS = frozenset({"ph", "ch", "sh", "gh", "th", "ck"})
_SOUND_ALIKE = {"k": "c", "c": "k", "s": "c", "z": "s", "f": "p", "j": "g", "g": "j"}


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
    """Say which tokens are letters, and which of those are unmistakable (a lone "I" or "A"
    is a word unless hyphen-joined)."""
    for i, t in enumerate(toks):
        low = t.text.lower()
        joined_next = (i + 1 < len(toks) and text[t.end : toks[i + 1].start].strip() == "-") or (
            i > 0 and text[toks[i - 1].end : t.start].strip() == "-"
        )
        if len(low) == 1 and low.isalpha():
            t.letter = low
            t.strong = low not in "ai" or joined_next
            t.joined = joined_next
        elif low in STRONG_NAMES:
            t.letter, t.strong = STRONG_NAMES[low], True
        elif low in WEAK_NAMES:
            t.letter, t.strong = WEAK_NAMES[low], False
        elif low in SYMBOLS:
            t.letter, t.strong = SYMBOLS[low], True
        elif low in NUMBERS or low == "capital":
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
    """Stretches of tokens that can be letters, with the modifiers between them. Punctuation
    between letters ("B, R, A") does not end a run, but a comma or full stop before a word that
    can be a letter ("N P C, a B B E G") does; a run starts and ends on an unmistakable letter."""
    runs: list[list[_Tok]] = []
    cur: list[_Tok] = []

    def close() -> None:
        while cur and cur[-1].text in ",.;:!?-":
            cur.pop()
        # Only a lower-case word that can be a letter ("a", "you") is trimmed from the ends; a
        # capital ("Z E double T A") is a letter.
        while cur and cur[0].letter and not cur[0].strong and cur[0].text.islower():
            cur.pop(0)
        while cur and (
            (cur[-1].letter and not cur[-1].strong and cur[-1].text.islower())
            or cur[-1].text in ",.;:!?-"
        ):
            cur.pop()
        if cur:
            runs.append(list(cur))
        cur.clear()

    for t in toks:
        punctuation = t.text in ",.;:!?"
        ok = bool(t.letter) or t.text.lower() in ("double", "triple", "capital") or t.text == "-"
        if punctuation and cur:
            cur.append(t)
            continue
        first_break = not any(x.text in ",.;:!?" for x in cur[:-1])
        if t.letter and not t.strong and cur and cur[-1].text in ",.;:!?" and first_break:
            close()  # "N P C, a B B": the weak "a" after the first comma starts something else
        if ok:
            cur.append(t)
        elif cur:
            close()
    if cur:
        close()
    return runs


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
    if word.lower() in NOT_NAMES or not_names.is_not_name(word):
        return None  # "N.P.C.", "T.P.K.": table talk; "W-E": a pronoun, not a name (#2358)
    # Title case, but keep a capital the speaker asked for ("capital b" inside a word).
    word = word[0].upper() + word[1:] if word[0].islower() else word
    return word, first.start, last.end, strong


def _run_tokens(run: list[_Tok]) -> list[_Tok]:
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
        spoken = len([t for t in toks if t.text not in ",.;:!?-" and t.text.lower() not in FILLERS])
        if asked and len(_run_tokens(run)) * 10 < spoken * 8:
            continue  # an answer is mostly letters, not a sentence with a few in it
        out.append(Spelling(word, start, end))
    return out


def said_spelling(text: str) -> str | None:
    """ "it's spelled Brannoch" / "that's Branoch with two Ns": the word, as the speaker means it.
    "with two Ns" doubles the single N; "with a K" swaps the sound-alike letter (Cael with a K is
    Kael). A change that cannot be applied returns None: a spelling known to be wrong is never
    offered."""
    m = _SAID.search(text) or _BARE.search(text)
    if m is None:
        return None
    word = m.group(1)
    if not 3 <= len(word) <= MAX_WORD:
        return None
    count, letter = m.group("count"), m.group("letter")
    if count is None or letter is None:
        return word
    return _with_letter(word, letter.lower(), _COUNTS.get(count.lower(), 1))


def _with_letter(word: str, letter: str, times: int) -> str | None:
    low = word.lower()
    if times >= 2:  # "two Ns": that letter is doubled
        if letter * 2 in low:
            return word
        at = low.find(letter)
        if at < 0 or low.count(letter) != 1:
            return None
        return word[: at + 1] + word[at] + word[at + 1 :]
    if letter in low:
        return word  # "with a K": it already has one
    other = _SOUND_ALIKE.get(letter)
    if other is None or low.count(other) != 1:
        return None  # which letter is meant is a guess: never offer a spelling made by guessing
    at = low.find(other)
    pairs = {low[at : at + 2], low[max(at - 1, 0) : at + 1]}
    if pairs & _DIGRAPHS:
        return None  # "ph", "ch", "sh", "gh" sound as one letter
    swapped = letter.upper() if word[at].isupper() else letter
    return word[:at] + swapped + word[at + 1 :]


@dataclass(frozen=True, slots=True)
class SpelledOut:
    word: str
    user_id: int
    near: tuple[str, ...]  # capitalised words from the last few lines, closest first


_CAPITALISED = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{2,}\b")


def capitalised_words(text: str) -> list[str]:
    """Capitalised words not at the start of a sentence: likely names."""
    return _CAPITALISED.findall(text)


class SpellingWatcher:
    """Watches a table's lines for spelled-out names. In memory, bounded."""

    def __init__(self) -> None:
        self._ask_at: float | None = None
        self._asker = 0  # who asked, so their ask goes when they stop being recorded
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
            if asked or self._after_capitalised(text, now):
                found.append(SpelledOut(word, user_id, near))
        if is_ask(text):
            self._ask_at = now
            self._asker = user_id
        elif found:
            self._ask_at = None  # answered
        self._lines.append((now, user_id, text))
        return found

    def forget(self, user_id: int) -> None:
        """Someone stopped being recorded: their recent words go now, so they can neither be a
        nearby word nor make another person's spelling count."""
        self._lines = deque((x for x in self._lines if x[1] != user_id), maxlen=RECENT_LINES)
        if self._asker == user_id:
            self._ask_at = None  # their "how do you spell that" no longer opens a window

    def _near(self, now: float) -> list[str]:
        """Words from the last few lines that might be the name being spelled: capitalised ones
        first, then the other words, closest line first."""
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
            if capitalised_words(before):
                return True
        if self._lines:
            at, _uid, previous = self._lines[-1]
            if now - at <= UNPROMPTED_WINDOW_S and capitalised_words(previous):
                return True
        return False
