"""A name said aloud with its pronunciation (#2171; docs/PLAN.md, "Names"). Pure: no Discord, no
database, no AI, so every rule here is unit-tested.

Someone at the table says "it's pronounced KAY-el", "you pronounce it like Kay-el" or "say it as
Kay-el" right after (or in the same line as) a name. This finds the words and the name they belong
to, for the caller to offer to the DM as the name's "said as" for the narrator. It decides
nothing: an offer, never a change. Only a name said in its main spelling counts (an alias says
nothing about how the main name is spoken), and when more than one name was just said it
attaches to none rather than guess.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

RECENT_S = 20.0  # a name said this recently is what "it" means
MAX_PHRASE = 30  # characters of the pronunciation kept

# Only a cue that is about saying a name counts: "is pronounced X", "pronounce it X", "say it like
# X". A bare "pronounced" is also what a judge does ("pronounced him dead"), so it is not enough.
_BARE = re.compile(
    r"(?:\b(?:is|was|are|be|been|it's|that's)\s+pronounced|,\s*pronounced"
    r"|\bpronounce\s+(?:it|that|his name|her name|their name|the name))\s+(?:(?:like|as)\s+)?",
    re.I,
)
_LIKE = re.compile(
    r"\b(?:say|says)\s+(?:it|that|his name|her name|their name)\s+(?:like|as)\s+", re.I
)
_WORD = re.compile(r"[A-Za-z][A-Za-z'’]*(?:-[A-Za-z][A-Za-z'’]*)*")
_NOT_A_PRONUNCIATION = frozenset(
    (
        "it", "that", "this", "the", "a", "an", "not", "wrong", "right", "correctly",
        "differently", "how", "like", "as", "with", "in", "to", "is", "and", "but", "so",
        "because", "or", "if", "when", "then", "yes", "no", "you", "he", "she", "they", "we",
        "i", "me", "him", "her", "them", "my", "his", "their", "our", "dead", "guilty",
        "judgement", "judgment", "wrongly", "properly", "again", "twice", "once", "however",
        "over", "just", "kind", "similar", "exactly", "weird", "void", "name", "word", "loud",
        "louder", "quickly", "slowly", "loudly", "quietly", "softly", "clearly", "fast", "slow",
        "strange", "funny", "different", "same", "tomorrow", "today", "tonight", "king",
        "queen", "no", "whatever", "whichever", "however",
    )
)  # fmt: skip
# What follows a person's name in "say it like Gandalf would".
_COMPARING = frozenset(
    ("would", "could", "should", "might", "does", "did", "do", "can", "will", "is", "was", "says")
)  # fmt: skip
# Words that follow a pronunciation in a sentence: never the second syllable of one.
_FUNCTION = frozenset(
    (
        "by", "the", "and", "a", "to", "of", "in", "on", "is", "it", "as", "we", "he", "she",
        "so", "my", "you", "our", "or", "but", "if", "at", "for", "did", "do", "does", "mean",
        "was", "are", "am", "they", "me", "him", "her", "them", "his", "their", "not", "too",
    )
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Pronounced:
    entity_id: str
    phrase: str


def sound_key(phrase: str) -> str:
    """What two writings of one sound share: "Kay el", "Kay-el" and "KAY-el" are one sound."""
    return re.sub(r"[^a-z']+", "", phrase.lower())


def mentions_pronunciation(text: str) -> bool:
    """A cheap check, so most lines never pay for looking names up."""
    return bool(_BARE.search(text) or _LIKE.search(text))


def _phrase(text: str, start: int) -> tuple[str, int] | None:
    """The word after `start` (hyphens kept), plus a short second one when STT split it ("Kay
    el"). Shouted syllables ("KAY-el") are written as a name ("Kay-el")."""
    first = _WORD.match(text, start)
    if first is None or first.group().lower() in _NOT_A_PRONUNCIATION:
        return None
    # It has to look like a sound being spelled out: capitalised, or hyphenated, or in capitals
    # ("Kay el", "KAY-el", "kay-el"); a plain lower-case word is ordinary talk ("pronounce it
    # slowly", "it's pronounced similar to…").
    if not (first.group()[0].isupper() or "-" in first.group()):
        return None
    if first.group().lower().endswith("ly") and "-" not in first.group():
        return None  # an adverb, not a sound
    words, end = [first.group()], first.end()
    second = re.match(r"[ \t]+([A-Za-z]{1,4})\b(?![-'’])", text[end:])
    if (
        second is not None
        and "-" not in words[0]
        and len(words[0]) <= 4
        and second.group(1).lower() not in _FUNCTION
        and re.search(r"[aeiouy]", second.group(1), re.I)
    ):
        words.append(second.group(1))
        end += second.end()
    gap = re.match(r"[ \t]*", text[end:])
    follow = _WORD.match(text, end + (gap.end() if gap else 0))
    if follow is not None and follow.group().lower() in _COMPARING:
        return None  # "say it like Gandalf would": a comparison, not a sound
    pieces = [
        "-".join(p.capitalize() if p.isupper() and len(p) > 1 else p for p in w.split("-"))
        for w in words
    ]
    phrase = " ".join(pieces)
    if len(phrase) > MAX_PHRASE:
        return None
    return phrase[0].upper() + phrase[1:], end


def find(text: str) -> list[tuple[str, int]]:
    """(the pronunciation, where the phrase that introduced it starts) for each one in the line."""
    out: list[tuple[str, int]] = []
    for pattern in (_BARE, _LIKE):
        for m in pattern.finditer(text):
            got = _phrase(text, m.end())
            if got is not None:
                out.append((got[0], m.start()))
    return sorted(out, key=lambda x: x[1])


class PronunciationWatcher:
    """Which name was just said, so "it's pronounced…" knows what "it" is. Kept per session in
    memory only; a speaker who stops being recorded is forgotten."""

    def __init__(self) -> None:
        self._recent: deque[tuple[float, int, str]] = deque(maxlen=12)  # (time, speaker, name)

    def parse(self, text: str, raw: str | None = None) -> list[tuple[str, int]]:
        """The pronunciations in a line and where each cue starts. `text` is the cleaned line
        (the names are found in it); `raw` is the line as heard: the cleaner turns a misheard
        "Kale" into the name "Kael", so the sound is read from the raw line when it has the same
        number of finds."""
        hits = find(text)
        if raw is not None and hits:
            heard = find(raw)
            if len(heard) == len(hits):
                hits = [(h[0], at) for h, (_, at) in zip(heard, hits, strict=True)]
        return hits

    def attach(
        self,
        user_id: int,
        hits: Sequence[tuple[str, int]],
        names: Sequence[tuple[str, int]],
        now: float,
    ) -> list[Pronounced]:
        """`names`: (entity id, where in the line) of each name said in its main spelling. A
        pronunciation belongs to the one name said before it in the same line, else to the one
        name said in the last few seconds; never to a guess between two."""
        found: list[Pronounced] = []
        for phrase, at in hits:
            before = {eid for eid, where in names if where < at}
            if len(before) > 1:
                continue
            if len(before) == 1:
                target = next(iter(before))
            else:
                recent = {eid for t, _, eid in self._recent if now - t <= RECENT_S}
                if len(recent) != 1:
                    continue
                target = next(iter(recent))
            found.append(Pronounced(target, phrase))
        for eid, _ in names:
            self._recent.append((now, user_id, eid))
        return found

    def note(
        self,
        user_id: int,
        text: str,
        names: Sequence[tuple[str, int]],
        now: float,
        raw: str | None = None,
    ) -> list[Pronounced]:
        """`parse` and `attach` in one."""
        return self.attach(
            user_id, self.parse(text, raw) if names or self._recent else [], names, now
        )

    def forget(self, user_id: int) -> None:
        kept = [x for x in self._recent if x[1] != user_id]
        self._recent.clear()
        self._recent.extend(kept)
