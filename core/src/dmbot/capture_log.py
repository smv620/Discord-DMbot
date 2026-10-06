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

# Below this percentage of expected frames, audio quality is flagged to the DM.
HEALTH_WARN_PERCENT = 95


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

    def render(self, name_of: Callable[[int], str]) -> str | None:
        """A warning for the DM screen if anyone's audio had gaps, then reset. None
        (and still reset) when everything arrived."""
        gaps: list[str] = []
        for user_id, s in sorted(self._stats.items(), key=lambda kv: -kv[1].seconds):
            if s.utterances == 0 or s.frames_expected == 0:
                continue
            percent, flagged = audio_health(s.frames_received, s.frames_expected)
            if flagged:
                gaps.append(f"**{name_of(user_id)}** {percent}%")
        self._stats.clear()
        if not gaps:
            return None
        return (
            "⚠️ **Some of what was said didn't reach DMbot** (audio received: "
            f"{', '.join(gaps)}). A few words may be missing from the transcript."
        )
