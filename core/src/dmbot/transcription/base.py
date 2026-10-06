"""Transcription engine interface and shared helpers.

Every engine implements ``Transcriber``. The bot only ever talks to this interface, so
switching between local Whisper and a cloud API is a configuration change
(``TRANSCRIBER=...``), not a code change.
"""

from __future__ import annotations

import io
import wave
from typing import Protocol

from dmbot.audio.segmenter import Utterance
from dmbot.ears.protocol import BYTES_PER_SAMPLE, SAMPLE_RATE

# Whisper's prompt window is ~224 tokens; keep hints well inside it.
MAX_HINT_CHARS = 600
# Shorter clips are almost always coughs, clicks or "uh" — not worth transcribing.
MIN_UTTERANCE_S = 0.25


class TranscriberUnavailable(RuntimeError):
    """The configured engine cannot start (missing package, bad settings)."""


class TranscriptionProblem(RuntimeError):
    """A failure whose message is plain words, safe to show the DM and to log: no keys,
    no players' names, no reply bodies.

    `host_can_fix`: the cause is in the host's settings (a wrong key, no credit), not an
    outage on the company's side, so the DM screen tells the host where to look.
    """

    def __init__(self, message: str, *, host_can_fix: bool = False) -> None:
        super().__init__(message)
        self.host_can_fix = host_can_fix


class Transcriber(Protocol):
    async def warm_up(self) -> None:
        """Prepare before the session (load models). Raise TranscriberUnavailable if unusable."""
        ...

    async def transcribe(self, utterance: Utterance, hints: list[str]) -> str | None:
        """Return the text spoken in the utterance, or None if nothing usable.

        `hints` are names (characters, NPCs, places) to bias recognition toward.
        """
        ...

    async def close(self) -> None:
        """Release models, sessions, or connections."""
        ...


class PlaceholderTranscriber:
    """Returns no text. Used with TRANSCRIBER=none (no text; only counts in the log)."""

    async def warm_up(self) -> None:
        return None

    async def transcribe(self, utterance: Utterance, hints: list[str]) -> str | None:
        return None

    async def close(self) -> None:
        return None


def build_prompt(hints: list[str]) -> str | None:
    """Turn name hints into a Whisper initial prompt, de-duplicated and length-capped."""
    seen: set[str] = set()
    names: list[str] = []
    length = 0
    for raw in hints:
        name = " ".join(raw.split())
        key = name.casefold()
        if not name or key in seen:
            continue
        if length + len(name) + 2 > MAX_HINT_CHARS:
            break
        seen.add(key)
        names.append(name)
        length += len(name) + 2
    return f"Names: {', '.join(names)}." if names else None


def to_wav(pcm: bytes) -> bytes:
    """Wrap 16 kHz mono s16le PCM in a WAV container (what cloud APIs expect)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(BYTES_PER_SAMPLE)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def clean_text(text: str) -> str | None:
    """Normalise whitespace; drop empty results."""
    cleaned = " ".join(text.split())
    return cleaned or None
