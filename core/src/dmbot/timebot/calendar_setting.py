"""A campaign's calendar as a setting (#5364 slice 3): what Discord's settings and the website's
settings page both send, checked by the one pure function, and the plain words for what is wrong.

The page never counts dates: it sends a kind (`days`, `harptos` or `custom`), for a calendar of its
own the definition (`Layout.to_json`), and where Day 1 falls (`dayOne`: a year, the part's name, the
day in it, and a weekday when the calendar has them). Core makes the `Calendar`, says the preview
line ("Day 1 is Summer 2nd, 1201 AR") and refuses what the calendar has not got, in words for a
person. No database, no Discord, and nothing is stored here.
"""

from __future__ import annotations

import re
from typing import Any

from dmbot.timebot import calendar as cal
from dmbot.timebot import layout as lay
from dmbot.timebot.calendar import Calendar

# One plain sentence for each code `timebot.layout` refuses with, saying what to change. The word
# "part" is the page's own for a month, a season or a festival.
PROBLEMS = {
    "kind": "Pick Days, Harptos, or your own calendar.",
    "big": "That calendar is too long. Take some parts or names out.",
    "layout": "DMbot couldn't read that calendar. Check each box and try again.",
    "name": (
        "Each name needs 1 to 30 letters or numbers, and no two can match. A name can't be just "
        "a number, or the words day, of or the."
    ),
    "parts": (
        f"A calendar needs 1 to {lay.MAX_PARTS} parts, and uses months or seasons, not both. "
        "Festivals can go with either."
    ),
    "days": f"Each part needs 1 or more days. A whole year can have at most {lay.MAX_YEAR_DAYS}.",
    "leap": (
        "The extra day needs a whole number of years, a name of its own, and a part it follows."
    ),
    "weekdays": f"Weekdays need 2 to {lay.MAX_WEEKDAYS} names, none repeated.",
    "label": (
        f"Year label: letters only, up to {lay.MAX_LABEL}, like AR. To add a spoken form, give a "
        f"label first. The spoken form can be up to {lay.MAX_NAME} characters."
    ),
    "date": "The year must be 1 to 9999.",
    "day": "That part is shorter than that. Use a smaller day.",
    "part": "No month, season or festival has that name here. Check the spelling.",
    "weekday": "No weekday here has that name. Check the spelling.",
    "start": "That date isn't in this calendar. Check the year, part and day you typed.",
    "day one": "That date isn't in this calendar. Check the year, part and day you typed.",
}


class CalendarProblem(ValueError):
    """The setting is refused; `problems` is why, in words for a person."""

    def __init__(self, *codes: str, words: str = "") -> None:
        super().__init__(codes[0] if codes else "layout")
        self.problems = [PROBLEMS.get(c, PROBLEMS["layout"]) for c in codes] or [PROBLEMS["layout"]]
        if words:  # (a sentence that names the line a person typed)
            self.problems = [words]


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


# -- the words a person types in Discord's forms (#5364 slice 3c) ---------------------------------
# Discord's forms have five boxes and no lists, so a calendar of its own is typed as short lines and
# turned into the same body the website sends; everything after that is `from_body`.

PROBLEMS.update(
    {
        "parts_text": (
            "Write each part on its own line: its name, its days, then month, season or festival "
            "(month if you leave it out). For example: Hammer 30, or Midwinter 1 festival."
        ),
        "leap_text": (
            "Write the extra day as its name, how many years between, and the part it follows. "
            "For example: Shieldmeet, 4, Midsummer."
        ),
        "number": "That needs a whole number.",
        "year_number": "**Year** needs a whole number, like 1492.",
        "day_number": "**Day** needs a whole number, like 14.",
        "length_number": "**Days in each season** needs a whole number, like 90.",
    }
)
_PART_LINE = re.compile(
    r"^(?P<name>.+?)\s+(?P<days>\d{1,4})(?:\s+(?P<kind>month|season|festival))?$", re.IGNORECASE
)


def parts_from_text(text: str) -> list[dict[str, Any]]:
    """The parts typed one to a line ("Hammer 30", "Summer 90 season", "Midwinter 1 festival") as
    the definition's part list. `CalendarProblem` for a line that is not one. Blank lines are
    skipped; the checks on names and days are `Layout`'s."""
    parts: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        found = _PART_LINE.match(line.strip())
        if found is None:
            shown = line.strip()[:60]
            raise CalendarProblem(
                "parts_text",
                words=f"Line {number}, “{shown}”: write a name, then its days, like Hammer 30.",
            )
        kind = (found["kind"] or "month").lower()
        parts.append({"name": found["name"].strip(), "days": int(found["days"]), "kind": kind})
    if not parts:
        raise CalendarProblem("parts")
    return parts


def leap_from_text(text: str) -> dict[str, Any] | None:
    """ "Shieldmeet, 4, Midsummer" as the extra day (`{every, name, after}`); none when empty."""
    if not text.strip():
        return None
    pieces = [p.strip() for p in text.split(",")]
    if len(pieces) != 3 or not pieces[1].isdecimal() or len(pieces[1]) > 4:
        raise CalendarProblem("leap_text")
    return {"name": pieces[0], "every": int(pieces[1]), "after": pieces[2]}


def leap_line(calendar: Calendar) -> str | None:
    """ "Shieldmeet comes every 4 years, after Midsummer": the extra day, once it applies. None for
    a calendar with none."""
    leap = calendar.layout.leap if calendar.layout is not None else None
    if leap is None:
        return None
    return f"{leap.name} comes every {leap.every} years, after {leap.after}."


def text_of(layout: lay.Layout) -> dict[str, str]:
    """A calendar of its own as the forms' boxes hold it (the other way from
    `definition_from_text`), so a DM changing one month does not retype forty lines."""
    kinds = {lay.MONTH: "", lay.SEASON: " season", lay.FESTIVAL: " festival"}
    parts = "\n".join(f"{p.name} {p.days}{kinds[p.kind]}" for p in layout.parts)
    label = layout.year_label + (f", {layout.year_say}" if layout.year_say else "")
    leap = ""
    if layout.leap is not None:
        leap = f"{layout.leap.name}, {layout.leap.every}, {layout.leap.after}"
    return {
        "name": layout.name,
        "parts": parts,
        "weekdays": ", ".join(layout.weekdays),
        "label": label,
        "leap": leap,
    }


def definition_from_text(
    name: str, parts: str, weekdays: str, label: str, leap: str
) -> dict[str, Any]:
    """The `def` of a calendar typed into a form: its name, the parts one to a line, the weekdays
    split on commas, the year label ("AR", or "AR, after the Reckoning" for how the voice says it)
    and the extra day. Checked by `Layout` afterwards, as for any body."""
    said_label, _, said = label.partition(",")
    found: dict[str, Any] = {
        "name": name.strip(),
        "parts": parts_from_text(parts),
        "leap": leap_from_text(leap),
        "weekdays": [w.strip() for w in weekdays.split(",") if w.strip()],
        "yearLabel": said_label.strip(),
    }
    if said.strip():
        found["yearSay"] = said.strip()
    return found


def day_one_from_text(year: str, part: str, day: str, weekday: str = "") -> dict[str, Any]:
    """The `dayOne` of a form: a whole year, the part's name, the day in it (empty for a one-day
    festival) and the weekday (empty for none)."""
    if not year.strip().isdecimal():
        raise CalendarProblem("year_number")
    if day.strip() and not day.strip().isdecimal():
        raise CalendarProblem("day_number")
    return {
        "year": int(year),
        "part": part.strip(),
        "day": int(day) if day.strip() else None,
        **({"weekday": weekday.strip()} if weekday.strip() else {}),
    }


# -- what to say before saving over a calendar (Supervisor's ruling on #5364) ----------------------
REMOVED = "Your own calendar will be removed."
REPLACES = "This replaces your own calendar."
VOICES = "Recap voices already made keep the old date until you make them again."
COUNTED = "Dates DMbot already counted stay on their day."


def warnings_for(current: Calendar, new: Calendar) -> list[str]:
    """The lines that come before Save: switching from a calendar of its own to Days or Harptos
    removes it; saving one over another replaces it; any change to how dates read leaves recap
    voices already made on the old date; dates already counted stay on their day. Nothing when
    nothing changes."""
    if current == new:
        return []
    out: list[str] = []
    if current.kind == cal.CUSTOM:
        out.append(REMOVED if new.kind != cal.CUSTOM else REPLACES)
    out.append(VOICES)
    out.append(COUNTED)
    return out
