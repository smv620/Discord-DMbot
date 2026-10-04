"""Batches captured utterances into periodic summaries for the DM screen.

Posting every utterance would flood the DM, so Phase 0 reports one compact summary
per interval: who spoke, how much, and audio health (frames received vs expected).
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
    transcripts: list[str] | None = None


class CaptureLog:
    def __init__(self) -> None:
        self._stats: dict[int, _SpeakerStats] = {}

    def _get(self, user_id: int) -> _SpeakerStats:
        return self._stats.setdefault(user_id, _SpeakerStats())

    def add_utterance(self, utterance: Utterance, text: str | None) -> None:
        stats = self._get(utterance.user_id)
        stats.utterances += 1
        stats.seconds += utterance.duration_s
        if text:
            if stats.transcripts is None:
                stats.transcripts = []
            stats.transcripts.append(text)

    def add_health(self, user_id: int, received: int, expected: int) -> None:
        stats = self._get(user_id)
        # Cap each report, so one over-counted clip can't hide a gap in another.
        stats.frames_received += max(0, min(received, expected))
        stats.frames_expected += max(0, expected)

    def render(self, name_of: Callable[[int], str]) -> str | None:
        """Render and reset the summary. Returns None if nothing was captured."""
        if not any(s.utterances for s in self._stats.values()):
            self._stats.clear()
            return None

        lines = ["🎙️ **Capture check**"]
        for user_id, s in sorted(self._stats.items(), key=lambda kv: -kv[1].seconds):
            if s.utterances == 0:
                continue
            line = f"• **{name_of(user_id)}** — {s.utterances} × speech, {s.seconds:.1f} s"
            if s.frames_expected > 0:
                percent, flagged = audio_health(s.frames_received, s.frames_expected)
                flag = " ⚠️ audio gaps" if flagged else ""
                line += f", audio {percent}%{flag}"
            lines.append(line)
            for text in s.transcripts or []:
                lines.append(f"  › {text}")
        self._stats.clear()
        return "\n".join(lines)
