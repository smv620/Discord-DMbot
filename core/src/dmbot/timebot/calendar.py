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

from collections.abc import Mapping
from dataclasses import dataclass

from dmbot.timebot import layout as layout_mod
from dmbot.timebot.clock import MINUTES_PER_DAY, clock_time, day_of, day_part, time_of_day

DAYS = "days"  # "Day 12": the default, no setting needed
HARPTOS = "harptos"
CUSTOM = "custom"  # a campaign's own calendar (#5364): its layout rides on the `Calendar`
KINDS = (DAYS, HARPTOS, CUSTOM)

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


def day_number_of(year: int, name: str, day: int | None, start: DayOne) -> int:
    """The game day (Day 1 is `start`) of a Harptos date: the other way round from `harptos`. Raises
    ValueError for a date the calendar has not got (`day_one`'s checks) or one before Day 1."""
    target = day_one(year, name, day)
    if (target.year, target.ordinal) < (start.year, start.ordinal):
        raise ValueError("before day one")
    days = sum(year_days(y) for y in range(start.year, target.year))
    return days + target.ordinal - start.ordinal + 1


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
    Day N, needs neither. A custom calendar (#5364) carries its layout and the date of Day 1 in
    that layout (`Start`), since its year may be longer than Harptos's `DayOne` allows."""

    kind: str = DAYS
    start: DayOne | None = None
    layout: layout_mod.Layout | None = None
    layout_start: layout_mod.Start | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS or (self.kind == HARPTOS and self.start is None):
            raise ValueError("calendar")
        if self.kind == CUSTOM:
            if self.layout is None or self.layout_start is None or self.start is not None:
                raise ValueError("calendar")
            self.layout.check_start(self.layout_start)
        elif self.layout is not None or self.layout_start is not None:
            raise ValueError("calendar")

    def words(self, minute: int, *, weekday: bool = False, spoken: bool = False) -> str:
        """The date of this game minute: Day N, the Harptos date, or the custom calendar's. With
        `weekday`, a calendar that has weekdays puts the weekday first (the DM's full clock card
        only); any other says what it always did. With `spoken`, a calendar of its own says its year
        label as the DM set it for the voice."""
        if self.kind == CUSTOM and self.layout is not None and self.layout_start is not None:
            if type(minute) is not int or minute < 0:
                raise ValueError("minute")
            return self.layout.words(
                day_of(minute), self.layout_start, weekday=weekday, spoken=spoken
            )
        return date_words(minute, self.kind, self.start)


def def_of(calendar: Calendar) -> dict[str, object]:
    """What a custom calendar keeps in `game_clocks.calendar_def` and a backup: its definition and
    the weekday of Day 1 (the year and the day of the year go in their own columns)."""
    if calendar.kind != CUSTOM or calendar.layout is None or calendar.layout_start is None:
        raise ValueError("calendar")
    return {"layout": calendar.layout.to_json(), "weekday": calendar.layout_start.weekday}


def custom_from(year: int, ordinal: int, raw: object) -> Calendar:
    """A custom calendar from its stored parts, checked as strictly as one made by hand. Raises
    ValueError for anything the calendar has not got (a damaged row or file)."""
    if not isinstance(raw, dict) or set(raw) != {"layout", "weekday"}:
        raise ValueError("calendar")
    weekday = raw["weekday"]
    if type(weekday) is not int or type(year) is not int or type(ordinal) is not int:
        raise ValueError("calendar")
    found = layout_mod.Layout.from_json(raw["layout"])
    return Calendar(CUSTOM, layout=found, layout_start=layout_mod.Start(year, ordinal, weekday))


def day_words(
    day: int | None, calendar: Calendar | None = None, *, spoken: bool = False
) -> str | None:
    """The date of game day `day` (the day number a recap job keeps) in the campaign's calendar:
    "Summer 5th, 1201 AR", or "Day 5" with none. None when the day is not known."""
    if not day or day < 1:
        return None
    return (calendar or Calendar()).words(minute_of(day), spoken=spoken)


def names_of(kind: object, definition: object) -> tuple[str, ...]:
    """Every month, season, festival and weekday name of a campaign's calendar, for the name
    finders' leave-out list: none of them is ever offered as a new name. Harptos has its own; a
    calendar of its own gives its definition's; Days and a definition that no longer reads give
    none (a calendar must never stop the name scan)."""
    if kind == HARPTOS:
        return layout_mod.HARPTOS.names
    if kind != CUSTOM or not isinstance(definition, dict):
        return ()
    try:
        return layout_mod.Layout.from_json(definition.get("layout")).names
    except ValueError:
        return ()


def from_door(got: Mapping[str, object]) -> Calendar:
    """The calendar from what the website's clock door gives (`dmbot_web_game_clock`: `kind`,
    `year`, `ordinal` and, for a campaign's own calendar, `def`). Day N for none, and for any
    setting that no longer reads (a calendar must never stop a page)."""
    kind, year, ordinal = got.get("kind"), got.get("year"), got.get("ordinal")
    if type(year) is not int or type(ordinal) is not int:
        return Calendar()
    try:
        if kind == HARPTOS:
            return Calendar(HARPTOS, DayOne(year, ordinal))
        if kind == CUSTOM:
            return custom_from(year, ordinal, got.get("def"))
    except ValueError:  # (e.g. a leap day in a common year)
        return Calendar()
    return Calendar()


# -- the clock's lines, the date part always `Calendar.words` -----------------------------------
# One printed form everywhere ("Summer 5th, 1201 AR, afternoon (14:30)"): a line built by hand would
# drift from the golden Harptos form. With no calendar they are the clock's own "Day 5, ..." lines
# (`clock.label`, `short_label`), so a campaign that never set one sees what it always saw.


def label(minute: int, calendar: Calendar | None = None) -> str:
    """ "Summer 5th, 1201 AR, afternoon (14:30)": the DM's pinned message, with the weekday first
    where the calendar has them."""
    when = (calendar or Calendar()).words(minute, weekday=True)
    t = time_of_day(minute)
    return f"{when}, {day_part(minute)} ({t // 60}:{t % 60:02d})"


def short_label(minute: int, calendar: Calendar | None = None) -> str:
    """ "Summer 5th, 1201 AR, afternoon": the players' clock, with no exact time."""
    return f"{(calendar or Calendar()).words(minute)}, {day_part(minute)}"


def exact_label(minute: int, calendar: Calendar | None = None) -> str:
    """ "Summer 5th, 1201 AR, 14:30": the DM's line for one group."""
    return f"{(calendar or Calendar()).words(minute)}, {clock_time(minute)}"
