"""A story thread's time limit on the game clock (quests slice 4; design `docs/design/quests.md`
§8, ruled on #4448): the pure part, no Discord and no database.

A time limit always keeps its words. Once it has a game date (`due_minute`, the DM's one tap or
DMbot's read the DM kept), DMbot counts it down on the campaign's clock:
- **"Soon"** is one game day before it, or halfway to it when it was set less than two days
  ahead (§8.4); **"passed"** is the clock at or past it. Both are worked out from the clock when
  shown, never stored (§13 rule 4). What was said once is kept apart (slice 4b).
- **While the party is split** the limit is measured against the group furthest ahead (§8.2).
- **The calendar is the only source of dates** (§8.2): the words are the campaign's own ("due
  Flamerule 14th", or "Day 12" with no calendar set). Nothing here is a real-world date.
- **A passed limit changes nothing by itself** (§8.4): the thread, its steps and its rewards stay
  as they are until the DM changes them.

`stage` and its two words are for slice 4b's messages; 4a shows the countdown (`left_said`).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from dmbot.timebot import calendar as cal
from dmbot.timebot import layout as lay
from dmbot.timebot.clock import MINUTES_PER_DAY, day_of

log = logging.getLogger(__name__)

SOON, PASSED = "soon", "passed"
TWO_DAYS = 2 * MINUTES_PER_DAY
# The DM's one-tap choices (§8.2), in days from the clock's now
QUICK_DAYS = (1, 3, 7)
MOST_DAYS = 3650  # ten game years: past this a typed number is a slip


def soon_mark(due: int, set_at: int | None) -> int:
    """When "soon" is said: one game day before the limit, or halfway to it when it was set less
    than two days ahead (so a limit set for tomorrow isn't "soon" the moment it is set)."""
    if set_at is not None and set_at < due and due - set_at < TWO_DAYS:
        return set_at + (due - set_at) // 2
    return due - MINUTES_PER_DAY


def stage(due: int, set_at: int | None, now: int) -> str | None:
    """Where the clock stands against a dated limit: PASSED, SOON or None (plenty of time)."""
    if now >= due:
        return PASSED
    if now >= soon_mark(due, set_at):
        return SOON
    return None


def furthest(minutes: Iterable[int]) -> int | None:
    """The clock a limit is measured against: the group furthest ahead while the party is split
    (§8.2), else the one clock. None with no clock at all."""
    return max(minutes, default=None)


def date_said(due: int, calendar: cal.Calendar) -> str:
    """The limit's date in the campaign's own calendar: "Flamerule 14th, 1492 DR", "Summer 2nd,
    1201 AR" (a calendar of its own, #5364), or "Day 12". Every card, alert and countdown says its
    dates through here, so they change together."""
    return calendar.words(max(due, 0))


def left_said(due: int, now: int, calendar: cal.Calendar) -> str:
    """What the cards say of a dated limit (§8.3, §8.4): "1 day left (due Day 12)", "5 hours left
    (due …)", or "time's up (was due Day 12)". Whole days while a day or more is left; the
    hours (rounded up) on the last day, so it never reads "0 days"."""
    date = date_said(due, calendar)
    if now >= due:
        return f"time's up (was due {date})"
    left = due - now
    if left >= MINUTES_PER_DAY:
        days = left // MINUTES_PER_DAY
        return f"{days} day{'s' if days != 1 else ''} left (due {date})"
    hours = -(-left // 60)
    return f"{hours} hour{'s' if hours != 1 else ''} left (due {date})"


def days_from(now: int, days: int) -> int:
    """The due minute `days` game days after the clock's now (the one-tap choices and the form's
    number), the same time of day."""
    if not 1 <= days <= MOST_DAYS:
        raise ValueError("days")
    return now + days * MINUTES_PER_DAY


def date_in_words(words: str, calendar: cal.Calendar, now: int) -> int | None:
    """The game minute a limit's words name, at the start of that day, or None (§8.2). Read by
    code with the campaign's own calendar (the one reader, `Layout.parse`: "by the 14th of
    Flamerule", "before Midsummer", "Summer 2nd, 1201 AR", or "Day 12" in any calendar), never the
    AI. A date with no year is its next coming; a date whose day has already begun (today
    included: its countdown would be over at once), a date before Day 1, or one more than
    `MOST_DAYS` ahead is None. Never a real-world date. Two dates in the words are none: DMbot
    can't tell which (the DM taps a date instead)."""
    number = _day_of(words, calendar, now)
    if number is None or number < 1:
        return None
    due = cal.minute_of(number)
    if due <= now or due - now > MOST_DAYS * MINUTES_PER_DAY:
        return None
    return due


def _day_of(words: str, calendar: cal.Calendar, now: int) -> int | None:
    """The game day the words name in this calendar: Harptos and a calendar of its own read their
    own dates, Days only "Day N" (the reader's, which every calendar has)."""
    today = day_of(now)
    if calendar.kind == cal.HARPTOS and calendar.start is not None:
        start = lay.Start(calendar.start.year, calendar.start.ordinal)
        return lay.HARPTOS.parse(words, start, today=today, direction=lay.NEXT)
    if calendar.layout is not None and calendar.layout_start is not None:
        return calendar.layout.parse(words, calendar.layout_start, today=today, direction=lay.NEXT)
    return lay.day_n_in(words)


def offer_for(words: str, clock: tuple[int, cal.Calendar]) -> int | None:
    """`date_in_words` for a card or a menu, which must never fail for it: a fault in the reading
    is no offer (logged), and the DM can still tap 📅 Set a date."""
    try:
        return date_in_words(words, clock[1], clock[0])
    except Exception:
        log.warning("Couldn't read a date in a time limit's words", exc_info=True)
        return None


async def clock_now(
    clocks: Any, guild_id: int, campaign_id: str
) -> tuple[int, cal.Calendar] | None:
    """The clock a limit is counted on (§8.2): the group furthest ahead while the party is split,
    and the campaign's calendar for its words. `clocks` is a `timebot.store.ClockStore` (or None).
    None with no game clock; a failure to read it is no clock too, so a card or the pre-session
    note falls back to the limit's words and never fails for it."""
    if clocks is None:
        return None
    try:
        stored = await clocks.get(guild_id, campaign_id)
        if stored is None:
            return None
        minutes = [stored.clock.minute, *(g.clock.minute for g in stored.groups)]
        return max(minutes), await clocks.calendar(guild_id, campaign_id)
    except Exception:
        log.warning("Couldn't read the game clock for a time limit", exc_info=True)
        return None
