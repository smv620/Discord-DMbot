"""When DMbot stops a session by itself (#2034, owner decision 2026-10-19): when no person is in
the voice channel for 5 minutes, or nobody has spoken (or typed at the table) for the campaign's
quiet limit (5, 10, 15 or 30 minutes; never off since #2288). A session left running bills
listening minutes against the owner's plan, and a music bot in the channel must not keep it alive.

Either condition first gives a warning, with a Keep listening button; the stop follows
`WARN_S` seconds later if nothing changes. Someone coming back or speaking cancels the warning,
and Keep listening resets both clocks. Pure logic on a clock the caller passes in, so it needs
no Discord and is easy to test; a restart starts a fresh watch (a restart is not silence)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EMPTY_S = 5 * 60  # no person in the channel for this long: warn
WARN_S = 5 * 60  # then wait this long for a Keep listening before stopping
Reason = Literal["empty", "quiet"]
EMPTY: Reason = "empty"
QUIET: Reason = "quiet"


@dataclass(frozen=True, slots=True)
class Verdict:
    action: Literal["none", "warn", "stop"]
    reason: Reason | None = None


NOTHING = Verdict("none")


class IdleWatch:
    """One session's watch. Times are monotonic seconds from the same clock."""

    def __init__(self, now: float) -> None:
        self.last_person = now  # the last time a person was in the channel
        self.last_activity = now  # the last time a person spoke or typed
        self.warned_at: float | None = None
        self.warned_for: Reason | None = None
        self.spoken_seconds = 0.0  # speech totals as last seen, to notice new speech

    def activity(self, now: float) -> None:
        """Someone spoke or typed."""
        self.last_activity = now

    def keep(self, now: float) -> None:
        """The DM pressed Keep listening: both clocks start again."""
        self.last_person = now
        self.last_activity = now
        self.warned_at = None
        self.warned_for = None

    def unwarn(self) -> None:
        """The warning could not be shown: it does not count, so the next check tries again.
        Nobody is stopped without having had the chance to press Keep listening."""
        self.warned_at = None
        self.warned_for = None

    def saw_speech(self, now: float, total_seconds: float) -> None:
        """Speech totals for the session: more than last time means someone spoke."""
        if total_seconds > self.spoken_seconds:
            self.last_activity = now
        self.spoken_seconds = total_seconds

    def poll(self, now: float, *, people: bool, quiet_minutes: int) -> Verdict:
        """Where the watch stands. `people`: a person (never a bot) is in the channel now.
        `quiet_minutes`: the campaign's limit (5, 10, 15 or 30; a 0 is read as off, though the store
        no longer allows it)."""
        if people:
            self.last_person = now
        reason: Reason | None = None
        if not people and now - self.last_person >= EMPTY_S:
            reason = EMPTY
        elif quiet_minutes > 0 and now - self.last_activity >= quiet_minutes * 60:
            reason = QUIET
        if reason is None:  # people are back, or someone spoke: the warning is off
            self.warned_at = None
            self.warned_for = None
            return NOTHING
        if self.warned_at is None:
            self.warned_at, self.warned_for = now, reason
            return Verdict("warn", reason)
        if now - self.warned_at >= WARN_S:
            return Verdict("stop", self.warned_for or reason)  # what the warning said
        return NOTHING
