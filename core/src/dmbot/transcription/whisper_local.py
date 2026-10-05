"""Local Whisper transcription using faster-whisper (CTranslate2).

Runs on this machine's CPU or GPU — free and private, but needs a reasonably strong
machine for live play. Install with ``pip install -e ".[whisper]"``.

Model guide (WHISPER_MODEL):
- CPU-only server: ``base`` or ``small`` (WHISPER_COMPUTE_TYPE=auto picks int8)
- NVIDIA GPU: ``large-v3`` or ``turbo`` (auto picks float16)

Shutdown note: a transcription already running in its worker thread can't be
interrupted, so stopping the bot may wait for the current clip (at most a few seconds).

Time budget (#137): the pipeline skips a clip that runs over its budget, but the worker
thread can't be stopped and finishes in the background (its text is thrown away, even
if the speaker withdrew consent meanwhile). That's why each clip's decoding is bounded
here (one pass, output length fitted to the clip): the budget is a safety net, not the
fix. Whisper runs on its own single worker thread, so a skipped clip still running can't
overlap the next one or fill the thread pool other work shares; the next clip waits
for it instead, which the bounds keep short.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, TypeVar

from dmbot.audio.segmenter import Utterance
from dmbot.ears.protocol import SAMPLE_RATE
from dmbot.transcription.base import TranscriberUnavailable, build_prompt, clean_text
from dmbot.transcription.config import TranscriptionSettings

log = logging.getLogger(__name__)
T = TypeVar("T")

# Whisper invents text ("Thanks for watching!") on noise. Drop segments it is unsure about.
NO_SPEECH_MAX = 0.6
AVG_LOGPROB_MIN = -1.0
# With one temperature, faster-whisper can't retry a looping decode, so drop text that
# repeats itself (Whisper's own threshold for "too repetitive").
COMPRESSION_RATIO_MAX = 2.4

# Bounds on the work for one clip (#137). faster-whisper's defaults re-decode a clip up
# to 6 times at rising temperatures and let each try run to ~448 tokens; on a short
# clip, where Whisper tends to loop on invented text, that took 40 s on the test
# server's CPU (the limit is 448 tokens minus the prompt). Live play needs one pass
# whose length fits the clip.
TEMPERATURE = 0.0  # one greedy pass, no retries
TOKENS_PER_SECOND = 12  # speech is about 3-4 tokens a second: generous, never cuts real speech
MIN_NEW_TOKENS = 24
# Whisper's limit is 448 tokens for prompt plus output, and faster-whisper raises an
# error past it. The prompt can take up to 227 (223 of names plus 4 control tokens).
WHISPER_MAX_LENGTH = 448
MAX_PROMPT_TOKENS = 227
MAX_NEW_TOKENS = 200


def max_new_tokens(duration_s: float) -> int:
    """How many tokens Whisper may write for a clip of this length."""
    wanted = max(MIN_NEW_TOKENS, math.ceil(TOKENS_PER_SECOND * duration_s))
    return min(MAX_NEW_TOKENS, wanted)


@dataclass(frozen=True, slots=True)
class SegmentScore:
    text: str
    no_speech_prob: float
    avg_logprob: float
    compression_ratio: float = 1.0


def keep_segment(segment: SegmentScore) -> bool:
    """True if a Whisper segment is likely real speech rather than a hallucination."""
    if segment.compression_ratio > COMPRESSION_RATIO_MAX:
        return False
    return not (segment.no_speech_prob > NO_SPEECH_MAX and segment.avg_logprob < AVG_LOGPROB_MIN)


class LocalWhisperTranscriber:
    def __init__(self, settings: TranscriptionSettings) -> None:
        self._settings = settings
        self._model: Any = None
        # One utterance at a time: keeps memory flat and latency predictable. The lock
        # orders callers; the single-thread executor keeps a skipped clip that is still
        # decoding from overlapping the next one (the lock is released on cancel).
        self._lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")

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
                self._model = await self._in_worker(self._load)

    async def transcribe(self, utterance: Utterance, hints: list[str]) -> str | None:
        async with self._lock:
            if self._model is None:
                self._model = await self._in_worker(self._load)
            return await self._in_worker(self._run, utterance.pcm, build_prompt(hints))

    async def _in_worker(self, fn: Callable[..., T], *args: Any) -> T:
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    def _run(self, pcm: bytes, prompt: str | None) -> str | None:
        import numpy as np

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        model = self._model  # close() may clear it while a skipped clip finishes
        segments, _info = model.transcribe(
            audio,
            language=self._settings.language or None,
            initial_prompt=prompt,
            vad_filter=True,
            beam_size=self._settings.whisper_beam_size,
            condition_on_previous_text=False,
            temperature=TEMPERATURE,
            max_new_tokens=max_new_tokens(len(audio) / SAMPLE_RATE),
        )
        kept = [
            s.text
            for s in segments
            if keep_segment(
                SegmentScore(s.text, s.no_speech_prob, s.avg_logprob, s.compression_ratio)
            )
        ]
        return clean_text(" ".join(kept))

    async def close(self) -> None:
        # Don't wait for a skipped clip still decoding; drop anything not yet started.
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._model = None
