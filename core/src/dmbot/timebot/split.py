"""Split-party clocks (#1607; docs/PLAN.md, "TimeBot"): pure, no Discord and no database.

When the party splits, one clock is wrong for half the table. The DM names two or three
groups; each starts at the current time and last long rest, and the DM's buttons act on the
group they picked. Rejoining goes back to one clock at a time the DM chooses. Nothing here
moves a clock by itself: every change is a DM's button (spoken phrases move nothing while
split, because DMbot can't know which group was meant).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from dmbot.timebot import clock as game
from dmbot.timebot.clock import Clock
from dmbot.timebot.durations import words

MIN_GROUPS, MAX_GROUPS = 2, 3
NAME_MAX = 20
DRIFT_MIN = 8 * 60  # a group more than this far ahead of another is worth a word

NEED_TWO = "Name at least two groups, like Tower and Camp. Press **Split the party** to try again."
SAME_NAME = "Two groups have the same name. Press **Split the party** again and give each its own."
IN_COMBAT = "End combat first, then split the party."
ALREADY_SPLIT = (
    "The party is already split. Pick a group under the clock, or press **Rejoin the party**."
)
NOT_SPLIT = "The party is together already. Press **Split the party** to split it."


@dataclass(frozen=True, slots=True)
class Group:
    """One part of the party: its place in the list (1 to 3), its short name and its clock."""

    position: int
    name: str
    clock: Clock


def clean_name(text: str) -> str:
    """One short line, no control characters or mentions (the name is said on the DM screen
    and in the players' game-time channel)."""
    kept = "".join(c if c.isprintable() else " " for c in text)
    return " ".join(kept.split()).replace("@", "").replace("<", "").replace(">", "")[:NAME_MAX]


def check_names(raw: list[str]) -> list[str] | str:
    """The cleaned names of the groups (blank boxes dropped), or a plain sentence saying what
    to fix. Two names the same, ignoring capitals, are refused: they could not be told apart
    on the picker."""
    names = [n for n in (clean_name(r) for r in raw) if n]
    if not MIN_GROUPS <= len(names) <= MAX_GROUPS:
        return NEED_TWO
    if len({n.casefold() for n in names}) != len(names):
        return SAME_NAME
    return names


def start(clock: Clock, names: list[str]) -> tuple[Group, ...]:
    """The groups, all at the time and last long rest the party had. Combat is refused
    before this (a fight has one round, not three)."""
    start_at = replace(clock, combat_round=None, combat_secs=0)
    return tuple(Group(i, name, start_at) for i, name in enumerate(names, start=1))


def drift(groups: tuple[Group, ...]) -> tuple[Group, Group, int] | None:
    """(the group furthest ahead, the one furthest behind, the gap in minutes) once the gap is
    more than DRIFT_MIN; None otherwise. A reminder, never a block."""
    if len(groups) < MIN_GROUPS:
        return None
    ahead = max(groups, key=lambda g: (g.clock.minute, -g.position))
    behind = min(groups, key=lambda g: (g.clock.minute, g.position))
    gap = ahead.clock.minute - behind.clock.minute
    return (ahead, behind, gap) if gap > DRIFT_MIN else None


def drift_line(found: tuple[Group, Group, int]) -> str:
    """ "Tower is 9 hours ahead of Camp." (whole hours, rounded down)."""
    ahead, behind, gap = found
    return f"{ahead.name} is {words(gap - gap % 60)} ahead of {behind.name}."


def latest_first(groups: tuple[Group, ...]) -> list[Group]:
    """The groups for the Rejoin question: the latest time first (ties keep the order named)."""
    return sorted(groups, key=lambda g: (-g.clock.minute, g.position))


def rejoin(groups: tuple[Group, ...], keep: int) -> Clock:
    """One clock again: the kept group's time (and fight, if it has one), with the last long
    rest the latest any group had. The 24-hour line stays said only if it was said for that
    very rest."""
    kept = next(g for g in groups if g.position == keep)
    rest = max(g.clock.last_long_rest for g in groups)
    told = kept.clock.tired_told_for if kept.clock.last_long_rest == rest else None
    return replace(kept.clock, last_long_rest=min(rest, kept.clock.minute), tired_told_for=told)


def shifted(ends: int, group_minute: int, kept_minute: int) -> int:
    """A timer's end after its group's time becomes the kept time: the same time left."""
    return min(game.MAX_MINUTE, max(0, ends + kept_minute - group_minute))


def exact_label(minute: int) -> str:
    """ "Day 4, 14:30": the DM's line for one group."""
    return f"Day {game.day_of(minute)}, {game.clock_time(minute)}"


def dm_line(groups: tuple[Group, ...]) -> str:
    """ "Tower: Day 4, 14:30 · Camp: Day 4, 16:10" (the pinned clock's head, inside the bold)."""
    return " · ".join(f"{g.name}: {exact_label(g.clock.minute)}" for g in groups)


def players_lines(groups: tuple[Group, ...]) -> list[str]:
    """One line per group for the game-time channel, the day and a rough time of day only."""
    return [f"{g.name}: {game.short_label(g.clock.minute)}" for g in groups]
