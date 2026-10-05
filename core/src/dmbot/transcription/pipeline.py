"""Transcription pipeline: a bounded queue of utterances and one worker.

Kept free of Discord so every rule here is unit-testable:
- consent is checked before AND after transcription (a revoke mid-call discards the text)
- clips too short to matter are counted but not transcribed
- repeated engine failures raise one alert to the DM, and one when service recovers
- failures are logged with a throttle so a dead engine can't flood the logs
- every clip has a time budget, so one stuck clip can't hold up the ones behind it (#137)
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

from dmbot.audio.segmenter import Utterance
from dmbot.logs import log_context
from dmbot.transcription.base import MIN_UTTERANCE_S, Transcriber

log = logging.getLogger(__name__)

QUEUE_SIZE = 64
# Warn the DM if this many utterances are waiting: the engine can't keep up.
BACKLOG_WARN = 16
FAILURES_BEFORE_ALERT = 3
LOG_EVERY_NTH_FAILURE = 50
# Time budget per clip: generous for real work, short enough that the table doesn't
# lose minutes of text to one stuck clip.
MIN_CLIP_BUDGET_S = 10.0
CLIP_BUDGET_PER_AUDIO_S = 3.0
# A clip slower than this (and slower than its own length) is logged as slow.
SLOW_CLIP_S = 5.0
# Tell the DM about skipped clips at most this often.
SKIP_ALERT_EVERY_S = 600.0
SKIPPED_ALERT = (
    "⚠️ Writing things down: DMbot couldn't keep up and skipped a bit of what was said, "
    "so it won't be in the transcript. DMbot is still listening. If you see this often, "
    "whoever hosts DMbot can pick a faster setting."
)


def clip_budget_s(duration_s: float) -> float:
    return max(MIN_CLIP_BUDGET_S, CLIP_BUDGET_PER_AUDIO_S * duration_s)


class ConsentChecker(Protocol):
    def has_consent(self, guild_id: int, user_id: int) -> bool: ...


Deliver = Callable[[Utterance, str | None], None]
Alert = Callable[[int, str], Awaitable[None]]
Hints = Callable[[int], Awaitable[list[str]]]
IsActive = Callable[[int], bool]


class TranscriptionPipeline:
    def __init__(
        self,
        transcriber: Transcriber,
        consent: ConsentChecker,
        *,
        is_active: IsActive,
        hints: Hints,
        deliver: Deliver,
        alert: Alert,
        queue_size: int = QUEUE_SIZE,
        budget_s: Callable[[float], float] = clip_budget_s,
    ) -> None:
        self.transcriber = transcriber
        self._consent = consent
        self._is_active = is_active
        self._hints = hints
        self._deliver = deliver
        self._alert = alert
        self._queue: asyncio.Queue[tuple[float, Utterance]] = asyncio.Queue(queue_size)
        self.dropped = 0
        self.consecutive_failures = 0
        self.total_failures = 0
        self.last_latency_s: float | None = None
        self.skipped = 0  # clips that ran over their time budget
        self.slow = 0  # clips slower than SLOW_CLIP_S and their own length
        self._budget_s = budget_s
        self._last_skip_alert: dict[int, float] = {}  # per Discord server
        self._backlog_warned = False

    @property
    def backlog(self) -> int:
        return self._queue.qsize()

    def enqueue(self, utterance: Utterance) -> bool:
        """Queue an utterance. Returns False (and counts a drop) if the queue is full."""
        try:
            self._queue.put_nowait((time.monotonic(), utterance))
        except asyncio.QueueFull:
            self.dropped += 1
            return False
        return True

    def _allowed(self, utterance: Utterance) -> bool:
        return self._is_active(utterance.guild_id) and self._consent.has_consent(
            utterance.guild_id, utterance.user_id
        )

    async def run(self) -> None:
        while True:
            queued_at, utterance = await self._queue.get()
            with log_context(guild_id=utterance.guild_id):
                await self._check_backlog(utterance.guild_id)
                await self.process(utterance)
            self.last_latency_s = time.monotonic() - queued_at

    async def process(self, utterance: Utterance) -> None:
        # Skip if the table ended or the speaker revoked consent while queued.
        if not self._allowed(utterance):
            return
        if utterance.duration_s < MIN_UTTERANCE_S:
            self._deliver(utterance, None)
            return

        text: str | None = None
        budget = self._budget_s(utterance.duration_s)
        # asyncio.timeout, not wait_for: an engine's own TimeoutError (a cloud request
        # timing out) is a failure, not a skipped clip. Rescheduled once hints are in.
        timer = asyncio.timeout(None)
        started = time.monotonic()
        try:
            async with timer:
                hints = await self._hints(utterance.guild_id)
                started = time.monotonic()
                timer.reschedule(asyncio.get_running_loop().time() + budget)
                text = await self.transcriber.transcribe(utterance, hints)
        except TimeoutError as exc:
            if timer.expired():
                await self._on_skip(utterance, budget)
            else:
                await self._on_failure(utterance.guild_id, exc)
        except Exception as exc:
            await self._on_failure(utterance.guild_id, exc)
        else:
            await self._on_success(utterance.guild_id)
            took = time.monotonic() - started
            if took > max(SLOW_CLIP_S, utterance.duration_s):
                self.slow += 1
                level = (
                    logging.WARNING
                    if self.slow == 1 or self.slow % LOG_EVERY_NTH_FAILURE == 0
                    else logging.DEBUG
                )
                log.log(
                    level,
                    "Slow transcription: %.1f s clip took %.1f s (%d slow so far)",
                    utterance.duration_s,
                    took,
                    self.slow,
                )
            else:
                log.debug("Transcribed %.1f s clip in %.2f s", utterance.duration_s, took)

        # Re-check: transcription can take seconds, and consent may have been revoked.
        if self._allowed(utterance):
            self._deliver(utterance, text)

    async def _on_skip(self, utterance: Utterance, budget: float) -> None:
        """A clip ran over its budget: move on, and tell the DM (not too often).

        The audio still counts in the capture check; only its text is missing.
        """
        self.skipped += 1
        log.warning(
            "Transcription skipped: %.1f s clip ran over its %.0f s budget (%d skipped so far)",
            utterance.duration_s,
            budget,
            self.skipped,
        )
        now = time.monotonic()
        last = self._last_skip_alert.get(utterance.guild_id)
        if last is None or now - last >= SKIP_ALERT_EVERY_S:
            self._last_skip_alert[utterance.guild_id] = now
            await self._alert(utterance.guild_id, SKIPPED_ALERT)

    async def _on_failure(self, guild_id: int, exc: Exception) -> None:
        self.consecutive_failures += 1
        self.total_failures += 1
        if self.total_failures == 1 or self.total_failures % LOG_EVERY_NTH_FAILURE == 0:
            log.error("Transcription failed (%d so far): %s", self.total_failures, exc)
        if self.consecutive_failures == FAILURES_BEFORE_ALERT:
            await self._alert(
                guild_id,
                f"⚠️ Transcription isn't working ({type(exc).__name__}: {exc}). "
                "Capture continues without text. Check the server log and your "
                "TRANSCRIBER settings.",
            )

    async def _on_success(self, guild_id: int) -> None:
        if self.consecutive_failures >= FAILURES_BEFORE_ALERT:
            await self._alert(guild_id, "✅ Transcription is working again.")
        self.consecutive_failures = 0

    async def _check_backlog(self, guild_id: int) -> None:
        depth = self.backlog
        if depth >= BACKLOG_WARN and not self._backlog_warned:
            self._backlog_warned = True
            log.warning("Transcription backlog: %d utterances waiting", depth)
            await self._alert(
                guild_id,
                f"🐢 Transcription is falling behind ({depth} clips waiting). "
                "Try a smaller WHISPER_MODEL or TRANSCRIBER=cloud.",
            )
        elif depth < BACKLOG_WARN // 2:
            self._backlog_warned = False
