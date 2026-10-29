"""One way to read a short clip into 16 kHz mono PCM in memory (#2162).

A Discord voice message (`sidebar/memo.py`) and a note spoken on the recap page
(`recaps/voice.py`) both arrive as small Opus files and both must stay cheap to read: the clip
is capped in size and length, reading stops the moment it passes the length limit (a small file
can expand to hours), and an optional time budget refuses a crafted stream that decodes slowly.
Each caller keeps its own error words, so this raises a `Refused` with a reason and the caller
turns that into its own message or code. Nothing is written to disk. Blocking: run it off the
event loop.
"""

from __future__ import annotations

import io
import time
from collections.abc import Mapping
from enum import Enum
from typing import TYPE_CHECKING

from dmbot.ears.protocol import BYTES_PER_SAMPLE, SAMPLE_RATE

if TYPE_CHECKING:
    import av


class Why(Enum):
    TOO_BIG = "too_big"  # empty, or more bytes than the caller allows
    NO_SOUND = "no_sound"  # the file has no audio stream
    TOO_LONG = "too_long"  # past the length limit once decoded
    TOO_SLOW = "too_slow"  # past the time budget
    UNREADABLE = "unreadable"  # ffmpeg could not read it
    TOO_SHORT = "too_short"  # shorter than a click worth hearing


class Refused(Exception):
    """The clip was not read. `why` says which check stopped it."""

    def __init__(self, why: Why) -> None:
        super().__init__(why.value)
        self.why = why


def seconds(pcm: bytes) -> float:
    return len(pcm) / (SAMPLE_RATE * BYTES_PER_SAMPLE)


def samples(frame: av.AudioFrame) -> bytes:
    # A plane's buffer can be padded past the last sample.
    return bytes(frame.planes[0])[: frame.samples * BYTES_PER_SAMPLE]


def decode_pcm(
    data: bytes,
    *,
    container: str,
    max_bytes: int,
    max_s: float,
    min_s: float,
    budget_s: float | None = None,
    options: Mapping[str, str] | None = None,
) -> bytes:
    """The clip as 16 kHz mono s16le PCM, or `Refused`.

    `container` names the demuxer (ffmpeg would probe any format it knows on bytes a stranger
    chose); `options` are probe limits for it."""
    import av

    if not data or len(data) > max_bytes:
        raise Refused(Why.TOO_BIG)
    started = time.monotonic()
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    chunks: list[bytes] = []
    total = 0
    limit = int(max_s * SAMPLE_RATE * BYTES_PER_SAMPLE)
    try:
        with av.open(io.BytesIO(data), "r", format=container, options=dict(options or {})) as c:
            if not c.streams.audio:
                raise Refused(Why.NO_SOUND)
            for frame in c.decode(audio=0):
                for out in resampler.resample(frame):
                    chunks.append(samples(out))
                    total += len(chunks[-1])
                if total > limit:
                    raise Refused(Why.TOO_LONG)
                if budget_s is not None and time.monotonic() - started > budget_s:
                    raise Refused(Why.TOO_SLOW)
        chunks.extend(samples(out) for out in resampler.resample(None))
    except (av.FFmpegError, ValueError) as exc:
        raise Refused(Why.UNREADABLE) from exc
    pcm = b"".join(chunks)
    if len(pcm) > limit:
        raise Refused(Why.TOO_LONG)
    if seconds(pcm) < min_s:
        raise Refused(Why.TOO_SHORT)
    return pcm
