"""A route's stated length (#4255, MapBot layers slice 1; Supervisor's ruling on #4255, 2026-11-14):
the length as the source gave it, in its own words ("two days by cart"), with an amount and a unit
when the words give one. Never measured from a picture (design basis §4, "Routes are not a layer").

The words are always kept, and they are what the card shows. The amount and unit are what a later
answer adds up, so they are only ever taken from words that say them plainly:
- units: hours, days or miles only. A league is 3 miles (SRD) and a week 7 days, so "two leagues"
  is 6 miles and "a week's ride" 7 days, the source's words kept;
- the amount: digits ("20 miles"), the number words one to twenty, "a", "an" or "one" ("a day's
  ride"), and "half a day";
- "about", "roughly", "nearly", "some" or "around" sets `approx`, so every answer built from it
  says "about" too; the amount is kept;
- a range ("two or three days", "2-3 days") keeps its lower end, so nothing is overstated, with
  `approx` set;
- anything vaguer ("a few days", "several miles") keeps its words only: no amount, no unit.

`find` turns a route's own quote (the sentence the reader already chose for the route, or the DM's
words on a journey) into the length's words by set phrases only ("two days' ride from", "a day by
cart to", "20 miles from", "five hours to Easthaven" when Easthaven is that route's own end): never
the AI's own sum. The lore keep and the session's journeys use it alike (#4255 slice 1b). Pure: no
Discord, no database."""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from typing import NamedTuple

HOURS, DAYS, MILES = "hours", "days", "miles"
UNITS = (HOURS, DAYS, MILES)
WORDS_MAX = 60  # the column's limit (as a route's means words)
AMOUNT_MAX = 10_000  # a sanity cap: no route in a story is longer

_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}  # fmt: skip
# Each unit word and what one of it is in a stored unit
_UNIT_WORDS: dict[str, tuple[str, float]] = {
    "hour": (HOURS, 1), "hours": (HOURS, 1),
    "day": (DAYS, 1), "days": (DAYS, 1),
    "week": (DAYS, 7), "weeks": (DAYS, 7),
    "mile": (MILES, 1), "miles": (MILES, 1),
    "league": (MILES, 3), "leagues": (MILES, 3),
}  # fmt: skip
_APPROX = ("about", "roughly", "nearly", "some", "around", "almost", "approximately")
_NUMBER = r"(?:\d+(?:\.\d+)?|" + "|".join(sorted(_NUMBERS, key=len, reverse=True)) + r")"
_UNIT = r"(?:" + "|".join(sorted(_UNIT_WORDS, key=len, reverse=True)) + r")"
_RANGE = re.compile(
    rf"^(?P<low>{_NUMBER})\s*(?:or|to|-|–)\s*(?P<high>{_NUMBER})\s+(?P<unit>{_UNIT})\b", re.I
)
_ONE = re.compile(rf"^(?P<n>{_NUMBER})\s+(?P<unit>{_UNIT})\b", re.I)
_HALF = re.compile(rf"^half\s+an?\s+(?P<unit>{_UNIT})\b", re.I)


class Length(NamedTuple):
    words: str  # as the source gave it, always kept and shown
    amount: float | None = None  # in `unit`; None when the words give none
    unit: str | None = None  # hours, days or miles; None with no amount
    approx: bool = False  # "about", or a range: every answer says "about" too


def _number(text: str) -> float | None:
    word = text.casefold()
    if word in _NUMBERS:
        return float(_NUMBERS[word])
    try:
        return float(word)
    except ValueError:
        return None


def parse(words: str) -> Length | None:
    """The length these words give, or None for empty words. Words with no plain amount are kept
    with no amount ("a few days")."""
    clean = " ".join(words.split())[:WORDS_MAX].strip()
    if not clean:
        return None
    text = clean.casefold()
    approx = False
    for word in _APPROX:
        if text.startswith(word + " "):
            approx, text = True, text[len(word) + 1 :]
            break
    if m := _RANGE.match(text):
        low, high = _number(m["low"]), _number(m["high"])
        unit, per = _UNIT_WORDS[m["unit"].casefold()]
        if low is not None and high is not None and 0 < low <= high:
            return _sized(clean, low * per, unit, approx=True)
    if m := _HALF.match(text):
        unit, per = _UNIT_WORDS[m["unit"].casefold()]
        return _sized(clean, 0.5 * per, unit, approx=approx)
    if m := _ONE.match(text):
        amount = _number(m["n"])
        unit, per = _UNIT_WORDS[m["unit"].casefold()]
        if amount is not None and amount > 0:
            return _sized(clean, amount * per, unit, approx=approx)
    return Length(clean)


def _sized(words: str, amount: float, unit: str, *, approx: bool) -> Length:
    amount = round(amount, 2)
    if not 0 < amount <= AMOUNT_MAX:
        # (no story route is that long, nor rounds to nothing: a misread, kept as words only)
        return Length(words)
    return Length(words, amount, unit, approx)


# A length said of a route, as set phrases only: "two days' ride from", "a day by cart to",
# "20 miles from", "about three days' march", "a week's sail to". The words found are the length's
# own (up to the unit and what follows it in the phrase), never the places' names.
_LEAD = r"(?:(?:about|roughly|nearly|some|around|almost|approximately)\s+)?"
_AMOUNT = rf"{_LEAD}(?:half\s+an?\s+{_UNIT}|{_NUMBER}(?:\s*(?:or|to|-|–)\s*{_NUMBER})?\s+{_UNIT})"
_HOW = (
    r"(?:['’]s?\s+(?:ride|walk|march|sail|journey|travel|trek|flight)"
    r"|\s+(?:by|on)\s+"
    r"(?:cart|wagon|horse|horseback|foot|boat|ship|sled|dogsled|road|river|sea|air)"
    # ("on the road" is travel; "by the river" is mostly beside it: a camp, not a way)
    r"|\s+on\s+the\s+(?:road|river|sea)"
    r"|\s+of\s+(?:road|river|travel|walking|riding|sailing))"
)
# With how it is travelled, the length may lead to the place ("a day by cart to"); a bare amount
# only from or away from it ("twenty miles from"): "three days to reach" is a deadline, not a way.
_PHRASE = re.compile(
    rf"\b(?P<words>{_AMOUNT}{_HOW})(?=\s+(?:from|to|away|along|up|down|across|between|on)\b|[,.;:]|\s*$)"
    rf"|\b(?P<bare>{_AMOUNT})(?=\s+(?:from|away|between)\b)",
    re.I,
)


_NAME_END = r"(?=[\s,.;:!?)]|$)"  # (not \b: "Bryn Shander's festival" is no way to Bryn Shander)


def _ends(places: Collection[str]) -> str:
    names = sorted({" ".join(p.split()) for p in places if p.strip()}, key=len, reverse=True)
    return "|".join(re.escape(n) for n in names)


def _lengths(text: str, ends: str) -> list[tuple[str, bool]]:
    """Every length phrase in `text`, each with whether it is tied to an end by name ("five hours
    to Easthaven"). The name-tied form adds only what the set phrases don't already cover."""
    found = [(m.span(), m["words"] or m["bare"]) for m in _PHRASE.finditer(text)]
    out = [(words, False) for _, words in found]
    if ends:
        to_end = re.compile(rf"\b(?P<to>{_AMOUNT})\s+to\s+(?:{ends}){_NAME_END}", re.I)
        for m in to_end.finditer(text):
            if not any(m.start() < e and s < m.end() for (s, e), _ in found):  # (no overlap)
                out.append((m["to"], True))
    return [(words.strip(), tied) for words, tied in out]


def find(quote: str, places: Collection[str] = ()) -> str:
    """The length's words a route's quote states by a set phrase, or "" when it states none, or
    more than one: a sentence with two lengths may be about two routes, and which is which is a
    guess this never makes. `places`: the route's own ends by name. A bare amount leading to one
    of them ("five hours to Easthaven") is a length too: "to" and a name the route ends at, never
    "to reach" (a deadline) or a place this route doesn't go to."""
    found = _lengths(" ".join(quote.split()), _ends(places))
    return found[0][0] if len(found) == 1 else ""


def find_on_way(lines: Sequence[str], places: Collection[str]) -> str:
    """A journey's length from the DM's lines on the way (#4255 slice 1b): unlike a route's own
    quote, a stretch of narration can speak of other places ("Kelvin's Cairn is twenty miles from
    here"), so a line's set phrase counts only when that line names one of the journey's ends,
    and the name-tied form ("1 hour to Bryn Shander") anywhere. Exactly one across the stretch,
    as `find` asks of one quote; each line read on its own, so one line's end is never another's."""
    ends = _ends(places)
    if not ends:
        return ""
    names_end = re.compile(rf"\b(?:{ends}){_NAME_END}", re.I)
    got: list[str] = []
    for line in lines:
        text = " ".join(line.split())
        named = names_end.search(text) is not None
        got += [words for words, tied in _lengths(text, ends) if tied or named]
    return got[0] if len(got) == 1 else ""


__all__ = [
    "DAYS", "HOURS", "MILES", "UNITS", "WORDS_MAX", "Length", "find", "find_on_way", "parse",
]  # fmt: skip
