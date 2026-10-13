"""What is said at the table that changes a character, and whether a re-read of their D&D Beyond
sheet shows it (#1128; docs/PLAN.md, "Re-reading D&D Beyond"). Pure: no Discord, no network.

A small, strict phrase list, as for the game clock: "takes 8 damage", "heals 5", "is poisoned",
"uses a level-2 slot". A line that sounds like a question, a wish, a condition or a maybe is
never one. Which character it is about is not decided here (the caller works that out from the
names heard, and doesn't guess when it is unclear).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from dmbot.memory import sheets

Kind = Literal["damage", "heal", "condition", "slot"]

MAX_WORDS = 16  # a long ramble that mentions damage isn't a ruling
MAX_AMOUNT = 999
_NOT_A_THING = re.compile(
    r"\b(?:if|when|whenever|once|unless|before|until|can|can't|cannot|could|couldn't|should|"
    r"shouldn't|would|wouldn't|will|won't|don't|do not|not|never|no|maybe|might|may|want|wants|"
    r"wanna|need|needs|let's|lets|why|how|what|whether|try|tried|trying|going|gonna|said|says|"
    r"told|tells|yesterday|ago|last|doesn't|didn't|isn't|aren't|wasn't)\b",
    re.IGNORECASE,
)
_ONES = {
    w: n
    for n, w in enumerate(
        [
            "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
            "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
            "eighteen", "nineteen",
        ],
        start=1,
    )
}  # fmt: skip
_TENS = {
    w: 10 * n
    for n, w in enumerate(
        ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"], start=2
    )
}
_NUMBER = (
    r"\d{1,3}|(?:" + "|".join(_TENS) + r")(?:[- ](?:" + "|".join(_ONES) + r"))?|" + "|".join(_ONES)
)
# "takes 8 damage", "takes 8 points of fire damage", "is hit for 8", "loses 8 hit points"
_DAMAGE = re.compile(
    rf"\b(?:takes?|taking|took|suffers?|is hit for|are hit for|loses|lose)\s+(?P<n>{_NUMBER})\s+"
    r"(?:points? of\s+)?(?:[a-z]+\s+)?(?:damage|hit points?|hp)\b",
    re.IGNORECASE,
)
_HIT = re.compile(rf"\b(?:is|are) hit for\s+(?P<n>{_NUMBER})\b", re.IGNORECASE)
_DAMAGE_TO = re.compile(rf"\b(?P<n>{_NUMBER})\s+(?:[a-z]+\s+)?damage\s+to\b", re.IGNORECASE)
# "heals 5", "regains 5 hit points", "recovers 5 hp", "gets 5 hit points back"
_HEAL = re.compile(
    rf"\b(?:heals?|healed|healing)\s+(?:for\s+)?(?P<n>{_NUMBER})\b"
    rf"|\b(?:regains?|recovers?|gains?|gets?)\s+(?P<m>{_NUMBER})\s+(?:hit points?|hp)\b",
    re.IGNORECASE,
)
_CONDITION_WORDS = "|".join(sorted((c.lower() for c in sheets.CONDITIONS), key=len, reverse=True))
_CONDITION = re.compile(
    rf"\b(?:is|are|'s|'re|becomes?|gets?|got|now)\s+(?:now\s+)?(?P<c>{_CONDITION_WORDS})\b",
    re.IGNORECASE,
)
_SLOT = re.compile(
    r"\b(?:uses?|spends?|burns?|used|spent)\s+(?:a|an|one|their|her|his|your|my)?\s*"
    r"(?:level[- ]?\d|\d(?:st|nd|rd|th)[- ]level|(?:first|second|third|fourth|fifth|sixth|"
    r"seventh|eighth|ninth)[- ]level|spell)\s+(?:spell\s+)?slot\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Heard:
    """One change the table plainly said. `amount` for damage and healing, `condition` (the
    sheet's name for it) for a condition."""

    kind: Kind
    amount: int | None = None
    condition: str | None = None
    # Where the words were in the line, to tell whose change it is (`about`); not part of what
    # was said, so two of the same change compare equal.
    at: int = field(default=0, compare=False, repr=False)
    end: int = field(default=0, compare=False, repr=False)


def _number(word: str) -> int | None:
    word = word.lower().replace("-", " ")
    if word.isdigit():
        return int(word)
    parts = word.split()
    total = 0
    for part in parts:
        if part in _TENS:
            total += _TENS[part]
        elif part in _ONES:
            total += _ONES[part]
        else:
            return None
    return total or None


def find(line: str) -> list[Heard]:
    """The changes a line plainly says (usually none or one). A question, a wish, a condition
    or a maybe says nothing."""
    text = " ".join(line.split())
    if not text or "?" in text or len(text.split()) > MAX_WORDS or _NOT_A_THING.search(text):
        return []
    found: list[Heard] = []
    for pattern in (_DAMAGE, _HIT, _DAMAGE_TO):
        m = pattern.search(text)
        if m is not None:
            n = _number(m.group("n"))
            if n is not None and 1 <= n <= MAX_AMOUNT:
                found.append(Heard("damage", n, at=m.start(), end=m.end()))
                break
    m = _HEAL.search(text)
    if m is not None:
        n = _number(m.group("n") or m.group("m"))
        if n is not None and 1 <= n <= MAX_AMOUNT:
            found.append(Heard("heal", n, at=m.start(), end=m.end()))
    m = _CONDITION.search(text)
    if m is not None:
        name = next((c for c in sheets.CONDITIONS if c.lower() == m.group("c").lower()), None)
        if name is not None:
            found.append(Heard("condition", condition=name, at=m.start(), end=m.end()))
    m = _SLOT.search(text)
    if m is not None:
        found.append(Heard("slot", at=m.start(), end=m.end()))
    return found


def about(line: str, heard: Heard, name: str) -> bool:
    """Is this change plainly about the character called `name`? Their name stands just before
    the words ("Belleros takes 8 damage"), or follows "to" or "on" ("8 damage to Belleros").
    A name elsewhere in the line ("Belleros hits the goblin, it takes 8 damage") is not enough:
    DMbot doesn't guess."""
    text = " ".join(line.split())
    names = {name.strip()} | {name.split()[0]} if len(name.split()) > 1 else {name.strip()}
    for each in sorted((n for n in names if len(n) > 2), key=len, reverse=True):
        pattern = re.escape(each)
        before = text[: heard.at]
        if re.search(rf"\b{pattern}\W*$", before, re.IGNORECASE) or re.search(
            rf"\b{pattern}\b[^.!,;]{{0,12}}$", before, re.IGNORECASE
        ):
            return True
        after = text[heard.end :]
        if re.match(rf"\s+(?:to|on)\s+{pattern}\b", after, re.IGNORECASE) or (
            re.search(r"\b(?:to|on)$", text[: heard.end], re.IGNORECASE)
            and re.match(rf"\s+{pattern}\b", after, re.IGNORECASE)
        ):
            return True
    return False


def _effective_hp(snapshot: Mapping[str, Any]) -> int | None:
    """Hit points and temporary hit points together; full health when damage isn't recorded."""
    hp, top = snapshot.get("hp"), snapshot.get("max_hp")
    base = hp if isinstance(hp, int) else top if isinstance(top, int) else None
    if base is None:
        return None
    temp = snapshot.get("temp_hp")
    return base + (temp if isinstance(temp, int) else 0)


def _hp_text(snapshot: Mapping[str, Any]) -> str | None:
    hp = snapshot.get("hp")
    top = snapshot.get("max_hp")
    now = hp if isinstance(hp, int) else top if isinstance(top, int) else None
    return None if now is None else f"{now} HP"


def mismatch(
    heard: Heard, before: Mapping[str, Any] | None, after: Mapping[str, Any], name: str
) -> str | None:
    """The question for the player when the sheet read now doesn't show what was said, or None
    when it matches (or can't be checked: a slot count isn't kept, and a sheet read for the
    first time has nothing to compare). `before`: the snapshot kept before this read. DMbot
    only asks; it never changes the sheet."""
    if before is None:
        return None
    if heard.kind == "damage" and heard.amount is not None:
        was, now = _effective_hp(before), _effective_hp(after)
        if was is None or now is None or now < was:
            return None
        at = _hp_text(after)
        return (
            f"At the table it sounded like {name} took {heard.amount} damage. "
            f"Your D&D Beyond sheet still shows {at}. Want to update it?"
            if at
            else f"At the table it sounded like {name} took {heard.amount} damage. "
            "Your D&D Beyond sheet doesn't show it yet. Want to update it?"
        )
    if heard.kind == "heal" and heard.amount is not None:
        was, now = _effective_hp(before), _effective_hp(after)
        top = after.get("max_hp")
        hp = after.get("hp")
        full = isinstance(top, int) and (not isinstance(hp, int) or hp >= top)
        if was is None or now is None or now > was or full:
            return None  # it went up, or there was nothing to heal
        at = _hp_text(after)
        return (
            f"At the table it sounded like {name} healed {heard.amount}. "
            f"Your D&D Beyond sheet still shows {at}. Want to update it?"
            if at
            else f"At the table it sounded like {name} healed {heard.amount}. "
            "Your D&D Beyond sheet doesn't show it yet. Want to update it?"
        )
    if heard.kind == "condition" and heard.condition is not None:
        have = after.get("conditions")
        if isinstance(have, list) and heard.condition in have:
            return None
        return (
            f"At the table it sounded like {name} is {heard.condition.lower()}. "
            "Your D&D Beyond sheet doesn't show it. Want to update it?"
        )
    return None
