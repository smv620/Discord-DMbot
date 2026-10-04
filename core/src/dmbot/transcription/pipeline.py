"""Transcription pipeline: a bounded queue of utterances and one worker.

Kept free of Discord so every rule here is unit-testable:
- consent is checked before AND after transcription (a revoke mid-call discards the text)
- clips too short to matter are counted but not transcribed
- repeated engine failures raise one alert to the DM, and one when service recovers
- failures are logged with a throttle so a dead engine can't flood the logs
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

        text: str | None
        try:
            text = await self.transcriber.transcribe(
                utterance, await self._hints(utterance.guild_id)
            )
        except Exception as exc:
            text = None
            await self._on_failure(utterance.guild_id, exc)
        else:
            await self._on_success(utterance.guild_id)

        # Re-check: transcription can take seconds, and consent may have been revoked.
        if self._allowed(utterance):
            self._deliver(utterance, text)

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
