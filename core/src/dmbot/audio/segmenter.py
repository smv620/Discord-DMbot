"""Groups streamed PCM frames into per-speaker utterances.

An utterance ends when ears reports the speaker stopped, when the speaker has been
quiet longer than the idle timeout (in case an end event was lost), or when it hits
the maximum length (keeps transcription latency and memory bounded).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from dmbot.ears.protocol import BYTES_PER_SAMPLE, SAMPLE_RATE, AudioFrame

MAX_UTTERANCE_MS = 15_000
IDLE_TIMEOUT_MS = 2_000


@dataclass(frozen=True, slots=True)
class Utterance:
    guild_id: int
    user_id: int
    start_ms: int
    end_ms: int
    pcm: bytes
    # Which session's segmenter cut it, so speech still queued when one session stops
    # can never land in the next one (another campaign's transcript, say).
    session: int = 0

    @property
    def duration_s(self) -> float:
        return len(self.pcm) / BYTES_PER_SAMPLE / SAMPLE_RATE


@dataclass(slots=True)
class _Buffer:
    start_ms: int
    last_ms: int
    chunks: list[bytes] = field(default_factory=list)
    size: int = 0


_MAX_BYTES = MAX_UTTERANCE_MS * SAMPLE_RATE // 1000 * BYTES_PER_SAMPLE


_sessions = itertools.count(1)


class Segmenter:
    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self.session = next(_sessions)  # one segmenter per session
        self._buffers: dict[int, _Buffer] = {}

    @property
    def active_speakers(self) -> int:
        return len(self._buffers)

    def add(self, frame: AudioFrame) -> Utterance | None:
        """Add a frame. Returns an utterance if this frame filled one to the maximum length."""
        buf = self._buffers.get(frame.user_id)
        if buf is None:
            buf = _Buffer(start_ms=frame.timestamp_ms, last_ms=frame.timestamp_ms)
            self._buffers[frame.user_id] = buf
        buf.chunks.append(frame.pcm)
        buf.size += len(frame.pcm)
        buf.last_ms = frame.timestamp_ms
        if buf.size >= _MAX_BYTES:
            return self.end(frame.user_id)
        return None

    def end(self, user_id: int) -> Utterance | None:
        """Close the speaker's current utterance, if any."""
        buf = self._buffers.pop(user_id, None)
        if buf is None or buf.size == 0:
            return None
        return Utterance(
            guild_id=self.guild_id,
            user_id=user_id,
            start_ms=buf.start_ms,
            end_ms=buf.last_ms,
            pcm=b"".join(buf.chunks),
            session=self.session,
        )

    def drop(self, user_id: int) -> None:
        """Discard a speaker's buffered audio without emitting it (consent revoked)."""
        self._buffers.pop(user_id, None)

    def flush_idle(self, now_ms: int) -> list[Utterance]:
        """Close utterances whose speaker has been silent longer than the idle timeout."""
        idle = [uid for uid, b in self._buffers.items() if now_ms - b.last_ms > IDLE_TIMEOUT_MS]
        return [u for uid in idle if (u := self.end(uid)) is not None]

    def flush_all(self) -> list[Utterance]:
        return [u for uid in list(self._buffers) if (u := self.end(uid)) is not None]
