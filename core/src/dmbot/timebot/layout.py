"""A calendar's layout (#5364 slice 1): the one shape every calendar has, as pure code.

Game time stays one number (`clock.py`: minutes since the start of Day 1). A layout only gives the
day number words, and reads words back into a day number. No calendar date is ever stored on a
fact, a limit or a recap, so a calendar can be set up or changed later and nothing is re-dated
(docs: the Expert's design note on #5364). No Discord and no database here.

A layout is the year in order, as parts: a month, a season or a festival (days that belong to no
month, one day or several), each with its number of days; optionally one extra day every N years
(`Leap`); optionally weekday names, which run on through every day, festivals included; and a year
label ("DR", "AR"). Days (plain) needs none of this, and Harptos is one of these (`HARPTOS`).

Nothing here is shown to anyone yet: callers (`calendar.py`, `memory/time_limits.py`) still use
their own Harptos code until the callers' slice moves them onto this. The names of months,
seasons, festivals and weekdays are `Layout.names`, for the name finders' leave-out list.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass

MONTH, SEASON, FESTIVAL = "month", "season", "festival"
PART_KINDS = (MONTH, SEASON, FESTIVAL)

# Limits, so a mistake stays small.
MAX_PARTS = 40
MAX_YEAR_DAYS = 1000
MAX_NAME = 30
MAX_WEEKDAYS = 20
MAX_LABEL = 8
MAX_YEAR = 9999  # keeps the number small enough to store and show
MAX_DAY_NUMBER = 99_999  # the clock stops here (`clock.py`)
# Words the date reader uses itself: a part named so would be read as grammar, not as a name.
_RESERVED = frozenset({"day", "days", "the", "of"})
_ONLY_A_NUMBER = re.compile(r"\d+(?:st|nd|rd|th)?", re.I)

# A date with no year needs a direction, given by the caller: a time limit means its next coming,
# a fact's "when it happened" means its last coming at or before the moment it was learned.
NEXT, LAST = "next", "last"


@dataclass(frozen=True, slots=True)
class Part:
    """One stretch of the year: a month, a season or a festival, with its days."""

    name: str
    days: int
    kind: str = MONTH


@dataclass(frozen=True, slots=True)
class Leap:
    """One extra day (a one-day festival) every `every` years, right after the part `after`."""

    every: int
    name: str
    after: str


@dataclass(frozen=True, slots=True)
class Start:
    """What date Day 1 of the campaign is: a year, the day of that year (0 is its first day) and
    the weekday (an index into the layout's weekday names, 0 without weekdays). Numbers only."""

    year: int
    ordinal: int
    weekday: int = 0


@dataclass(frozen=True, slots=True)
class Date:
    """A game day in the calendar: the year, the part it falls in, and the day of that part."""

    year: int
    part: str
    kind: str
    day: int  # the day within the part (1 for a one-day festival)
    of: int  # how many days the part has this year
    ordinal: int  # the day of the year, 0 for the first


def _is_int(*values: object) -> bool:
    """Exactly an int: a bool is one in Python and a float would be silently rounded."""
    return all(type(v) is int for v in values)


def _name_ok(name: object) -> bool:
    return (
        isinstance(name, str)
        and name == name.strip()
        and 1 <= len(name) <= MAX_NAME
        and name.isprintable()
        and name.casefold() not in _RESERVED
        and not _ONLY_A_NUMBER.fullmatch(name)
    )


@dataclass(frozen=True, slots=True)
class Layout:
    """A calendar's definition, checked when it is made: a bad one raises ValueError with a short
    code (`parts`, `name`, `days`, `leap`, `weekdays`, `label`) the settings can turn into words."""

    name: str
    parts: tuple[Part, ...]
    leap: Leap | None = None
    weekdays: tuple[str, ...] = ()
    year_label: str = ""

    def __post_init__(self) -> None:
        if not _name_ok(self.name):
            raise ValueError("name")
        if not 1 <= len(self.parts) <= MAX_PARTS:
            raise ValueError("parts")
        seen: set[str] = set()

        def fresh(name: object) -> None:
            """A name is plain, and no two names are alike (a weekday and a month included)."""
            if not _name_ok(name):
                raise ValueError("name")
            assert isinstance(name, str)
            if name.casefold() in seen:
                raise ValueError("name")
            seen.add(name.casefold())

        for part in self.parts:
            if not isinstance(part, Part) or part.kind not in PART_KINDS:
                raise ValueError("parts")
            if not _is_int(part.days) or part.days < 1:
                raise ValueError("days")
            fresh(part.name)
        # months or seasons for now, never both in one calendar (festivals go with either)
        if len({p.kind for p in self.parts} - {FESTIVAL}) > 1:
            raise ValueError("parts")
        if self.leap is not None:
            leap = self.leap
            if not _is_int(leap.every) or not 1 <= leap.every <= MAX_YEAR:
                raise ValueError("leap")
            fresh(leap.name)
            if leap.after.casefold() not in {p.name.casefold() for p in self.parts}:
                raise ValueError("leap")
        if sum(p.days for p in self.parts) + (1 if self.leap else 0) > MAX_YEAR_DAYS:
            raise ValueError("days")
        if self.weekdays and not 2 <= len(self.weekdays) <= MAX_WEEKDAYS:
            raise ValueError("weekdays")
        for day_name in self.weekdays:
            fresh(day_name)
        if len(self.year_label) > MAX_LABEL or (self.year_label and not self.year_label.isalpha()):
            raise ValueError("label")

    # -- the year -------------------------------------------------------------------------

    def is_leap(self, year: int) -> bool:
        return self.leap is not None and year % self.leap.every == 0

    def sections(self, year: int) -> list[Part]:
        """The year in order; a leap year has its extra day right after the part it follows."""
        out: list[Part] = []
        for part in self.parts:
            out.append(part)
            if (
                self.leap is not None
                and self.is_leap(year)
                and part.name.casefold() == self.leap.after.casefold()
            ):
                out.append(Part(self.leap.name, 1, FESTIVAL))
        return out

    def year_days(self, year: int) -> int:
        return sum(p.days for p in self.parts) + (1 if self.is_leap(year) else 0)

    @property
    def names(self) -> tuple[str, ...]:
        """Every month, season, festival and weekday name, for the name finders' leave-out list:
        none of them is ever offered as a new name."""
        leap = (self.leap.name,) if self.leap else ()
        return (*(p.name for p in self.parts), *leap, *self.weekdays)

    # -- Day 1 ----------------------------------------------------------------------------

    def ordinal_of(self, year: int, name: str, day: int | None = None) -> int:
        """The day of the year (0 is the first) of a part and a day in it: a month or season on a
        day (its 1st when none is given), a one-day festival alone, a several-day festival on a
        day (its first when none is given). Raises ValueError for a part the year has not got (the
        leap day in a year without one), a day the part has not got, a day that is not exactly an
        int, or a day given with a one-day festival."""
        if not _is_int(year) or (day is not None and not _is_int(day)):
            raise ValueError("date")
        if not 1 <= year <= MAX_YEAR:
            raise ValueError("date")
        at = 0
        wanted = name.strip().casefold()
        for part in self.sections(year):
            if part.name.casefold() == wanted:
                if part.kind == FESTIVAL and part.days == 1:
                    if day is not None:
                        raise ValueError("day")
                    return at
                chosen = 1 if day is None else day
                if not 1 <= chosen <= part.days:
                    raise ValueError("day")
                return at + chosen - 1
            at += part.days
        raise ValueError("part")

    def start_on(
        self, year: int, name: str, day: int | None = None, weekday: str | None = None
    ) -> Start:
        """Day 1 on a date of this calendar, and (with weekday names) the weekday it falls on."""
        ordinal = self.ordinal_of(year, name, day)
        index = 0
        if weekday is not None:
            names = [w.casefold() for w in self.weekdays]
            if weekday.strip().casefold() not in names:
                raise ValueError("weekday")
            index = names.index(weekday.strip().casefold())
        return Start(year, ordinal, index)

    def check_start(self, start: Start) -> None:
        """ValueError unless `start` is a day this calendar has (and a weekday it has)."""
        if not _is_int(start.year, start.ordinal, start.weekday):
            raise ValueError("start")
        if not 1 <= start.year <= MAX_YEAR or not 0 <= start.ordinal < self.year_days(start.year):
            raise ValueError("start")
        if not 0 <= start.weekday < max(len(self.weekdays), 1):
            raise ValueError("start")

    # -- counting -------------------------------------------------------------------------

    def date_of(self, day_number: int, start: Start) -> Date:
        """The date of game day `day_number` (Day 1 is `start`). Days before Day 1 do not exist."""
        if not _is_int(day_number) or not 1 <= day_number <= MAX_DAY_NUMBER:
            raise ValueError("day")
        self.check_start(start)
        count = start.ordinal + (day_number - 1)
        year = start.year
        while count >= self.year_days(year):
            count -= self.year_days(year)
            year += 1
        ordinal = count
        for part in self.sections(year):
            if count < part.days:
                return Date(year, part.name, part.kind, count + 1, part.days, ordinal)
            count -= part.days
        raise AssertionError("unreachable")  # (count is under the year's length)

    def day_number_of(self, year: int, name: str, day: int | None, start: Start) -> int:
        """The game day of a date: the other way round from `date_of`. ValueError for a date the
        calendar has not got (`ordinal_of`'s checks), one before Day 1, or past the clock's end."""
        self.check_start(start)
        target = self.ordinal_of(year, name, day)
        if (year, target) < (start.year, start.ordinal):
            raise ValueError("before day one")
        days = sum(self.year_days(y) for y in range(start.year, year))
        number = days + target - start.ordinal + 1
        if number > MAX_DAY_NUMBER:
            raise ValueError("day")
        return number

    def weekday_of(self, day_number: int, start: Start) -> str | None:
        """The weekday's name, or None for a calendar with no weekdays. Weeks run on through every
        day, festivals included."""
        self.date_of(day_number, start)  # (the same checks)
        if not self.weekdays:
            return None
        return self.weekdays[(start.weekday + day_number - 1) % len(self.weekdays)]

    # -- printing -------------------------------------------------------------------------

    def words(self, day_number: int, start: Start, *, weekday: bool = False) -> str:
        """ "Summer 2nd, 1201 AR", or "Midwinter, 1201 AR" for a one-day festival (a several-day
        one counts its days: "Moonfeast 2nd, 1201 AR"). With `weekday` and weekday names, the
        weekday first ("Fireday, Summer 2nd, 1201 AR"), for where the clock is shown in full."""
        d = self.date_of(day_number, start)
        label = f" {self.year_label}" if self.year_label else ""
        if d.kind == FESTIVAL and d.of == 1:
            text = f"{d.part}, {d.year}{label}"
        else:
            text = f"{d.part} {d.day}{ordinal_suffix(d.day)}, {d.year}{label}"
        named = self.weekday_of(day_number, start) if weekday else None
        return f"{named}, {text}" if named else text

    # -- reading --------------------------------------------------------------------------

    def parse(self, words: str, start: Start, *, today: int, direction: str = NEXT) -> int | None:
        """The game day the words name, or None. Code reads dates, never the AI.

        Read: "the 2nd day of Summer", "the 2nd of Summer", "Summer 2nd", "Summer 2", with a year
        ("Summer 2nd, 1201 AR"), a festival's name alone ("before Midwinter"), and "Day N" in
        every calendar. A bare month or season name is no date (a "Reaping" or an "Autumn" may be
        a person); a one-day festival stands alone, and a several-day one means its first day.
        Strict: two dates in the words are none (DMbot can't tell which), a name the calendar has
        not got is none, and nothing before Day 1.

        A date with no year takes `direction` from game day `today`: NEXT is its next coming
        after today (a time limit's), LAST is its last coming at or before today (when something
        happened, given the day it was learned)."""
        if direction not in (NEXT, LAST):
            raise ValueError("direction")
        pat = _patterns(self)
        found: set[tuple[str, int | None, int | None]] = set()
        taken: list[tuple[int, int]] = []  # (spans already read as a dated day)
        for m in pat.day_first.finditer(words):
            found.add((m.group(2).casefold(), int(m.group(1)), _year(m.group(3))))
            taken.append(m.span())
        for m in pat.name_first.finditer(words):
            found.add((m.group(1).casefold(), int(m.group(2)), _year(m.group(3))))
            taken.append(m.span())
        for m in pat.festival.finditer(words):
            if not any(a <= m.start(1) < b for a, b in taken):
                found.add((m.group(1).casefold(), None, _year(m.group(2))))
        counted = {int(m.group(1)) for m in _DAY_N.finditer(words)}
        if len(found) + len(counted) != 1:
            return None
        if counted:
            number = counted.pop()
            return number if 1 <= number <= MAX_DAY_NUMBER else None
        ((name, day, year),) = found
        try:
            self.check_start(start)
            if year is not None:
                return self.day_number_of(year, name, day, start)
            return self._coming(name, day, start, today, direction)
        except ValueError:
            return None

    def _coming(
        self, name: str, day: int | None, start: Start, today: int, direction: str
    ) -> int | None:
        """The next (after `today`) or last (up to `today`) game day of a date with no year."""
        today = min(max(today, 1), MAX_DAY_NUMBER)
        here = self.date_of(today, start).year
        # a date of a leap year comes once in `every` years; one more year for the edges
        reach = (self.leap.every if self.leap else 1) + 1
        years = range(max(start.year, here - 1), here + reach + 1)
        if direction == LAST:
            years = range(here, max(start.year, here - reach) - 1, -1)
        for y in years:
            try:
                n = self.day_number_of(y, name, day, start)
            except ValueError:
                continue
            if (direction == NEXT and n > today) or (direction == LAST and n <= today):
                return n
        return None


def ordinal_suffix(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


# -- the reader's patterns, built from a layout's own names -----------------------------------

_ORD = r"(\d{1,2})(?:st|nd|rd|th)?"
_DAY_N = re.compile(r"\bday\s+(\d{1,5})\b", re.I)


def _year(said: str | None) -> int | None:
    return int(said) if said else None


@dataclass(frozen=True, slots=True)
class _Patterns:
    day_first: re.Pattern[str]
    name_first: re.Pattern[str]
    festival: re.Pattern[str]


def _alternatives(names: list[str]) -> str:
    """The names as one pattern, longest first so "Midsummer" is never read as "Summer"."""
    return "|".join(re.escape(n) for n in sorted(names, key=lambda n: (-len(n), n.casefold())))


@functools.lru_cache(maxsize=64)
def _patterns(layout: Layout) -> _Patterns:
    every = [p.name for p in layout.parts] + ([layout.leap.name] if layout.leap else [])
    counted = [p.name for p in layout.parts if p.kind != FESTIVAL or p.days > 1]
    alone = [p.name for p in layout.parts if p.kind == FESTIVAL] + (
        [layout.leap.name] if layout.leap else []
    )
    # A year is a number with the year label, or one of 3 or 4 digits with no word after it:
    # "by Midsummer, 1500 gold" is no year, and "Summer 14, 3 guards" no date in year 3.
    bare = r"(?<=\d{3})(?!\s*[^\W\d_])"
    label = rf"\s*{re.escape(layout.year_label)}\b|" if layout.year_label else ""
    year = rf"(?:,?\s*(\d{{1,4}})\b(?:{label}{bare}))?"
    day_first = re.compile(
        rf"\b(?:the\s+)?{_ORD}\s+(?:day\s+)?of\s+({_alternatives(every)})\b{year}", re.I
    )
    name_first = re.compile(rf"\b({_alternatives(counted)})\s+{_ORD}\b{year}", re.I)
    # (a calendar with no festival has nothing to stand alone: a pattern that matches nothing)
    festival = re.compile(
        rf"\b(?:the\s+)?({_alternatives(alone)})\b{year}" if alone else r"(?!x)x(.)(.)", re.I
    )
    return _Patterns(day_first, name_first, festival)


# -- the calendars the repository ships --------------------------------------------------------

# Harptos: 12 months of 30 days and 5 festival days that belong to no month, 365 days a year.
# Shieldmeet is the extra festival day of a leap year (every 4th), right after Midsummer.
HARPTOS = Layout(
    "Harptos",
    (
        Part("Hammer", 30), Part("Midwinter", 1, FESTIVAL),
        Part("Alturiak", 30), Part("Ches", 30), Part("Tarsakh", 30),
        Part("Greengrass", 1, FESTIVAL),
        Part("Mirtul", 30), Part("Kythorn", 30), Part("Flamerule", 30),
        Part("Midsummer", 1, FESTIVAL),
        Part("Eleasis", 30), Part("Eleint", 30), Part("Highharvestide", 1, FESTIVAL),
        Part("Marpenoth", 30), Part("Uktar", 30), Part("Feast of the Moon", 1, FESTIVAL),
        Part("Nightal", 30),
    ),
    Leap(4, "Shieldmeet", "Midsummer"),
    year_label="DR",
)  # fmt: skip


def four_seasons(days: int, name: str = "Four seasons") -> Layout:
    """The plain homebrew calendar: Spring, Summer, Autumn and Winter, `days` days each."""
    return Layout(
        name,
        tuple(Part(n, days, SEASON) for n in ("Spring", "Summer", "Autumn", "Winter")),
    )
