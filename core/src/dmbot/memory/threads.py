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
    came_from: tuple[Entity, ...] = ()  # what led to this thread (the DM's word, #1679)
    led_to: tuple[Entity, ...] = ()  # ...and what it led to


def in_play_first(infos: Iterable[ThreadInfo]) -> list[ThreadInfo]:
    """Live threads for a list: the ones happening now first, then the open ones, then the
    hinted; within each the quietest first, then by name."""
    order = {GOING: 0, OPEN: 1, HINTED: 2}
    return sorted(
        infos,
        key=lambda t: (
            order.get(t.state.condition if t.state else "", 3),
            -t.quiet_sessions,
            t.thread.name.lower(),
            t.thread.id,
        ),
    )


LED_TO = "led_to"
CHAIN_DEPTH = 10  # how far the loop check follows "led to" before it gives up (and refuses)


def loops_through(pairs: Iterable[tuple[str, str]], node: str) -> bool:
    """Is `node` on a loop of the live links in `pairs` (from, to)? Followed to the end (no depth
    limit): a loop is a loop however long (a merge can also push a chain past `CHAIN_DEPTH`, which
    this does not mind; the card readers follow one link at a time). Used before a merge, which
    moves links wholesale."""
    out: dict[str, list[str]] = {}
    for a, b in pairs:
        out.setdefault(a, []).append(b)
    seen: set[str] = set()
    frontier = [node]
    while frontier:
        nxt: list[str] = []
        for current in frontier:
            for to in out.get(current, ()):
                if to == node:
                    return True
                if to not in seen:
                    seen.add(to)
                    nxt.append(to)
        frontier = nxt
    return False


def would_loop(
    pairs: Iterable[tuple[str, str]], start: str, end: str, depth: int = CHAIN_DEPTH
) -> bool:
    """Would "start led to end" close a loop (or be a link to itself)? True when `end` already
    leads, through the live links in `pairs` (from, to), back to `start`. Also True when the new
    link would make the chain below `start` longer than `depth` links (`end` already has
    `depth` links under it): a path nobody could read on a card is not a path."""
    if start == end:
        return True
    out: dict[str, list[str]] = {}
    for a, b in pairs:
        out.setdefault(a, []).append(b)
    seen = {end}
    frontier = [end]
    for _ in range(depth):
        nxt: list[str] = []
        for node in frontier:
            for to in out.get(node, ()):
                if to == start:
                    return True
                if to not in seen:
                    seen.add(to)
                    nxt.append(to)
        if not nxt:
            return False
        frontier = nxt
    return bool(frontier)  # still going after `depth` steps: too deep to trust


CARD_NAMES = 5  # names on a "Came from" / "Led to" line, then "+N more"


def names_line(label: str, names: Sequence[str]) -> str | None:
    """ "Came from: A, B (+2 more)", or None when there is nothing to say."""
    if not names:
        return None
    shown = list(names[:CARD_NAMES])
    more = len(names) - len(shown)
    return f"**{label}:** " + ", ".join(shown) + (f" (+{more} more)" if more > 0 else "")


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
