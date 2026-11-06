"""When "Questions for you" reminds a DM (#3435): the backoff, as one pure function.

One DM's state for one campaign (`Notice`) and what is true now go in; the new state and what to do
come out. Nothing here sends anything or reads the database, so every rule is tested alone:

- The first notice goes out once the campaign has a question waiting and the DM has not been told
  (`due_at` is empty); never while the campaign is recording (it waits for the first round after).
- Reminders follow it 1, 2, 4, 8 ... minutes apart (the gap doubles, capped at 24 hours).
- Any answer sets the gap back to 1 minute; a new question after 🔕 Not now ends the mute.
- Nothing waiting clears the timer. Counts and times only.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final, Literal

MINUTE_S: Final = 60
MAX_GAP_MIN: Final = 24 * 60  # a reminder is never more than a day apart

Action = Literal["first", "remind", "clear", "none"]


def gap_minutes(step: int) -> int:
    """Minutes before the next reminder once `step` reminders have been sent: 1, 2, 4, 8 ... up to
    a day."""
    return min(1 << min(step, 20), MAX_GAP_MIN)


@dataclass(frozen=True, slots=True)
class Notice:
    """One DM's timer for one campaign. `due_at` is when the next message is due; empty means the
    DM has not been told yet (or nothing waits)."""

    step: int = 0  # reminders sent since the last first notice or answer
    due_at: int | None = None
    muted: bool = False  # 🔕 Not now: no reminders until a new question
    last_total: int = 0  # how many questions waited at the last round


def next_step(
    notice: Notice,
    *,
    total: int,
    answered: bool,
    grew: bool,
    recording: bool,
    now: int,
) -> tuple[Notice, Action]:
    """The new state and the one thing to do. `answered`: some question was answered since the last
    round (`store.answered`); `grew`: a new question arrived (`store.grew`)."""
    if total <= 0:
        quiet = Notice()
        return quiet, ("clear" if notice != quiet else "none")
    state = replace(notice, last_total=total)
    if grew and state.muted:  # a new question ends 🔕 Not now
        state = replace(state, muted=False, step=0, due_at=now + MINUTE_S)
    if answered and state.due_at is not None:  # the gap starts again from 1 minute
        state = replace(state, step=0, due_at=now + MINUTE_S)
    if recording:
        return state, "none"  # never at the table; whatever was due is sent after it ends
    if state.due_at is None:
        return replace(state, step=0, due_at=now + MINUTE_S, muted=False), "first"
    if state.muted or state.due_at > now:
        return state, "none"
    step = state.step + 1
    return replace(state, step=step, due_at=now + gap_minutes(step) * MINUTE_S), "remind"
