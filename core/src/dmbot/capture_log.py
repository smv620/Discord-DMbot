"""Batches captured utterances into periodic summaries for #dm-screen.

Posting every utterance would flood the DM, so Phase 0 reports one compact summary
per interval: who spoke, how much, and audio health (frames received vs expected).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from dmbot.audio.segmenter import Utterance

# Below this share of expected frames, audio quality is flagged to the DM.
HEALTH_WARN_RATIO = 0.95


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
        stats.frames_received += received
        stats.frames_expected += expected

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
                ratio = min(1.0, s.frames_received / s.frames_expected)
                flag = "" if ratio >= HEALTH_WARN_RATIO else " ⚠️ audio gaps"
                line += f", audio {ratio:.0%}{flag}"
            lines.append(line)
            for text in s.transcripts or []:
                lines.append(f"  › {text}")
        self._stats.clear()
        return "\n".join(lines)
