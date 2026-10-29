"""The game clock's arithmetic (#965): pure, no Discord and no database.

Game time is one number: minutes since the start of Day 1. A day has 1440 minutes; dawn
is 06:00, noon 12:00 and dusk 18:00 (docs/PLAN.md, "TimeBot"). Nothing here guesses time
from narration: every change comes from a DM's button or a clear phrase the DM said.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    # Combat (#1127): the round (None out of combat) and the game seconds already spent toward
    # the next whole minute. The clock counts whole minutes and a round is 6 seconds, so the
    # minute moves every tenth round and when combat ends.
    combat_round: int | None = None
    combat_secs: int = 0


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


# The players' approximate words (docs/PLAN.md, "TimeBot", the game-time channel): the minute
# of the day each word starts at, fixed hours, no seasons (Supervisor, 2026-10-13: dawn is
# 05:00 to 06:14 and morning 06:15 to 09:59, so 06:00, the dawn mark, reads "dawn").
# "near midnight" spans midnight (23:15 to 00:44), so the day starts inside "near midnight"
# and the table below begins at 00:45 with "late night".
_DAY_PARTS: tuple[tuple[int, str], ...] = (
    (0, "near midnight"),
    (45, "late night"),
    (3 * 60 + 30, "pre-dawn"),
    (5 * 60, "dawn"),
    (6 * 60 + 15, "morning"),  # dawn runs to 06:14, so the dawn mark (06:00) reads "dawn"
    (10 * 60, "late morning"),
    (11 * 60 + 30, "about noon"),
    (13 * 60, "afternoon"),
    (15 * 60, "late afternoon"),
    (17 * 60, "early evening"),
    (18 * 60, "dusk"),
    (19 * 60, "evening"),
    (20 * 60 + 30, "late evening"),
    (22 * 60, "night"),
    (23 * 60 + 15, "near midnight"),
)


def day_part(minute: int) -> str:
    """The approximate word for the time of day: one of exactly 14, for the players' clock
    ("Day 5, morning") and, with the exact time, the DM's."""
    t = time_of_day(minute)
    word = _DAY_PARTS[0][1]
    for start, name in _DAY_PARTS:
        if t < start:
            break
        word = name
    return word


def clock_time(minute: int) -> str:
    """ "06:20": the form's default, which the Rests & time form reads back."""
    t = time_of_day(minute)
    return f"{t // 60:02d}:{t % 60:02d}"


def label(minute: int) -> str:
    """ "Day 5, morning (6:20)": the DM's pinned message (the players see only short_label)."""
    t = time_of_day(minute)
    return f"Day {day_of(minute)}, {day_part(minute)} ({t // 60}:{t % 60:02d})"


def short_label(minute: int) -> str:
    """ "Day 5, morning": the players' clock, with no exact time (also used in the DM's short
    notes)."""
    return f"Day {day_of(minute)}, {day_part(minute)}"


def advance(clock: Clock, delta: int) -> Clock:
    """Move forward (never back past the start or beyond the limit)."""
    return replace(clock, minute=min(MAX_MINUTE, max(0, clock.minute + delta)))


def long_rest(clock: Clock) -> Clock:
    """Eight hours pass and the party has rested: the 24-hour count starts again from the
    end of the rest."""
    end = min(MAX_MINUTE, clock.minute + LONG_REST_MIN)
    return replace(clock, minute=end, last_long_rest=end, tired_told_for=None)


def skip_days(clock: Clock, days: int, *, rested: bool) -> Clock:
    """Whole days pass (a journey, downtime): the same time of day, `days` on. A journey of days
    includes nights' sleep, so the party counts as rested from the end of the skip unless the DM
    said it was without rest (#1594)."""
    moved = advance(clock, days * MINUTES_PER_DAY)
    if not rested:
        return moved
    return replace(moved, last_long_rest=moved.minute, tired_told_for=None)


def next_dawn(minute: int) -> int:
    """The next 06:00 at or after `minute` (today's if it hasn't come yet)."""
    today = minute - time_of_day(minute) + DAWN
    return today if today >= minute else today + MINUTES_PER_DAY


def set_to(clock: Clock, minute: int) -> Clock:
    """The DM says what time it is. If that is earlier than the last long rest the rest
    can't be in the future, so it moves back with it."""
    minute = min(MAX_MINUTE, max(0, minute))
    return replace(clock, minute=minute, last_long_rest=min(clock.last_long_rest, minute))


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
    return replace(clock, tired_told_for=clock.last_long_rest)


# ---- combat (#1127) ----------------------------------------------------------------------

ROUND_SECONDS = 6
MAX_ROUND = 99


def in_combat(clock: Clock | None) -> bool:
    """The one question other parts ask: is the table in combat? (The 15-minute sheet reads
    pause in combat; FightBot runs only in combat.)"""
    return clock is not None and clock.combat_round is not None


def _spend(clock: Clock, seconds: int) -> Clock:
    """Game seconds pass: whole minutes move the clock, the rest wait for the next round."""
    total = clock.combat_secs + seconds
    return replace(advance(clock, total // 60), combat_secs=total % 60)


def combat_start(clock: Clock) -> Clock | None:
    """Round 1. None if combat is already on: saying "roll initiative" twice is one fight."""
    if clock.combat_round is not None:
        return None
    return replace(clock, combat_round=1, combat_secs=0)


def combat_round_to(clock: Clock, number: int) -> Clock | None:
    """Set the round ("round three"). Going forward spends 6 seconds for every round passed;
    going back is a correction and spends nothing. None if not in combat, already there, or
    out of range."""
    current = clock.combat_round
    if current is None or number == current or not 1 <= number <= MAX_ROUND:
        return None
    moved = _spend(clock, (number - current) * ROUND_SECONDS) if number > current else clock
    return replace(moved, combat_round=number)


def combat_next(clock: Clock) -> Clock | None:
    """The next round (at most round 99); None if not in combat."""
    if clock.combat_round is None:
        return None
    return combat_round_to(clock, clock.combat_round + 1)


def combat_end(clock: Clock) -> Clock | None:
    """Combat is over: the round just played counts, and the clock rounds up to the next
    whole minute. None if not in combat."""
    if clock.combat_round is None:
        return None
    total = clock.combat_secs + ROUND_SECONDS
    ended = advance(clock, -(-total // 60))  # rounded up: never less than a minute
    return replace(ended, combat_round=None, combat_secs=0)


def combat_label(clock: Clock) -> str | None:
    """ "⚔️ Combat, round 3" while in combat; the players' line and the DM's."""
    return None if clock.combat_round is None else f"⚔️ Combat, round {clock.combat_round}"
