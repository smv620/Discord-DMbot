"""Private scenes between two player characters (#2996 rule 5; Supervisor's ruling, 2026-11-10).
Pure: no database, no AI, nothing written.

The owner's rule (docs/STORY_MEMORY.md §9b): "A private interaction between two player characters
always matters, always." Who learns what is said there is slice 2a's (a split party's one group
learns it); this finds the scenes themselves, so the recap can cite each one without saying what
(#2997) and PlotBot can weigh what was named in it.

A scene is found by code, never by the AI: a stretch of a session in which the game clock has the
party split, and the speakers' group holds exactly two player characters, played by two different
people, both of whom speak in it (one player with two characters is no scene). The clock's own
rule decides "split":
the newest main-clock row at or before a moment belongs to a group (`timebot.stamp`).
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Scene:
    """One private scene: its two player characters (entity ids, sorted), the first and last line's
    start (ms), how many lines, and who spoke them (for the consent paths)."""

    first_id: str
    second_id: str
    from_ms: int
    to_ms: int
    line_count: int
    speaker_ids: tuple[int, ...]


@dataclass
class _Run:
    pair: tuple[str, str]
    from_ms: int
    to_ms: int
    lines: int = 0
    speakers: set[int] = field(default_factory=set)


class Clock:
    """The campaign's game-clock rows, read at a moment. `main`: (at, is a group's row) for each
    main-clock row, oldest first; `placed`: for each player character, (at, group position or
    None) oldest first. Times are Unix seconds."""

    def __init__(
        self,
        main: Sequence[tuple[int, bool]],
        placed: Mapping[str, Sequence[tuple[int, int | None]]],
    ) -> None:
        self._main_at = [at for at, _ in main]
        self._main_group = [group for _, group in main]
        self._placed = {
            c: ([at for at, _ in rows], [p for _, p in rows]) for c, rows in placed.items()
        }

    def split(self, at: int) -> bool:
        i = bisect.bisect_right(self._main_at, at) - 1
        return i >= 0 and self._main_group[i]

    def group(self, character_id: str, at: int) -> int | None:
        rows = self._placed.get(character_id)
        if rows is None:
            return None
        i = bisect.bisect_right(rows[0], at) - 1
        return rows[1][i] if i >= 0 else None


def find(
    lines: Sequence[tuple[int, int]],
    clock: Clock,
    players: Mapping[str, int],
    session_started_at: int,
) -> list[Scene]:
    """The session's private scenes, in the order they began. `lines`: (started_ms, speaker id) in
    the order spoken, `started_ms` counted from `session_started_at` (Unix seconds), the DM's
    sidebar and table questions already left out by the caller; `players`: each confirmed player
    character's id to who plays it. Pure.

    A split session's transcript interleaves the groups (the DM moves between them), so a line of
    another group touches no scene, and two pairs may each have a scene at once. A scene ends when
    the clock rejoins the party or its two are no longer a group of two. A line nobody's character
    places (the DM's, say) is counted in no scene: it can't be told which group it was said to."""
    found: list[Scene] = []
    runs: dict[tuple[str, str], _Run] = {}

    def close(pair: tuple[str, str]) -> None:
        run = runs.pop(pair)
        a, b = pair
        if {players[a], players[b]} <= run.speakers:  # (both players spoke in it)
            found.append(
                Scene(a, b, run.from_ms, run.to_ms, run.lines, tuple(sorted(run.speakers)))
            )

    for started_ms, speaker in lines:
        at = session_started_at + started_ms // 1000  # (the clock's rows are in real time)
        if not clock.split(at):
            for pair in list(runs):
                close(pair)
            continue
        where = {c: clock.group(c, at) for c in players}
        for pair in [p for p in runs if not _still_pair(p, where)]:
            close(pair)
        theirs = {where[c] for c, p in players.items() if p == speaker and where[c] is not None}
        if len(theirs) != 1:
            continue  # (nobody's character places it, or it is in two groups at once)
        (group,) = theirs
        members = tuple(sorted(c for c, g in where.items() if g == group))
        if len(members) != 2 or players[members[0]] == players[members[1]]:
            continue
        pair = (members[0], members[1])
        run = runs.setdefault(pair, _Run(pair, started_ms, started_ms))
        run.lines += 1
        run.to_ms = started_ms
        run.speakers.add(speaker)
    for pair in list(runs):
        close(pair)
    found.sort(key=lambda scene: (scene.from_ms, scene.first_id))
    return found


def _still_pair(pair: tuple[str, str], where: Mapping[str, int | None]) -> bool:
    """Whether these two are still a group of exactly two at this moment."""
    group = where.get(pair[0])
    if group is None or where.get(pair[1]) != group:
        return False
    return sum(1 for g in where.values() if g == group) == 2
