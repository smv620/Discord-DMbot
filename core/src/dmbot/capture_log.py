"""Counts captured speech per interval: who spoke, how much, and audio health (frames
received vs expected).

Every interval goes to the log as one line of IDs and numbers (`log_line`). The DM
screen only hears about it when audio went missing (`render`): what was said goes to
the transcript channel (#124), never here, and a screen full of "all fine" checks hid
the notes that need the DM (#134).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from dmbot.audio.segmenter import Utterance

# Below this percentage of expected frames, audio is flagged (logs, test scoring).
HEALTH_WARN_PERCENT = 95
# The DM screen is only told below this (90-94% rarely costs real words), once per
# person, and again only if it gets clearly worse or after a while: a phone on bad
# Wi-Fi mustn't bury the notes that need the DM.
DM_WARN_PERCENT = 90
WARN_AGAIN_DROP = 10
WARN_AGAIN_AFTER_S = 600.0


def audio_health(received: int, expected: int) -> tuple[int, bool]:
    """(percent to show, whether to flag gaps) for `expected` > 0.

    Uses whole-number maths and rounds down, so a flagged result never shows as 95%
    or more (#44: 94.6% used to read "audio 95% ⚠️ audio gaps"). Received is capped at
    expected: ears caps each utterance (#36), and the sum here must stay capped too.
    """
    received = max(0, min(received, expected))
    flagged = received * 100 < HEALTH_WARN_PERCENT * expected
    return received * 100 // expected, flagged


@dataclass(slots=True)
class _SpeakerStats:
    utterances: int = 0
    seconds: float = 0.0
    frames_received: int = 0
    frames_expected: int = 0


class CaptureLog:
    def __init__(self) -> None:
        self._stats: dict[int, _SpeakerStats] = {}
        self._warned: dict[int, tuple[int, float]] = {}  # user → (percent, when)

    def _get(self, user_id: int) -> _SpeakerStats:
        return self._stats.setdefault(user_id, _SpeakerStats())

    def add_utterance(self, utterance: Utterance) -> None:
        stats = self._get(utterance.user_id)
        stats.utterances += 1
        stats.seconds += utterance.duration_s

    def add_health(self, user_id: int, received: int, expected: int) -> None:
        stats = self._get(user_id)
        # Cap each report, so one over-counted clip can't hide a gap in another.
        stats.frames_received += max(0, min(received, expected))
        stats.frames_expected += max(0, expected)

    def log_line(self) -> str | None:
        """One line for the terminal log: user IDs and numbers only, never names or
        words (#37). Call before render(), which resets. None if nothing was captured."""
        parts: list[str] = []
        for user_id, s in sorted(self._stats.items()):
            if s.utterances == 0:
                continue
            part = f"user {user_id}: {s.utterances} x speech, {s.seconds:.1f} s"
            if s.frames_expected > 0:
                percent, flagged = audio_health(s.frames_received, s.frames_expected)
                part += f", audio {percent}%{' (audio gaps)' if flagged else ''}"
            parts.append(part)
        if not parts:
            return None
        return f"Capture check: {len(parts)} speaker(s); " + "; ".join(parts)

    def render(self, name_of: Callable[[int], str], now: float = 0.0) -> str | None:
        """A warning for the DM screen if someone's voice is cutting out, then reset.
        None (and still reset) otherwise. `now` is a monotonic time in seconds."""
        gaps: list[tuple[str, int]] = []
        for user_id, s in sorted(self._stats.items(), key=lambda kv: -kv[1].seconds):
            if s.utterances == 0 or s.frames_expected == 0:
                continue
            percent, _ = audio_health(s.frames_received, s.frames_expected)
            if percent >= DM_WARN_PERCENT:
                continue
            last = self._warned.get(user_id)
            if (
                last is None
                or percent <= last[0] - WARN_AGAIN_DROP
                or now - last[1] >= WARN_AGAIN_AFTER_S
            ):
                self._warned[user_id] = (percent, now)
                gaps.append((name_of(user_id), percent))
        self._stats.clear()
        if not gaps:
            return None
        tail = (
            "Some of their words may be missing from the transcript. If it keeps up, ask "
            "them to check their internet or rejoin voice. DMbot only mentions it again "
            "if it gets worse."
        )
        if len(gaps) == 1:
            name, percent = gaps[0]
            return f"⚠️ **{name}'s voice is cutting out for DMbot** ({percent}% got through). {tail}"
        who = ", ".join(f"{name} {percent}%" for name, percent in gaps)
        return f"⚠️ **Voices cutting out for DMbot:** {who}. {tail}"


@dataclass(slots=True)
class SpeakerTotal:
    seconds: float = 0.0
    frames_received: int = 0
    frames_expected: int = 0

    @property
    def percent(self) -> int | None:
        """How much of their audio got through, or None if nothing was measured."""
        if self.frames_expected == 0:
            return None
        return audio_health(self.frames_received, self.frames_expected)[0]


class SessionTotals:
    """How much each person spoke in the whole session, and how much of their audio got
    through, for the end-of-session summary (#109). Never reset; numbers only."""

    def __init__(self) -> None:
        self.speakers: dict[int, SpeakerTotal] = {}

    def add_utterance(self, utterance: Utterance) -> None:
        total = self.speakers.setdefault(utterance.user_id, SpeakerTotal())
        total.seconds += utterance.duration_s

    def add_health(self, user_id: int, received: int, expected: int) -> None:
        total = self.speakers.setdefault(user_id, SpeakerTotal())
        total.frames_received += max(0, min(received, expected))
        total.frames_expected += max(0, expected)
