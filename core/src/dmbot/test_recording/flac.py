"""One utterance as a FLAC file and back (#1019). 16 kHz mono 16-bit, what core hears."""

from __future__ import annotations

import io

import av

from dmbot.ears.protocol import BYTES_PER_SAMPLE, SAMPLE_RATE


def encode(pcm: bytes) -> bytes:
    """The audio as a FLAC file. Lossless: `decode(encode(x)) == x`."""
    if not pcm or len(pcm) % BYTES_PER_SAMPLE:
        raise ValueError("not whole samples of audio")
    buf = io.BytesIO()
    with av.open(buf, "w", format="flac") as out:
        stream = out.add_stream("flac", rate=SAMPLE_RATE)
        stream.layout = "mono"
        frame = av.AudioFrame(format="s16", layout="mono", samples=len(pcm) // BYTES_PER_SAMPLE)
        frame.sample_rate = SAMPLE_RATE
        frame.planes[0].update(pcm)
        for packet in stream.encode(frame):
            out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


def decode(data: bytes) -> bytes:
    """A FLAC file made by `encode`, as 16 kHz mono 16-bit audio."""
    chunks: list[bytes] = []
    with av.open(io.BytesIO(data), "r") as container:
        for frame in container.decode(audio=0):
            chunks.append(bytes(frame.planes[0])[: frame.samples * BYTES_PER_SAMPLE])
    return b"".join(chunks)
