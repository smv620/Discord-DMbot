"""A note a DM speaks on the recap page, turned into words by the bot (#2113).

The clip arrives as a short webm or ogg file in memory, becomes the same 16 kHz mono audio the
table's speech is, goes through the table's speech-to-text with the campaign's name hints, and
only the text is kept. Pure apart from PyAV decoding bytes in memory: the clip is never written
to disk, never logged, and the caller deletes it in the same statement that stores the words.
"""

from __future__ import annotations

import io
import math
from typing import TYPE_CHECKING

from dmbot.ears.protocol import BYTES_PER_SAMPLE, SAMPLE_RATE

if TYPE_CHECKING:
    import av

MAX_CLIP_BYTES = 2 * 1024 * 1024  # the same limit the database door enforces
MAX_CLIP_S = 120.0  # two minutes
MIN_CLIP_S = 0.4  # shorter is a click, not a note
TYPES = ("audio/webm", "audio/ogg")

BAD_CLIP = "bad_clip"  # not audio DMbot can read, or too short to hear
TOO_LONG = "too_long"  # over two minutes
NO_WORDS = "no_words"  # the engine heard nothing
TRANSCRIBE_FAILED = "transcribe_failed"  # the engine failed: try again
NO_CONSENT = "no_consent"  # the DM has not agreed to be recorded (or stopped meanwhile)


class ClipError(RuntimeError):
    """The clip can't be used. `code` is one of the words above, safe to show and to log."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def seconds(pcm: bytes) -> float:
    return len(pcm) / (SAMPLE_RATE * BYTES_PER_SAMPLE)


def meter_minutes(clip_seconds: float) -> int:
    """What a clip adds to the hours meter: its length rounded up to the next whole minute, at
    least one (the meter stores whole minutes, so it never counts less than was heard)."""
    return max(1, math.ceil(clip_seconds / 60))


def decode(data: bytes) -> bytes:
    """The clip as 16 kHz mono s16le PCM. Blocking: run it off the event loop. Stops reading as
    soon as the clip is past the limit, so a small file that expands to hours costs little."""
    import av

    if not data or len(data) > MAX_CLIP_BYTES:
        raise ClipError(BAD_CLIP)
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    chunks: list[bytes] = []
    total = 0
    limit = int(MAX_CLIP_S * SAMPLE_RATE * BYTES_PER_SAMPLE)
    try:
        # The container is probed from the bytes (webm from a browser, ogg), not taken on trust
        # from the type the caller sent.
        with av.open(io.BytesIO(data), "r") as container:
            if not container.streams.audio:
                raise ClipError(BAD_CLIP)
            for frame in container.decode(audio=0):
                for out in resampler.resample(frame):
                    chunks.append(_samples(out))
                    total += len(chunks[-1])
                if total > limit:
                    raise ClipError(TOO_LONG)
        chunks.extend(_samples(out) for out in resampler.resample(None))
    except (av.FFmpegError, ValueError) as exc:
        raise ClipError(BAD_CLIP) from exc
    pcm = b"".join(chunks)
    if len(pcm) > limit:
        raise ClipError(TOO_LONG)
    if seconds(pcm) < MIN_CLIP_S:
        raise ClipError(BAD_CLIP)
    return pcm


def _samples(frame: av.AudioFrame) -> bytes:
    # A plane's buffer can be padded past the last sample.
    return bytes(frame.planes[0])[: frame.samples * BYTES_PER_SAMPLE]
