"""What is said at the table that moves the game clock (#965, #1117): no AI, a small and strict
phrase list, and a line that sounds like a question, a wish, a condition or a maybe is never
one. Never guessed: "you walk for a while" or "after some time" is ignored.

Two kinds of speaker. A campaign's DM clearly saying a rest starts ("we take a long rest") or
finishes ("you finish your long rest"), that the next morning has come, or how much time has
passed ("an hour later", "three days later") moves the clock (`find_clock`; the caller checks
who is speaking).
A player *suggesting* a rest ("let's take a short rest", "can we long rest?") moves nothing;
it only lets DMbot ask the DM with one button (`suggested_rest`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

Rest = Literal["short", "long"]
Action = Literal["short", "long", "dawn", "advance", "days", "days_norest"]

MAX_WORDS = 14  # a long ramble that mentions a rest isn't a ruling
MAX_AMOUNT_MIN = 24 * 60  # "twenty-four hours later" is the most it will move by itself
MAX_SKIP_DAYS = 30  # "thirty days later" is the most a skip moves by itself (#1594)
MINUTES_PER_DAY = 24 * 60


@dataclass(frozen=True, slots=True)
class Said:
    """A clock change the DM clearly said. `key` is what the same thing said twice within a
    few minutes is counted once by (a rest said as starting and as finishing is one rest)."""

    action: Action
    minutes: int | None = None  # for "advance", and for a skip of days (a whole number of days)
    ends: bool = field(
        default=False, compare=False
    )  # a rest said as finishing ("you finish your long rest"), not starting

    @property
    def key(self) -> str:
        if self.action in ("days", "days_norest"):
            return f"days{self.minutes}"  # rested or not, the same skip said twice is one
        return f"+{self.minutes}" if self.action == "advance" else self.action


_SAYS = re.compile(
    r"\b(?:we|you|you all|you guys|everyone|the party|the group)\s+"
    r"(?:(?:all|now|then|each)\s+)?"
    r"(?:take|taking|finish|finished)\s+"
    r"(?:a|an|your)\s+(short|long)\s+rest\b",
    re.IGNORECASE,
)
# A rest finishing: "you finish your long rest", "the long rest is over", "after your short
# rest" (the last only at the start of the line).
_FINISHES = re.compile(
    r"\b(?:(?:you|you all|everyone|the party|the group|we)\s+"
    r"(?:(?:all|now|then)\s+)?(?:finish|finished|complete|completed|wake from|wake up from)\s+"
    r"(?:a|an|your|the)\s+(short|long)\s+rest"
    r"|(?:the|your)\s+(short|long)\s+rest\s+(?:is over|is done|ends|has ended|finishes))\b",
    re.IGNORECASE,
)
# "After your short rest." as the whole line: "after your long rest you hear a noise" is a story
# beat, not the rest finishing.
_AFTER = re.compile(
    r"^(?:and\s+)?after\s+(?:your|the|a)\s+(short|long)\s+rest[.,!]?$", re.IGNORECASE
)
# Words that make it a question, a wish, a condition or a refusal: not something that happened.
_NOT_A_THING = (
    r"if|can|can't|cannot|couldn't|could|can not|don't|do not|won't|will not|unless|"
    r"want|wants|wanna|should|shouldn't|would|wouldn't|need|needs|"
    r"may|might|maybe|let's|lets|why|how|what|whether|try|tried|trying|no|not|never|"
    r"yesterday|ago|says|said|tells|told|will|going|gonna|instead of|rather than"
)
# Starting a rest also refuses a past or future telling ("took", "last", "tomorrow"...).
_NOT_A_REST = re.compile(
    rf"\b(?:{_NOT_A_THING}|when|before|after|until|took|had|last|earlier|later|tomorrow)\b",
    re.IGNORECASE,
)
# A rest that is still ahead, not finished: "when you finish...", "once you...".
_LATER_THAN_NOW = re.compile(r"\b(?:when|before|until|once|as soon as|while)\b", re.IGNORECASE)
_NOT_THE_TIME = re.compile(rf"\b(?:{_NOT_A_THING})\b|'ll\b", re.IGNORECASE)

_ONES = {
    w: n
    for n, w in enumerate(
        [
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
        ],
        start=1,
    )
}
_TENS = {
    w: 10 * n
    for n, w in enumerate(
        ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"], start=2
    )
}
_SMALL = "|".join(w for w in _ONES if _ONES[w] < 10)
_NUMBER_WORD = "|".join(
    [
        *(f"(?:{t})[- ](?:{_SMALL})" for t in _TENS),
        *sorted(_TENS, key=len, reverse=True),
        *sorted(_ONES, key=len, reverse=True),
    ]
)
# A time cut is how a scene starts: "Two hours later, the guards come." The words must open the
# line (after "and", "then" or "so"), because anywhere else they tell the story ("he left the
# next morning", "the guard returned ten minutes later"), which is not the clock moving.
_OPENS = r"^(?:(?:and|then|so|okay|ok|alright|um|uh|well)[, ]+){0,2}"
_AMOUNT = re.compile(
    rf"{_OPENS}(?P<n>\d{{1,4}}|{_NUMBER_WORD}|an|a|half an)\s+"
    r"(?P<unit>hours?|minutes?|mins?)\s+later\b",
    re.IGNORECASE,
)
# The cut must be followed by the narration's subject ("At dawn, you...", "The next morning, the
# party...") or stand alone as a sentence: "At dawn, meet me at the gate" is an NPC talking.
_DAWN = re.compile(
    rf"{_OPENS}(?:(?:the\s+)?next\s+morning|at\s+dawn)"
    r"(?:[.!,]?$|,?\s+(?:you|your|you all|all of you|the party|the group|everyone|we)\b)",
    re.IGNORECASE,
)
# A skip of days is a scene cut too: "Three days later, you reach the city." Like dawn, it must be
# followed by the narration's subject or end the sentence ("Two days later, meet me at the gate"
# is an NPC talking), and must open the line ("he arrived three days later" is story).
_DAYS = re.compile(
    rf"{_OPENS}(?:(?:(?P<n>\d{{1,2}}|{_NUMBER_WORD})\s+days|(?P<a>a|one)\s+day)\s+later"
    r"|(?P<w>a|one|two|three|four)\s+weeks?\s+later|(?:the\s+)?next\s+day)"
    r"(?:[.!,]?$|,?\s+(?:you|your|you all|all of you|the party|the group|everyone|we)\b)",
    re.IGNORECASE,
)
_WEEKS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4}
# The DM said the days passed without sleep: not counted as a long rest.
_WITHOUT_REST = re.compile(r"\b(?:without (?:any )?rest(?:ing)?|no rest)\b", re.IGNORECASE)
_QUOTES = re.compile(r"[\"\u201c\u201d]")
_SUGGESTS = re.compile(
    r"\b(?:let'?s|lets|can we|could we|should we|we should|we could|shall we|how about|"
    r"we need|i think we (?:should|need)|can i get|i'?d like)\b"
    r"[^.?!]{0,30}?\b(short|long)\s+rest\b",
    re.IGNORECASE,
)


_CURLY = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'"})


def normalise(line: str) -> str:
    """Whitespace squeezed and the curly apostrophes a phone keyboard types (U+2019, U+2018,
    U+02BC) made plain, so a curly "combat's over" matches the plain one (#1173)."""
    return " ".join(line.translate(_CURLY).split())


def _clean(line: str) -> str | None:
    text = normalise(line)
    if not text or "?" in text or len(text.split()) > MAX_WORDS:
        return None
    return text


def find(line: str) -> Rest | None:
    """ "short" or "long" if the line plainly says the party takes that rest."""
    text = _clean(line)
    if text is None or _NOT_A_REST.search(text):
        return None
    found = _SAYS.search(text)
    if found is None:
        return None
    return "short" if found.group(1).lower() == "short" else "long"


def _finished(text: str) -> Rest | None:
    """A rest plainly finishing ("you finish your long rest", "after your short rest")."""
    if _NOT_THE_TIME.search(text) or _LATER_THAN_NOW.search(text):
        return None
    found = _FINISHES.search(text) or _AFTER.match(text)
    if found is None:
        return None
    kind = next(g for g in found.groups() if g)
    return "short" if kind.lower() == "short" else "long"


def _number(word: str) -> int | None:
    word = word.lower().replace("-", " ")
    if word.isdigit():
        return int(word)
    if word in _ONES:
        return _ONES[word]
    if word in _TENS:
        return _TENS[word]
    parts = word.split()
    if len(parts) == 2 and parts[0] in _TENS and parts[1] in _ONES and _ONES[parts[1]] < 10:
        return _TENS[parts[0]] + _ONES[parts[1]]
    return None


def _amount(text: str) -> int | None:
    """Game minutes for "an hour later", "two hours later", "ten minutes later", "half an hour
    later"; None when it isn't one or is out of range (0, or more than 24 hours)."""
    found = _AMOUNT.match(text)
    # Only the words up to "later" can make it not a time: what follows is the scene ("An hour
    # later, you can see the tower").
    if found is None or _NOT_THE_TIME.search(text[: found.end()]):
        return None
    word, unit = found.group("n").lower(), found.group("unit").lower()
    hours = unit.startswith("hour")
    if word in ("a", "an") and unit.endswith("s"):
        return None  # "a hours later" is not English
    if word == "half an":
        if not hours:
            return None
        minutes = 30
    elif word in ("a", "an"):
        minutes = 60 if hours else 1
    else:
        count = _number(word)
        if count is None:
            return None
        minutes = count * 60 if hours else count
    return minutes if 1 <= minutes <= MAX_AMOUNT_MIN else None


def _skip(text: str) -> Said | None:
    """A skip of whole days the DM plainly said ("Three days later, you reach the city", "A week
    later, the party...", "The next day, you..."), 1 to 30 days, counted as rested unless the
    DM said "without rest" in the same line. Never inside quoted speech, a question, a wish, a
    condition or a maybe, nor "in three days", "for three days" or "three days ago"."""
    found = _DAYS.match(text)
    if found is None or _QUOTES.search(text):
        return None
    # The whole line, as for dawn ("Three days later, you can't go on" is not the clock moving), but
    # with a plain "no rest" taken out first: its "no" is the DM's own "without rest".
    if _NOT_THE_TIME.search(_WITHOUT_REST.sub("", text)):
        return None
    if found.group("w"):
        days = 7 * _WEEKS[found.group("w").lower()]
    elif found.group("a"):
        days = 1  # "a day later", "one day later"
    elif found.group("n"):
        word = found.group("n").lower()
        days = _number(word) or 0
    else:
        days = 1  # "the next day"
    if not 1 <= days <= MAX_SKIP_DAYS:
        return None
    rested = _WITHOUT_REST.search(text[found.end() :]) is None
    return Said("days" if rested else "days_norest", days * MINUTES_PER_DAY)


def find_clock(line: str) -> Said | None:
    """The clock change a DM's line plainly says, or None. A rest starting or finishing, the
    next morning or dawn, or an amount of time later, in that order."""
    text = _clean(line)
    if text is None:
        return None
    rest = _finished(text)
    if rest is not None:
        return Said(rest, ends=True)
    rest = find(text)
    if rest is not None:
        return Said(rest)
    if not _NOT_THE_TIME.search(text) and _DAWN.match(text):
        return Said("dawn")
    skip = _skip(text)
    if skip is not None:
        return skip
    minutes = _amount(text)
    return None if minutes is None else Said("advance", minutes)


def suggested_rest(line: str) -> Rest | None:
    """ "short" or "long" if a player is suggesting a rest ("let's take a short rest", "can we
    long rest?"). Only ever a question for the DM: it moves nothing. A line that is about
    something else (a past rest, a quote) is not one."""
    text = normalise(line)
    if not text or len(text.split()) > MAX_WORDS:
        return None
    if re.search(
        r"\b(?:yesterday|ago|last|took|had|said|says|told|tells|not|never|don'?t|no|later|tomorrow)\b",
        text,
        re.I,
    ):
        return None
    found = _SUGGESTS.search(text)
    if found is None:
        return None
    return "short" if found.group(1).lower() == "short" else "long"
