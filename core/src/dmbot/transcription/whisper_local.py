"""Local Whisper transcription using faster-whisper (CTranslate2).

Runs on this machine's CPU or GPU — free and private, but needs a reasonably strong
machine for live play. Install with ``pip install -e ".[whisper]"``.

Model guide (WHISPER_MODEL):
- CPU-only server: ``base`` or ``small`` (WHISPER_COMPUTE_TYPE=auto picks int8)
- NVIDIA GPU: ``large-v3`` or ``turbo`` (auto picks float16)

Shutdown note: a transcription already running in its worker thread can't be
interrupted, so stopping the bot may wait for the current clip (at most a few seconds).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from dmbot.audio.segmenter import Utterance
from dmbot.transcription.base import TranscriberUnavailable, build_prompt, clean_text
from dmbot.transcription.config import TranscriptionSettings

log = logging.getLogger(__name__)

# Whisper invents text ("Thanks for watching!") on noise. Drop segments it is unsure about.
NO_SPEECH_MAX = 0.6
AVG_LOGPROB_MIN = -1.0


@dataclass(frozen=True, slots=True)
class SegmentScore:
    text: str
    no_speech_prob: float
    avg_logprob: float


def keep_segment(segment: SegmentScore) -> bool:
    """True if a Whisper segment is likely real speech rather than a hallucination."""
    return not (segment.no_speech_prob > NO_SPEECH_MAX and segment.avg_logprob < AVG_LOGPROB_MIN)


class LocalWhisperTranscriber:
    def __init__(self, settings: TranscriptionSettings) -> None:
        self._settings = settings
        self._model: Any = None
        # One utterance at a time: keeps memory flat and latency predictable.
        self._lock = asyncio.Lock()

    def _load(self) -> Any:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriberUnavailable(
                'Local Whisper is not installed. Run: pip install -e ".[whisper]" '
                "(or set TRANSCRIBER=cloud to use a pay-as-you-go service)."
            ) from exc
        s = self._settings
        log.info(
            "Loading Whisper model %r on %s (%s)…",
            s.whisper_model,
            s.whisper_device,
            s.whisper_compute_type,
        )
        return WhisperModel(
            s.whisper_model, device=s.whisper_device, compute_type=s.whisper_compute_type
        )

    async def warm_up(self) -> None:
        """Load the model before the session so the first utterance isn't slow."""
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load)

    async def transcribe(self, utterance: Utterance, hints: list[str]) -> str | None:
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load)
            return await asyncio.to_thread(self._run, utterance.pcm, build_prompt(hints))

    def _run(self, pcm: bytes, prompt: str | None) -> str | None:
        import numpy as np

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = self._model.transcribe(
            audio,
            language=self._settings.language or None,
            initial_prompt=prompt,
            vad_filter=True,
            beam_size=self._settings.whisper_beam_size,
            condition_on_previous_text=False,
        )
        kept = [
            s.text
            for s in segments
            if keep_segment(SegmentScore(s.text, s.no_speech_prob, s.avg_logprob))
        ]
        return clean_text(" ".join(kept))

    async def close(self) -> None:
        self._model = None
