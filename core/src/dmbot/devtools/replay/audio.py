"""Turns a recording into what ears would have sent core: 16 kHz mono 16-bit PCM in
20 ms frames, one piece of speech at a time.

Live, the Discord client stops sending when someone mutes or goes quiet (after a short
hangover), and ears ends a speaker's stream 800 ms after the last packet
(`SILENCE_END_MS` in ears/src/voice.ts). Here, `SPEECH_END_MS` of near-silence ends a
piece: ears' 800 ms plus the client's hangover, set so DMOnlyAudio.m4a gives run 7's
9 pieces. Quiet stretches inside a piece longer than the hangover are left out, as no
packets arrive for them live; the Segmenter joins the rest end to end.

A recording is not a live session: its dramatic pauses are quiet, not muted, and
Discord's own voice detection and network aren't modelled.
"""

from __future__ import annotations

import math
import sys
import wave
from array import array
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from dmbot.ears.protocol import BYTES_PER_SAMPLE, SAMPLE_RATE

if TYPE_CHECKING:
    import av

FRAME_MS = 20
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * BYTES_PER_SAMPLE
# How long the Discord client keeps sending after speech stops.
HANGOVER_MS = 200
SPEECH_END_MS = 1000  # ears' 800 ms, plus the hangover
# Quieter than this is silence. A muted mic is digital silence (about -90 dBFS); a quiet
# room is around -60 to -50. A recording that wasn't muted between lines has its room
# noise there, so silence is also anything within NOISE_MARGIN_DB of the room's noise.
SILENCE_DBFS = -60.0
NOISE_MARGIN_DB = 10.0
# Never louder than this: in a recording that is nearly all speech, the quietest tenth is
# speech, and a gate set above it would cut words.
SILENCE_MAX_DBFS = -40.0


class DecodeError(RuntimeError):
    """The recording can't be read (a missing decoder, an unknown format)."""


def decode(path: Path) -> bytes:
    """The recording as 16 kHz mono s16le PCM. WAV in exactly that format needs nothing
    extra; anything else (m4a, mp3, other WAVs) needs PyAV: pip install -e ".[twin]"."""
    if path.suffix.casefold() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                if (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (
                    SAMPLE_RATE,
                    1,
                    BYTES_PER_SAMPLE,
                ):
                    return w.readframes(w.getnframes())
        except wave.Error:
            pass  # a WAV the standard library can't read (float, extensible): try PyAV
    try:
        import av
    except ImportError as exc:
        raise DecodeError(
            f'Reading {path.name} needs PyAV: pip install -e ".[twin]" (in core/)'
        ) from exc
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    chunks: list[bytes] = []
    try:
        with av.open(str(path)) as container:
            if not container.streams.audio:
                raise DecodeError(f"{path.name} has no sound in it")
            for frame in container.decode(audio=0):
                chunks.extend(_samples(out) for out in resampler.resample(frame))
        chunks.extend(_samples(out) for out in resampler.resample(None))
    except av.FFmpegError as exc:
        raise DecodeError(f"Can't read {path.name}: {exc}") from exc
    return b"".join(chunks)


def _samples(frame: av.AudioFrame) -> bytes:
    # A plane's buffer can be padded past the last sample.
    return bytes(frame.planes[0])[: frame.samples * BYTES_PER_SAMPLE]


def frame_dbfs(frame: bytes) -> float:
    samples = array("h", frame)
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return -math.inf
    mean_square = sum(s * s for s in samples) / len(samples)
    return 10 * math.log10(mean_square / 32768**2) if mean_square else -math.inf


def frame_levels(pcm: bytes) -> list[float]:
    """The level of every 20 ms frame, in dBFS."""
    return [
        frame_dbfs(pcm[i : i + FRAME_BYTES])
        for i in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES)
    ]


def silence_dbfs_for(levels: list[float]) -> float:
    """How quiet counts as silence in this recording: SILENCE_DBFS, or the room's noise
    (its quietest tenth) plus NOISE_MARGIN_DB if that is louder, as a voice gate does,
    but never above SILENCE_MAX_DBFS."""
    ranked = sorted(max(level, -120.0) for level in levels)
    if not ranked:
        return SILENCE_DBFS
    noise = ranked[len(ranked) // 10]
    return min(SILENCE_MAX_DBFS, max(SILENCE_DBFS, noise + NOISE_MARGIN_DB))


@dataclass(frozen=True, slots=True)
class Piece:
    """One piece of speech: the frames ears would have sent, each with its time."""

    frames: tuple[tuple[int, bytes], ...]  # (milliseconds from the start, 20 ms of PCM)

    @property
    def start_ms(self) -> int:
        return self.frames[0][0]

    @property
    def end_ms(self) -> int:
        return self.frames[-1][0] + FRAME_MS


def pieces(
    pcm: bytes,
    *,
    silence_dbfs: float = SILENCE_DBFS,
    speech_end_ms: int = SPEECH_END_MS,
    hangover_ms: int = HANGOVER_MS,
    lead_in_ms: int = 0,
    levels: list[float] | None = None,
) -> Iterator[Piece]:
    """Pieces of speech: from the first loud frame to the last one before
    `speech_end_ms` of silence. Inside a piece, up to `hangover_ms` of quiet after speech
    is sent, as the Discord client does; longer quiet isn't. `lead_in_ms`: also send this
    much of the audio just before each piece, as a voice gate opens a little early (live,
    the client's packets start before the first loud moment)."""
    end_frames = speech_end_ms // FRAME_MS
    hangover_frames = hangover_ms // FRAME_MS
    before: deque[tuple[int, bytes]] = deque(maxlen=max(0, lead_in_ms // FRAME_MS))
    current: list[tuple[int, bytes]] = []
    quiet: list[tuple[int, bytes]] = []
    for index in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
        at_ms = index // BYTES_PER_SAMPLE * 1000 // SAMPLE_RATE
        frame = (at_ms, pcm[index : index + FRAME_BYTES])
        level = levels[index // FRAME_BYTES] if levels is not None else frame_dbfs(frame[1])
        loud = level > silence_dbfs
        if not current:
            if loud:
                current = [*before, frame]
                before.clear()
            elif before.maxlen:
                before.append(frame)
            continue
        if loud:
            current.extend(quiet[:hangover_frames])
            quiet = []
            current.append(frame)
        else:
            quiet.append(frame)
            if len(quiet) >= end_frames:
                yield Piece(tuple(current))
                if before.maxlen:
                    before.extend(quiet)  # the quiet that ended it can lead into the next
                current, quiet = [], []
    if current:
        yield Piece(tuple(current))
