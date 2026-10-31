"""The campaign's calendar (#3091 part 3): pure, no Discord and no database.

Game time is one number (`clock.py`: minutes since the start of Day 1). A calendar only gives that
number words: "Day 12", or a Harptos date such as "Flamerule 14th, 1492 DR". Nothing here is a
real-world date or `TIMESTAMP`; a campaign's Day 1 is stored as words and numbers (`DayOne`) and
everything after it is counted in whole days, so a flashback or time travel stays on the one
timeline (docs/STORY_MEMORY.md 9a, "One timeline").

Harptos has 12 months of 30 days and 5 festival days that belong to no month, 365 days a year.
Shieldmeet is the extra festival day of a leap year, the day after Midsummer; a Dale Reckoning
year divisible by 4 is a leap year (366 days).
"""

from __future__ import annotations

from dataclasses import dataclass

from dmbot.timebot.clock import MINUTES_PER_DAY, day_of

DAYS = "days"  # "Day 12": the default, no setting needed
HARPTOS = "harptos"
KINDS = (DAYS, HARPTOS)

# The year in order: a month is (name, 30 days); a festival day follows the month it is listed
# after. Hammer 1 is the first day of the year.
_YEAR: tuple[tuple[str, int], ...] = (
    ("Hammer", 30), ("Midwinter", 0),
    ("Alturiak", 30), ("Ches", 30), ("Tarsakh", 30), ("Greengrass", 0),
    ("Mirtul", 30), ("Kythorn", 30), ("Flamerule", 30), ("Midsummer", 0),
    ("Eleasis", 30), ("Eleint", 30), ("Highharvestide", 0),
    ("Marpenoth", 30), ("Uktar", 30), ("Feast of the Moon", 0),
    ("Nightal", 30),
)  # fmt: skip
YEAR_DAYS = sum(30 if days else 1 for _, days in _YEAR)  # 365 (366 in a leap year)
SHIELDMEET = "Shieldmeet"
MAX_YEAR = 9999  # keeps the number small enough to store and show
# The names a DM can start Day 1 on: a month with a day, or a festival day alone.
MONTHS = tuple(name for name, days in _YEAR if days)
FESTIVALS = tuple(name for name, days in _YEAR if not days)


def is_leap(year: int) -> bool:
    return year % 4 == 0


def year_days(year: int) -> int:
    return YEAR_DAYS + (1 if is_leap(year) else 0)


def _layout(year: int) -> list[tuple[str, int]]:
    """The year in order; a leap year has Shieldmeet right after Midsummer."""
    out: list[tuple[str, int]] = []
    for label, days in _YEAR:
        out.append((label, days))
        if label == "Midsummer" and is_leap(year):
            out.append((SHIELDMEET, 0))
    return out


@dataclass(frozen=True, slots=True)
class DayOne:
    """What date Day 1 of the campaign is: a Harptos year and the day of that year (0 is Hammer 1,
    364 is Nightal 30, or 365 in a leap year). Words and numbers only."""

    year: int
    ordinal: int

    def __post_init__(self) -> None:
        # exactly an int: a bool is one in Python and a float would be silently rounded, and
        # neither is a year or a day
        if type(self.year) is not int or type(self.ordinal) is not int:
            raise ValueError("day one")
        if not 1 <= self.year <= MAX_YEAR or not 0 <= self.ordinal < year_days(self.year):
            raise ValueError("day one")


def day_one(year: int, name: str, day: int | None = None) -> DayOne:
    """Day 1 on a month and day ("Flamerule", 14; the 1st when no day is given) or on a festival
    day ("Midsummer", no day). Raises ValueError for a name the calendar has not got (Shieldmeet
    in a year without one), a day the month has not got, a day that is not exactly an int, or a
    day given with a festival (which has none)."""
    if type(year) is not int or (day is not None and type(day) is not int):
        raise ValueError("day one")
    at = 0
    for label, days in _layout(year):
        if label.casefold() == name.strip().casefold():
            if not days:
                if day is not None:
                    raise ValueError("day")
                return DayOne(year, at)
            chosen = 1 if day is None else day
            if not 1 <= chosen <= days:
                raise ValueError("day")
            return DayOne(year, at + chosen - 1)
        at += days or 1
    raise ValueError("month")


def _ordinal_suffix(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def harptos(day_number: int, start: DayOne) -> str:
    """ "Flamerule 14th, 1492 DR" for game day `day_number` (Day 1 is `start`), or the festival's
    name with the year ("Midsummer, 1492 DR"). Days before Day 1 do not exist."""
    if day_number < 1:
        raise ValueError("day")
    count = start.ordinal + (day_number - 1)
    year = start.year
    while count >= year_days(year):  # (at most a few hundred years: the clock stops at day 99,999)
        count -= year_days(year)
        year += 1
    for label, days in _layout(year):
        span = days or 1
        if count < span:
            if days:
                return f"{label} {count + 1}{_ordinal_suffix(count + 1)}, {year} DR"
            return f"{label}, {year} DR"
        count -= span
    raise AssertionError("unreachable")  # (count is under the year's length, so a span holds it)


def date_words(minute: int, kind: str = DAYS, start: DayOne | None = None) -> str:
    """The date of this game minute in the campaign's calendar. Plain "Day N" when the campaign
    has no Harptos setting (or no Day 1 date), so a missing setting never hides the time. A
    negative minute (or one that is not exactly an int) is no time at all: ValueError in both
    modes, never "Day 0"."""
    if type(minute) is not int or minute < 0:
        raise ValueError("minute")
    number = day_of(minute)
    if kind == HARPTOS and start is not None:
        return harptos(number, start)
    return f"Day {number}"


def minute_of(day_number: int, hour: int = 0, minute: int = 0) -> int:
    """The game minute at the start of day `day_number` (the clock's own arithmetic)."""
    return (day_number - 1) * MINUTES_PER_DAY + hour * 60 + minute


@dataclass(frozen=True, slots=True)
class Calendar:
    """A campaign's calendar setting: which one, and (for Harptos) the date of Day 1. The default,
    Day N, needs neither."""

    kind: str = DAYS
    start: DayOne | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS or (self.kind == HARPTOS and self.start is None):
            raise ValueError("calendar")

    def words(self, minute: int) -> str:
        """The date of this game minute: Day N, or the Harptos date."""
        return date_words(minute, self.kind, self.start)
