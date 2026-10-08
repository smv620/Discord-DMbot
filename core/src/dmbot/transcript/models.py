"""Stored transcripts' data, and the lines waiting to be saved (#41, #125). Pure: no
Discord, no database."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

MAX_UNSAVED = 5_000  # lines kept while saving keeps failing; the oldest go first


@dataclass(frozen=True, slots=True)
class TranscriptSession:
    id: str
    guild_id: int
    campaign_id: str
    started_at: int  # Unix seconds
    ended_at: int | None  # None while it's running (or if DMbot stopped unexpectedly)
    lines: int = 0
    speakers: tuple[int, ...] = ()
    number: int = 0  # Session 1, 2, … in its campaign (only sessions with lines count)
    engines: tuple[str, ...] = ()  # "engine model host" of each speech-to-text used


@dataclass(frozen=True, slots=True)
class Line:
    started_ms: int  # Unix milliseconds, when the speech started
    user_id: int
    heard: str  # exactly what speech-to-text wrote
    text: str  # cleaned: misheard names fixed (dmbot.transcript.cleaner)


@dataclass(slots=True)
class TranscriptBuffer:
    """Lines written down but not saved yet, for one session. Pure: no database.

    Consent is checked again as each batch is taken (`take`): someone who presses Stop
    never has another line saved, even one already waiting.
    """

    _waiting: list[Line] = field(default_factory=list)
    dropped: int = 0  # thrown away because saving kept failing

    def __len__(self) -> int:
        return len(self._waiting)

    def add(self, line: Line) -> None:
        if not line.heard.strip():
            return
        self._waiting.append(line)
        self._trim()

    def drop_speaker(self, user_id: int) -> None:
        self._waiting = [w for w in self._waiting if w.user_id != user_id]

    def take(self, allowed: Callable[[int], bool]) -> list[Line]:
        """Everything waiting from people still allowed; nothing is left behind."""
        batch = [w for w in self._waiting if allowed(w.user_id)]
        self._waiting = []
        return batch

    def relabel(self, user_id: int, started_ms: int, text: str) -> bool:
        """A waiting line's cleaned words changed (an Undo, #296); True if it was here."""
        for i, waiting in enumerate(self._waiting):
            if (waiting.user_id, waiting.started_ms) == (user_id, started_ms):
                self._waiting[i] = Line(waiting.started_ms, waiting.user_id, waiting.heard, text)
                return True
        return False

    def put_back(self, batch: list[Line]) -> None:
        """Saving failed: keep the batch, ahead of anything newer."""
        self._waiting = batch + self._waiting
        self._trim()

    def _trim(self) -> None:
        extra = len(self._waiting) - MAX_UNSAVED
        if extra > 0:
            self._waiting = self._waiting[extra:]
            self.dropped += extra
