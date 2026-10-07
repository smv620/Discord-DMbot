"""Scores a transcript against its read-aloud script, the way docs/test-scripts/README.md
asks a tester to: Part 1 words wrong, missing and added (the whisper left out), how many
of Part 2's 12 terms came out right, the whispered sentence, and errors on the first word
after a dramatic pause (#121)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from dmbot.devtools.replay.script import Part, Script

# Part 2's terms and the spellings the README accepts for each.
TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Bryn Shander", ("Brin Shander", "Bryn Shandar")),
    ("Ten-Towns", ("Ten Towns",)),
    ("Targos", ("Targus",)),
    ("Easthaven", ("East Haven",)),
    ("Detect Magic", ()),
    ("Dexterity saving throw", ()),
    ("frost giant", ()),
    ("Auril", ("Aurel",)),
    ("Frostmaiden", ("Frost Maiden",)),
    ("longsword", ("long sword",)),
    ("Lonelywood", ("Lonely Wood",)),
    ("Caer-Dineval", ("Care Dineval", "Kair Dineval")),
)

_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _number(n: int) -> list[str]:
    """0 to 99 in words: 23 is "twenty three", as "twenty-three" normalises."""
    if n < 20:
        return [_ONES[n]]
    tens, ones = divmod(n, 10)
    return [_TENS[tens - 2]] + ([_ONES[ones]] if ones else [])


def words(text: str) -> list[str]:
    """Words compared the README's way: ignore capitals, punctuation and hyphens;
    "it's" = "it is"; "3" = "three"; a die like "d20" = "d 20" = "d twenty"."""
    text = text.casefold().replace("\u2019", "'")
    text = re.sub(r"\bit is\b", "it's", text)
    text = re.sub(r"[-\u2010-\u2015]", " ", text)
    text = re.sub(r"[^\w\s']", " ", text).replace("'", "")
    text = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", text)  # d20 -> d 20
    out: list[str] = []
    for word in text.split():
        out.extend(_number(int(word)) if word.isdigit() and int(word) < 100 else [word])
    return out


@dataclass(frozen=True, slots=True)
class Step:
    """One step of the alignment: a script word heard right, misheard, missed, or a
    word heard that the script doesn't have."""

    kind: str  # "ok", "wrong", "missing" or "added"
    ref: int | None  # index into the script's words
    hyp: int | None  # index into the heard words


def align(ref: Sequence[str], hyp: Sequence[str]) -> list[Step]:
    """The cheapest way to turn the script into what was heard (word edit distance)."""
    n, m = len(ref), len(hyp)
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0] = i
    for j in range(1, m + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            same = ref[i - 1] == hyp[j - 1]
            cost[i][j] = min(
                cost[i - 1][j - 1] + (0 if same else 1),
                cost[i - 1][j] + 1,
                cost[i][j - 1] + 1,
            )
    steps: list[Step] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and cost[i][j] == cost[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            kind = "ok" if ref[i - 1] == hyp[j - 1] else "wrong"
            steps.append(Step(kind, i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and cost[i][j] == cost[i - 1][j] + 1:
            steps.append(Step("missing", i - 1, None))
            i -= 1
        else:
            steps.append(Step("added", None, j - 1))
            j -= 1
    steps.reverse()
    return steps


def merge_splits(steps: list[Step], ref: Sequence[str], hyp: Sequence[str]) -> list[Step]:
    """One word heard as two ("long sword") or two heard as one ("frontdoor") is one
    error, as a person scoring by hand counts it, not a wrong word plus a missing or
    added one."""
    out: list[Step] = []
    i = 0
    while i < len(steps):
        if i + 1 < len(steps):
            a, b = steps[i], steps[i + 1]
            kinds = {a.kind, b.kind}
            # Two script words heard as one.
            if kinds == {"wrong", "missing"} and a.ref is not None and b.ref is not None:
                heard = a.hyp if a.hyp is not None else b.hyp
                assert heard is not None
                if hyp[heard] == ref[a.ref] + ref[b.ref]:
                    first, second = (a, b) if a.kind == "wrong" else (b, a)
                    out += sorted(
                        (first, Step("ok", second.ref, None)), key=lambda step: step.ref or 0
                    )
                    i += 2
                    continue
            # One script word heard as two.
            if kinds == {"wrong", "added"} and a.hyp is not None and b.hyp is not None:
                word = a.ref if a.ref is not None else b.ref
                assert word is not None
                if ref[word] == hyp[a.hyp] + hyp[b.hyp]:
                    out.append(a if a.kind == "wrong" else b)
                    i += 2
                    continue
        out.append(steps[i])
        i += 1
    return out


@dataclass(slots=True)
class PartScore:
    total: int = 0
    wrong: int = 0
    missing: int = 0
    added: int = 0

    @property
    def errors(self) -> int:
        return self.wrong + self.missing + self.added


@dataclass(slots=True)
class Score:
    part1: PartScore
    terms_right: list[str]
    terms_missed: list[str]
    whisper: str  # "all", "part" or "missing"
    pauses: int  # dramatic pauses in the script
    pauses_split: int  # pauses where a new piece of speech started
    first_word_errors: list[str] = field(default_factory=list)  # after a split pause

    @property
    def terms_total(self) -> int:
        return len(self.terms_right) + len(self.terms_missed)


def score(script: Script, pieces: Sequence[str]) -> Score:
    """`pieces`: the text of each piece of speech, in order (empty if nothing was heard)."""
    ref_words: list[str] = []
    ref_index: list[int] = []  # script word for each normalised word
    for index, word in enumerate(script.words):
        for part in words(word.text):
            ref_words.append(part)
            ref_index.append(index)
    hyp: list[str] = []
    piece_starts: set[int] = set()
    for text in pieces:
        heard = words(text)
        if heard:
            piece_starts.add(len(hyp))
        hyp.extend(heard)
    steps = merge_splits(align(ref_words, hyp), ref_words, hyp)

    part1 = PartScore(total=script.count(Part.ONE))
    whisper_heard = whisper_total = 0
    first_errors: list[str] = []
    split = 0
    last_part = script.words[0].part
    # A script word split by normalising ("Ten-Towns") is wrong if any piece of it is.
    wrong_words: set[int] = set()
    for step in steps:
        if step.ref is not None and step.kind != "ok":
            wrong_words.add(ref_index[step.ref])
    counted: set[int] = set()
    consumed = 0  # heard words aligned so far
    added_from: int | None = None  # where a run of added words began
    for step in steps:
        next_heard = consumed
        before = consumed if added_from is None else added_from
        if step.hyp is not None:
            consumed = step.hyp + 1
        if step.ref is None:
            if added_from is None:
                added_from = before
            if last_part in (Part.ONE, Part.WHISPER):  # after the whisper is still Part 1
                part1.added += 1
            continue
        added_from = None
        index = ref_index[step.ref]
        word = script.words[index]
        last_part = word.part
        if index in counted:
            continue
        counted.add(index)
        # Where this word is (or would be, if it was lost) among the heard words.
        here = step.hyp if step.hyp is not None else next_heard
        bad = index in wrong_words
        if word.part is Part.ONE and bad:
            if step.kind == "missing":
                part1.missing += 1
            else:
                part1.wrong += 1
        if word.part is Part.WHISPER:
            whisper_total += 1
            whisper_heard += not bad
        # The pause split the speech if a new piece starts right here (a lost first
        # word still counts: that is what #121 looks like).
        if word.after_pause and any(before <= start <= here for start in piece_starts):
            split += 1
            if bad:
                first_errors.append(word.text)
    if whisper_total == 0:
        whisper = "n/a"
    elif whisper_heard == whisper_total:
        whisper = "all"
    else:
        whisper = "part" if whisper_heard else "missing"

    heard_text = f" {' '.join(hyp)} "
    right: list[str] = []
    missed: list[str] = []
    in_script = f" {' '.join(ref_words)} "
    for term, variants in TERMS:
        if f" {' '.join(words(term))} " not in in_script:
            continue  # not a term this script uses
        found = any(f" {' '.join(words(s))} " in heard_text for s in (term, *variants))
        (right if found else missed).append(term)
    return Score(
        part1=part1,
        terms_right=right,
        terms_missed=missed,
        whisper=whisper,
        pauses=script.pauses,
        pauses_split=split,
        first_word_errors=first_errors,
    )
