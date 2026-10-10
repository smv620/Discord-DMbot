"""How long a spell or effect lasts, in game minutes (#998): pure, no Discord, no database.

The rules index gives a spell's duration as text ("1 minute", "Concentration, up to 1 hour",
"Until dispelled", "Instantaneous"). The DM can also type one ("10 minutes", "8 hr"). A round
is 6 seconds, rounded up to the clock's whole minute (so 1 to 10 rounds is a minute). Anything
that is not a length of time ("Instantaneous", "Until dispelled", "Special") is not timed: no
timer is started for it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
}  # fmt: skip
_UNIT_MINUTES = {"minute": 1, "min": 1, "hour": 60, "hr": 60, "day": 1440}
_LENGTH = re.compile(
    r"(?:up to\s+)?(\d{1,4}|[a-z]+)\s*(round|minute|min|hour|hr|day)s?\b", re.IGNORECASE
)
MAX_MINUTES = 60 * 24 * 365  # a year: more than any timer a table would run


@dataclass(frozen=True, slots=True)
class Duration:
    minutes: int | None  # None: not a length of time
    concentration: bool = False

    @property
    def timed(self) -> bool:
        return self.minutes is not None


def parse(text: str | None) -> Duration:
    """The duration in `text`, or an untimed one ("Instantaneous", "Until dispelled",
    "Special", or anything it cannot read)."""
    if not text:
        return Duration(None)
    lowered = " ".join(text.split()).lower()
    concentration = "concentration" in lowered
    match = _LENGTH.search(lowered)
    if match is None:
        return Duration(None, concentration)
    number = match.group(1)
    count = int(number) if number.isdigit() else _NUMBERS.get(number)
    if count is None or count < 1:
        return Duration(None, concentration)
    unit = match.group(2)
    minutes = math.ceil(count * 6 / 60) if unit == "round" else count * _UNIT_MINUTES[unit]
    if minutes > MAX_MINUTES:
        return Duration(None, concentration)
    return Duration(minutes, concentration)


def words(minutes: int) -> str:
    """ "10 minutes", "1 hour", "8 hours", "2 days": for the DM screen."""
    for size, name in ((1440, "day"), (60, "hour"), (1, "minute")):
        if minutes % size == 0 or size == 1:
            count = minutes // size if size > 1 else minutes
            return f"{count} {name}{'' if count == 1 else 's'}"
    raise AssertionError("size 1 always returns")  # pragma: no cover
