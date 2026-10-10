"""Reading a Discord voice message (#935). Pure apart from PyAV decoding bytes in memory.

A voice message is a short Ogg Opus file. It becomes the same 16 kHz mono 16-bit audio the
table's speech is, so it goes through the same speech-to-text. Nothing is written to disk:
the audio is dropped as soon as it is transcribed.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from dmbot.ears.protocol import BYTES_PER_SAMPLE, SAMPLE_RATE

if TYPE_CHECKING:
    import av

MAX_MEMO_S = 30.0  # a question is short; a long memo would hold the speech worker
MAX_MEMO_BYTES = 1_500_000  # about 30 s of Discord's Opus with room to spare
MIN_MEMO_S = 0.4
TOO_LONG = f"Too long. Keep it under {int(MAX_MEMO_S)} seconds."


class MemoError(RuntimeError):
    """The file can't be read as a voice message. The message is plain words for the DM."""


def seconds(pcm: bytes) -> float:
    return len(pcm) / (SAMPLE_RATE * BYTES_PER_SAMPLE)


def decode(data: bytes) -> bytes:
    """The voice message as 16 kHz mono s16le PCM. Blocking: run it off the event loop."""
    import av

    if not data or len(data) > MAX_MEMO_BYTES:
        raise MemoError(TOO_LONG)
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    chunks: list[bytes] = []
    total = 0
    limit = int(MAX_MEMO_S * SAMPLE_RATE * BYTES_PER_SAMPLE)
    try:
        with av.open(io.BytesIO(data), "r", format="ogg") as container:  # what Discord sends
            if not container.streams.audio:
                raise MemoError("That file has no sound. Send a voice message instead.")
            for frame in container.decode(audio=0):
                for out in resampler.resample(frame):
                    chunks.append(_samples(out))
                    total += len(chunks[-1])
                if total > limit:
                    raise MemoError(TOO_LONG)
        chunks.extend(_samples(out) for out in resampler.resample(None))
    except av.FFmpegError as exc:
        raise MemoError("I couldn't read that voice message. Try sending it again.") from exc
    pcm = b"".join(chunks)
    if seconds(pcm) < MIN_MEMO_S:
        raise MemoError("Too short to hear. Try again.")
    return pcm


def _samples(frame: av.AudioFrame) -> bytes:
    # A plane's buffer can be padded past the last sample.
    return bytes(frame.planes[0])[: frame.samples * BYTES_PER_SAMPLE]
