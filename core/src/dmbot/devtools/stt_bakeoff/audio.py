"""Audio for the bake-off: decode recordings, make them sound like Discord, and cut
them into one clip per script line.

Decoding and Opus use PyAV, which the core image already has (it comes with
faster-whisper). It's imported only when needed, so the scoring code and its tests
don't need it.
"""

from __future__ import annotations

import io
import logging
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

log = logging.getLogger(__name__)

RATE = 16_000  # what DMbot sends to speech-to-text
DISCORD_RATE = 48_000
DISCORD_BITRATE = 64_000  # Discord's default voice bitrate
FRAME_MS = 20

Pcm = npt.NDArray[np.int16]


def decode(path: Path, rate: int) -> Pcm:
    """Any audio file -> mono 16-bit PCM at `rate`."""
    import av

    out: list[Pcm] = []
    with av.open(str(path)) as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        for frame in container.decode(audio=0):
            for f in resampler.resample(frame):
                out.append(f.to_ndarray().reshape(-1).astype(np.int16))
        for f in resampler.resample(None):
            out.append(f.to_ndarray().reshape(-1).astype(np.int16))
    return np.concatenate(out) if out else np.zeros(0, dtype=np.int16)


def discord_like(pcm48: Pcm) -> Pcm | None:
    """Opus at Discord's voice bitrate and back, resampled to 16 kHz mono.

    Returns None if this PyAV build can't encode Opus (the clean audio is used then,
    and the report says so).
    """
    import av

    try:
        buf = io.BytesIO()
        with av.open(buf, "w", format="ogg") as oc:
            stream: Any = oc.add_stream("libopus", rate=DISCORD_RATE)
            stream.bit_rate = DISCORD_BITRATE
            stream.layout = "mono"
            step = DISCORD_RATE * FRAME_MS // 1000
            pts = 0
            for start in range(0, len(pcm48), step):
                chunk = pcm48[start : start + step]
                if len(chunk) < step:
                    chunk = np.pad(chunk, (0, step - len(chunk)))
                frame = av.AudioFrame.from_ndarray(
                    chunk.reshape(1, -1), format="s16", layout="mono"
                )
                frame.sample_rate = DISCORD_RATE
                frame.pts = pts
                pts += step
                for packet in stream.encode(frame):
                    oc.mux(packet)
            for packet in stream.encode(None):
                oc.mux(packet)
        buf.seek(0)
        out: list[Pcm] = []
        with av.open(buf, "r", format="ogg") as ic:
            resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
            for frame in ic.decode(audio=0):
                for f in resampler.resample(frame):
                    out.append(f.to_ndarray().reshape(-1).astype(np.int16))
            for f in resampler.resample(None):
                out.append(f.to_ndarray().reshape(-1).astype(np.int16))
        return np.concatenate(out) if out else None
    except Exception as exc:  # codec missing or PyAV API differences
        log.warning("Opus round trip unavailable (%s); using clean audio", exc)
        return None


@dataclass(frozen=True, slots=True)
class Segment:
    start: int  # samples
    end: int

    def seconds(self, rate: int = RATE) -> float:
        return (self.end - self.start) / rate


def frame_db(pcm: Pcm, rate: int = RATE) -> npt.NDArray[np.float64]:
    """Loudness of each 20 ms frame, in dB relative to full scale."""
    step = rate * FRAME_MS // 1000
    n = len(pcm) // step
    if n == 0:
        return np.zeros(0)
    frames = pcm[: n * step].astype(np.float64).reshape(n, step) / 32768.0
    rms = np.sqrt(np.mean(frames**2, axis=1))
    return 20 * np.log10(np.maximum(rms, 1e-9))


def split(
    pcm: Pcm,
    rate: int = RATE,
    min_silence_s: float = 1.2,
    min_speech_s: float = 0.25,
    pad_before_s: float = 0.2,
    pad_after_s: float = 0.3,
) -> list[Segment]:
    """Cut a recording at long pauses: one segment per spoken line."""
    db = frame_db(pcm, rate)
    if len(db) == 0:
        return []
    floor = float(np.percentile(db, 10))
    loud = float(np.percentile(db, 95))
    threshold = floor + max(6.0, 0.35 * (loud - floor))
    voiced = db > threshold
    frame_s = FRAME_MS / 1000
    gap_frames = int(min_silence_s / frame_s)
    segments: list[tuple[int, int]] = []  # frame ranges [start, end)
    start: int | None = None
    last_voiced = -1
    for i, v in enumerate(voiced):
        if v:
            if start is None:
                start = i
            elif i - last_voiced > gap_frames:
                segments.append((start, last_voiced + 1))
                start = i
            last_voiced = i
    if start is not None:
        segments.append((start, last_voiced + 1))
    step = rate * FRAME_MS // 1000
    out: list[Segment] = []
    for a, b in segments:
        if int(voiced[a:b].sum()) * frame_s < min_speech_s:
            continue  # a click or a bump, not a line
        s = max(0, a * step - int(pad_before_s * rate))
        e = min(len(pcm), b * step + int(pad_after_s * rate))
        out.append(Segment(s, e))
    return out


def write_wav(path: Path, pcm: Pcm, rate: int = RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.astype("<i2").tobytes())


def read_wav(path: Path) -> bytes:
    """Raw 16 kHz mono 16-bit PCM from a clip written by write_wav."""
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path} isn't 16 kHz mono 16-bit")
        return w.readframes(w.getnframes())
