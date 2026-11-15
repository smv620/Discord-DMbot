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
"""

from __future__ import annotations

from collections.abc import Iterable

from dmbot.timebot import calendar as cal
from dmbot.timebot.clock import MINUTES_PER_DAY

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
    """The limit's date in the campaign's own calendar: "Flamerule 14th, 1492 DR", or "Day 12"."""
    return cal.date_words(max(due, 0), calendar.kind, calendar.start)


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
