"""A note a DM speaks on the recap page, turned into words by the bot (#2113).

The clip arrives as a short webm or ogg file in memory, becomes the same 16 kHz mono audio the
table's speech is, goes through the table's speech-to-text with the campaign's name hints, and
only the text is kept. Pure apart from PyAV decoding bytes in memory: the clip is never written
to disk, never logged, and the caller deletes it in the same statement that stores the words.
"""

from __future__ import annotations

import io
import math
import time
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


METER_GRACE_S = 2.0  # the browser stops at 60 s but the file decodes to a hair over


def meter_minutes(clip_seconds: float) -> int:
    """What a clip adds to the hours meter: its length rounded up to a whole minute, at least
    one, after a 2 second grace (the meter stores whole minutes). A clip never costs a minute it
    did not use: a full 60 second note, which decodes to a little over 60, costs one."""
    return max(1, math.ceil(max(0.0, clip_seconds - METER_GRACE_S) / 60))


# The only containers read, by the type the page sent: ffmpeg would probe any format it knows on
# bytes a stranger chose, so the demuxer is named.
FORMATS = {"audio/webm": "matroska,webm", "audio/ogg": "ogg"}
DECODE_BUDGET_S = 20.0  # a clip that takes longer than this to read is refused, not waited for
PROBE_OPTIONS = {"probesize": "65536", "analyzeduration": "1000000"}


def decode(data: bytes, content_type: str = "audio/webm") -> bytes:
    """The clip as 16 kHz mono s16le PCM. Blocking: run it off the event loop. Stops reading as
    soon as the clip is past the length limit or the time budget, so a small file that expands to
    hours, or a crafted stream that decodes slowly, costs little."""
    import av

    demuxer = FORMATS.get(content_type)
    if demuxer is None:
        raise ClipError(BAD_CLIP)
    started = time.monotonic()

    if not data or len(data) > MAX_CLIP_BYTES:
        raise ClipError(BAD_CLIP)
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    chunks: list[bytes] = []
    total = 0
    limit = int(MAX_CLIP_S * SAMPLE_RATE * BYTES_PER_SAMPLE)
    try:
        with av.open(io.BytesIO(data), "r", format=demuxer, options=PROBE_OPTIONS) as container:
            if not container.streams.audio:
                raise ClipError(BAD_CLIP)
            for frame in container.decode(audio=0):
                for out in resampler.resample(frame):
                    chunks.append(_samples(out))
                    total += len(chunks[-1])
                if total > limit:
                    raise ClipError(TOO_LONG)
                if time.monotonic() - started > DECODE_BUDGET_S:
                    raise ClipError(BAD_CLIP)
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
