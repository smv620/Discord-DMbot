"""Story threads the DM keeps (#1662; docs/STORY_MEMORY.md, sections 2 and 3). Pure: the read model
of a thread (its state, who is involved and in what part, the events that moved it) and which
threads are stalled. A thread's state is kept like a condition, in `memory_states`: one open at a
time, a new one closes the old. **Stalled is worked out, never stored:** a thread that is hinted,
open or active is stalled when at least N known sessions have started since it was last touched,
where touched means its newest state row (a "checked, still going" counts), its newest
involved/advances/resolves fact, or a session its name was heard in. A session where nobody was
heard doesn't count: nothing could have moved a thread in it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from dmbot.memory.models import ABANDONED, GOING, HINTED, OPEN, RESOLVED, Entity, State

# What a state is called to the DM, and the roles the screen suggests for "who is involved".
STATE_WORDS = {
    HINTED: "Just a hint",
    OPEN: "Not started",
    GOING: "Happening now",
    RESOLVED: "Finished",
    ABANDONED: "Dropped",
}
# A promise says the same five states in its own words (#1674): the states stored are the same,
# and "Due soon" is the DM's own state (the due note is never read as a date). The words are the
# ones Supervisor specified on the issue.
PROMISE_WORDS = {
    HINTED: "Made in passing",
    OPEN: "Open",
    GOING: "Due soon",
    RESOLVED: "Kept",
    ABANDONED: "Broken or let go",
}
PROMISE = "promise"
ROLES = ("quest giver", "target", "villain", "victim", "reward")
ALL_STATES = (HINTED, OPEN, GOING, RESOLVED, ABANDONED)
LIVE_STATES = (HINTED, OPEN, GOING)  # a thread in one of these can stall
STALLED_AFTER = 3  # sessions, unless DMBOT_THREAD_STALLED_SESSIONS says otherwise


def words_for(kind: str) -> dict[str, str]:
    """The state words for this kind of thread: a promise has its own."""
    return PROMISE_WORDS if kind == PROMISE else STATE_WORDS


@dataclass(frozen=True, slots=True)
class Involved:
    who: Entity
    role: str  # the fact's detail: "quest giver", "villain"… or "" when none was said


@dataclass(frozen=True, slots=True)
class Mover:
    event: Entity
    ends_it: bool  # resolves, not just advances
    from_session_at: int | None


@dataclass(frozen=True, slots=True)
class ThreadInfo:
    thread: Entity
    state: State | None
    history: tuple[State, ...]  # oldest first, the current one last
    involved: tuple[Involved, ...]
    movers: tuple[Mover, ...]
    last_touch: int  # when anything last moved it (seconds)
    quiet_sessions: int  # known sessions that have started since
    stalled: bool
    promised_by: tuple[Entity, ...] = ()  # a promise: who made it (the DM's word)
    promised_to: tuple[Entity, ...] = ()  # ...and to whom
    due: str = ""  # ...and the DM's free-text note on when it is due, never parsed


def sessions_since(touch: int, sessions: Iterable[int]) -> int:
    """How many known sessions started after `touch`."""
    return sum(1 for s in sessions if s > touch)


def is_stalled(state: str | None, quiet: int, after: int) -> bool:
    return state in LIVE_STATES and after > 0 and quiet >= after


def last_touch(
    created_at: int,
    states: Sequence[State],
    fact_times: Iterable[int | None],
    fact_saved: Iterable[int],
    heard_in: Iterable[int],
) -> int:
    """The newest of everything that counts as touching a thread."""
    times = [created_at, *heard_in, *fact_saved]
    times += [s.from_session_at if s.from_session_at is not None else s.created_at for s in states]
    times += [t for t in fact_times if t is not None]
    return max(times)


def checked(history: Sequence[State]) -> list[bool]:
    """For each row of a thread's history: is it a "checked, still going" row, the same state
    written again, and not a change? Never the first row."""
    return [n > 0 and row.condition == history[n - 1].condition for n, row in enumerate(history)]


def history_lines(
    history: Sequence[State], numbers: dict[int, int], kind: str = "thread"
) -> list[str]:
    """One plain line a row, oldest first: "Open (Session 2)", "checked, still going (Session 4)",
    "Happening now (Session 5)". A same-value row is never shown as a change of state."""
    lines = []
    words = words_for(kind)
    for row, again in zip(history, checked(history), strict=True):
        number = numbers.get(row.from_session_at) if row.from_session_at is not None else None
        when = f" (Session {number})" if number is not None else ""
        if again:
            lines.append(f"checked, still going{when}")
        else:
            lines.append(f"{words.get(row.condition, row.condition)}{when}")
    return lines


def stalled_line(count: int, quiet: int, promises: int = 0) -> str:
    """For the after-session report: how many threads, and for how long. It says "promise"
    where all of them are promises, and counts them in when only some are."""
    if promises and promises == count:
        what = "promise hasn't" if count == 1 else "promises haven't"
    elif promises:
        extra = "1 is a promise" if promises == 1 else f"{promises} are promises"
        return f"{count} threads haven't moved for {quiet} or more sessions ({extra})."
    else:
        what = "thread hasn't" if count == 1 else "threads haven't"
    return f"{count} {what} moved for {quiet} or more sessions."
