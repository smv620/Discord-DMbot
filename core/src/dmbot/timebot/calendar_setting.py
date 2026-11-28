"""A campaign's calendar as a setting (#5364 slice 3): what Discord's settings and the website's
settings page both send, checked by the one pure function, and the plain words for what is wrong.

The page never counts dates: it sends a kind (`days`, `harptos` or `custom`), for a calendar of its
own the definition (`Layout.to_json`), and where Day 1 falls (`dayOne`: a year, the part's name, the
day in it, and a weekday when the calendar has them). Core makes the `Calendar`, says the preview
line ("Day 1 is Summer 2nd, 1201 AR") and refuses what the calendar has not got, in words for a
person. No database, no Discord, and nothing is stored here.
"""

from __future__ import annotations

from typing import Any

from dmbot.timebot import calendar as cal
from dmbot.timebot import layout as lay
from dmbot.timebot.calendar import Calendar

# One plain sentence for each code `timebot.layout` refuses with, saying what to change. The word
# "part" is the page's own for a month, a season or a festival.
PROBLEMS = {
    "kind": "Pick Days, Harptos, or your own calendar.",
    "big": "That calendar is too long. Take some parts or names out.",
    "layout": "That calendar could not be read. Check each field.",
    "name": (
        "A name needs 1 to 30 characters, starting and ending with a letter or number. "
        "No two names can match, and a name can't be only a number or the words day, of or the."
    ),
    "parts": (
        f"A calendar needs 1 to {lay.MAX_PARTS} parts, and uses months or seasons, not both. "
        "Festivals can go with either."
    ),
    "days": (
        f"Each part needs a whole number of days, and a year can be at most {lay.MAX_YEAR_DAYS}."
    ),
    "leap": (
        "The extra day needs a whole number of years, a name of its own, and a part it follows."
    ),
    "weekdays": f"Weekdays need 2 to {lay.MAX_WEEKDAYS} names, none repeated.",
    "label": (
        f"The year label is letters only, up to {lay.MAX_LABEL}, such as AR. How the voice says it "
        f"needs a label, and up to {lay.MAX_NAME} characters."
    ),
    "date": "Day 1 needs a year between 1 and 9999.",
    "day": "That part doesn't have that day. Check the day.",
    "part": "This calendar has no part with that name. Check the part's name.",
    "weekday": "This calendar has no weekday with that name.",
    "start": "Day 1 must be a day this calendar has. Check the year, the part and the day.",
    "day one": "Day 1 must be a day this calendar has. Check the year, the part and the day.",
}


class CalendarProblem(ValueError):
    """The setting is refused; `problems` is why, in words for a person."""

    def __init__(self, *codes: str) -> None:
        super().__init__(codes[0] if codes else "layout")
        self.problems = [PROBLEMS.get(c, PROBLEMS["layout"]) for c in codes] or [PROBLEMS["layout"]]


MAX_BODY = 16_000  # bytes: the most a stored definition may be (migration 0653's check)
NO_CLOCK_NOTE = "You can save this calendar once the game clock is started."


def _int(value: object) -> int | None:
    """An exact int (a bool or a float is not a year or a day), else None."""
    return value if type(value) is int else None


def from_body(body: object) -> Calendar:
    """The calendar a settings body names, or `CalendarProblem`. `{"kind": "days"}`;
    `{"kind": "harptos", "dayOne": {"year", "part", "day"?}}`; `{"kind": "custom", "def": {...},
    "dayOne": {"year", "part", "day"?, "weekday"?}}`."""
    if not isinstance(body, dict):
        raise CalendarProblem("layout")
    kind = body.get("kind")
    if kind == cal.DAYS:
        return Calendar()
    if kind not in (cal.HARPTOS, cal.CUSTOM):
        raise CalendarProblem("kind")
    one = body.get("dayOne")
    if not isinstance(one, dict):
        raise CalendarProblem("start")
    year, part, day = _int(one.get("year")), one.get("part"), one.get("day")
    if year is None or not isinstance(part, str) or (day is not None and _int(day) is None):
        raise CalendarProblem("start")
    if kind == cal.HARPTOS:
        try:
            return Calendar(cal.HARPTOS, cal.day_one(year, part, day))
        except ValueError as exc:  # (`day_one` says "month" for a part Harptos has not got)
            raise CalendarProblem({"month": "part"}.get(str(exc), str(exc))) from None
    try:
        layout = lay.Layout.from_json(body.get("def"))
    except ValueError as exc:
        raise CalendarProblem(str(exc)) from None
    weekday = one.get("weekday")
    if weekday is not None and not isinstance(weekday, str):
        raise CalendarProblem("weekday")
    try:
        start = layout.start_on(year, part, day, weekday or None)
        return Calendar(cal.CUSTOM, layout=layout, layout_start=start)
    except ValueError as exc:
        code = str(exc)
        # (a part or a day the calendar has not got says so; anything else is Day 1 itself)
        raise CalendarProblem(code if code in PROBLEMS else "start") from None


def today_of(calendar: Calendar, minute: int | None) -> str | None:
    """ "Today is Summer 12th, 1201 AR": where the game clock is now in this calendar, so the DM
    sees the months and the leap day count the way they meant. None for Days or with no clock."""
    if calendar.kind == cal.DAYS or minute is None:
        return None
    return f"Today is {calendar.words(minute)}"


def line_of(calendar: Calendar) -> str | None:
    """ "Day 1 is Summer 2nd, 1201 AR": the preview, worded by `Calendar.words` like every other
    date. None for Days, which has no date to say."""
    if calendar.kind == cal.DAYS:
        return None
    return f"Day 1 is {calendar.words(0)}"


def day_one_of(calendar: Calendar) -> dict[str, Any] | None:
    """Where Day 1 falls, as the settings send it: `{year, part, day, weekday}` (`day` None for a
    one-day festival, `weekday` None with no weekdays). None for Days."""
    if calendar.kind == cal.HARPTOS and calendar.start is not None:
        layout = lay.HARPTOS
        start = lay.Start(calendar.start.year, calendar.start.ordinal)
    elif calendar.layout is not None and calendar.layout_start is not None:
        layout, start = calendar.layout, calendar.layout_start
    else:
        return None
    found = layout.date_of(1, start)
    return {
        "year": found.year,
        "part": found.part,
        "day": None if found.kind == lay.FESTIVAL and found.of == 1 else found.day,
        "weekday": layout.weekday_of(1, start),
    }
