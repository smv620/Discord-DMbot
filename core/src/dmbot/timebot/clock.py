"""The game clock's arithmetic (#965): pure, no Discord and no database.

Game time is one number: minutes since the start of Day 1. A day has 1440 minutes; dawn
is 06:00, noon 12:00 and dusk 18:00 (docs/PLAN.md, "TimeBot"). Nothing here guesses time
from narration: every change comes from a DM's button or a clear phrase the DM said.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MINUTES_PER_DAY = 1440
DAWN, NOON, DUSK = 6 * 60, 12 * 60, 18 * 60
LONG_REST_MIN = 8 * 60
SHORT_REST_MIN = 60
NO_REST_LIMIT_MIN = MINUTES_PER_DAY  # 24 hours without a long rest
MAX_DAY = 99_999  # keeps the number small enough to store and show
MAX_MINUTE = MAX_DAY * MINUTES_PER_DAY

Mark = Literal["dawn", "noon", "dusk"]
_MARKS: tuple[tuple[int, Mark], ...] = ((DAWN, "dawn"), (NOON, "noon"), (DUSK, "dusk"))
MAX_ANNOUNCED = 3  # a long jump names only the last few, never a list of dozens


@dataclass(frozen=True, slots=True)
class Clock:
    """Where the game is. `last_long_rest` is the game minute the party last finished a
    long rest (the 24-hour rule counts from it); `tired_told_for` is the long-rest minute
    the 24-hour line was already said for, so it is said once."""

    minute: int
    last_long_rest: int
    tired_told_for: int | None = None


def from_day_and_time(day: int, hour: int, minute: int = 0) -> int:
    """Game minutes for Day `day`, `hour`:`minute`. Raises ValueError if out of range."""
    if not 1 <= day <= MAX_DAY:
        raise ValueError("day")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time")
    return (day - 1) * MINUTES_PER_DAY + hour * 60 + minute


def day_of(minute: int) -> int:
    return minute // MINUTES_PER_DAY + 1


def time_of_day(minute: int) -> int:
    return minute % MINUTES_PER_DAY


def phase(minute: int) -> str:
    """A word for the time of day, as a person would say it."""
    t = time_of_day(minute)
    if t < 5 * 60 or t >= 21 * 60:
        return "night"
    if t < NOON:
        return "morning"
    if t < 17 * 60:
        return "afternoon"
    return "evening"


def clock_time(minute: int) -> str:
    t = time_of_day(minute)
    return f"{t // 60:02d}:{t % 60:02d}"


def label(minute: int) -> str:
    """ "Day 4, afternoon (14:30)": the pinned message's words."""
    return f"Day {day_of(minute)}, {phase(minute)} ({clock_time(minute)})"


def short_label(minute: int) -> str:
    return f"Day {day_of(minute)}, {phase(minute)}"


def advance(clock: Clock, delta: int) -> Clock:
    """Move forward (never back past the start or beyond the limit)."""
    return Clock(
        min(MAX_MINUTE, max(0, clock.minute + delta)), clock.last_long_rest, clock.tired_told_for
    )


def long_rest(clock: Clock) -> Clock:
    """Eight hours pass and the party has rested: the 24-hour count starts again from the
    end of the rest."""
    end = min(MAX_MINUTE, clock.minute + LONG_REST_MIN)
    return Clock(end, end, None)


def next_dawn(minute: int) -> int:
    """The next 06:00 at or after `minute` (today's if it hasn't come yet)."""
    today = minute - time_of_day(minute) + DAWN
    return today if today >= minute else today + MINUTES_PER_DAY


def set_to(clock: Clock, minute: int) -> Clock:
    """The DM says what time it is. If that is earlier than the last long rest the rest
    can't be in the future, so it moves back with it."""
    minute = min(MAX_MINUTE, max(0, minute))
    return Clock(minute, min(clock.last_long_rest, minute), clock.tired_told_for)


def crossings(before: int, after: int) -> list[tuple[int, Mark]]:
    """The dawns, noons and dusks passed moving from `before` to `after` (not counting
    `before` itself, counting `after`), in order; at most the last MAX_ANNOUNCED."""
    if after <= before:
        return []
    found: list[tuple[int, Mark]] = []
    first_day, last_day = before // MINUTES_PER_DAY, after // MINUTES_PER_DAY
    for d in range(first_day, last_day + 1):
        for offset, name in _MARKS:
            at = d * MINUTES_PER_DAY + offset
            if before < at <= after:
                found.append((at, name))
    return found[-MAX_ANNOUNCED:]


def tired_due(clock: Clock) -> bool:
    """24 hours or more without a long rest, and not yet said for this rest."""
    return (
        clock.minute - clock.last_long_rest >= NO_REST_LIMIT_MIN
        and clock.tired_told_for != clock.last_long_rest
    )


def told_tired(clock: Clock) -> Clock:
    return Clock(clock.minute, clock.last_long_rest, clock.last_long_rest)
