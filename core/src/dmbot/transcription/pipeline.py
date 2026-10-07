"""Transcription pipeline: a bounded queue of utterances per Discord server, and a few
workers that take turns between servers (#173).

Kept free of Discord so every rule here is unit-testable:
- consent is checked before AND after transcription (a revoke mid-call discards the text)
- clips too short to matter are counted but not transcribed
- repeated engine failures raise one alert to the DM, and one when service recovers
- failures are logged with a throttle so a dead engine can't flood the logs
- every clip has a time budget, so one stuck clip can't hold up the ones behind it (#137)
- each server's clips are written one at a time and in order (lines never swap), while
  `workers` servers can be written for at once: one slow table can't hold up the others
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter, deque
from collections.abc import Awaitable, Callable
from typing import Protocol

from dmbot.audio.segmenter import Utterance
from dmbot.logs import log_context
from dmbot.transcription.base import MIN_UTTERANCE_S, Transcriber, TranscriptionProblem

log = logging.getLogger(__name__)

QUEUE_SIZE = 64  # per Discord server
# Across all servers: a clip is up to 480 KB of audio, so this bounds memory (~120 MB at
# worst) when the engine is down for every table at once.
MAX_QUEUED = 256
# Warn the DM if this many of a server's utterances are waiting: the engine can't keep up.
BACKLOG_WARN = 16
FAILURES_BEFORE_ALERT = 3
# "Working again" only once it's steady (#470): this many answers in a row, or one answer
# and this long without a failure, so a flapping engine doesn't churn the DM screen.
SUCCESSES_BEFORE_ALL_CLEAR = 3
ALL_CLEAR_AFTER_S = 30.0
LOG_EVERY_NTH_FAILURE = 50
# Time budget per clip: generous for real work, short enough that the table doesn't
# lose minutes of text to one stuck clip.
MIN_CLIP_BUDGET_S = 10.0
CLIP_BUDGET_PER_AUDIO_S = 3.0
# A clip slower than this (and slower than its own length) is logged as slow.
SLOW_CLIP_S = 5.0
DRAIN_POLL_S = 0.1
# Tell the DM about skipped clips at most this often.
SKIP_ALERT_EVERY_S = 600.0
SKIPPED_ALERT = (
    "⚠️ Writing things down: DMbot couldn't keep up and skipped a bit of what was said, "
    "so it won't be in the transcript. DMbot is still listening. If you see this often, "
    "whoever hosts DMbot can pick a faster setting."
)
SKIPPED_ALERT_OUTSIDE = (
    "⚠️ Writing things down: the speech-to-text company was slow, so DMbot skipped a bit "
    "of what was said; it won't be in the transcript. DMbot is still listening."
)


def clip_budget_s(duration_s: float) -> float:
    return max(MIN_CLIP_BUDGET_S, CLIP_BUDGET_PER_AUDIO_S * duration_s)


class ConsentChecker(Protocol):
    def has_consent(self, guild_id: int, user_id: int) -> bool: ...


Deliver = Callable[[Utterance, str | None], None]
Alert = Callable[[int, str], Awaitable[None]]
Hints = Callable[[Utterance], Awaitable[list[str]]]  # for the session that heard it
IsActive = Callable[[Utterance], bool]  # is the session that heard it still on?


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
        outside: bool = False,
        workers: int = 1,
        max_queued: int = MAX_QUEUED,
    ) -> None:
        # True when another company does the writing down (TRANSCRIBER=deepgram or cloud):
        # slowness is theirs, so alerts don't suggest a smaller Whisper model.
        self._outside = outside
        self.transcriber = transcriber
        self._consent = consent
        self._is_active = is_active
        self._hints = hints
        self._deliver = deliver
        self._alert = alert
        self._queue_size = queue_size
        self._max_queued = max_queued
        self._workers = max(1, workers)
        # Each server's clips in order, and whose turn it is: a server is in `_turns`
        # once when it has clips waiting and none being written, so workers go round
        # the servers and never write two of one server's clips at once.
        self._queues: dict[int, deque[tuple[float, Utterance]]] = {}
        self._turns: asyncio.Queue[int] = asyncio.Queue()
        self._writing: set[int] = set()
        self.dropped = 0
        self.consecutive_failures = 0
        self.total_failures = 0
        self.last_latency_s: float | None = None  # any server's last clip
        self.latency_of: dict[int, float] = {}  # per server, for its own status
        self._told_stopped: set[int] = set()  # servers told writing stopped
        self._alerts_lock = asyncio.Lock()  # "stopped" and "working again" never cross
        self._ok_streak = 0  # answers in a row since writing stopped
        self._ok_since: float | None = None  # when the first of them came
        self.skipped = 0  # clips that ran over their time budget
        self.slow = 0  # clips slower than SLOW_CLIP_S and their own length
        self._budget_s = budget_s
        self._last_skip_alert: dict[int, float] = {}  # per Discord server
        self._backlog_warned: set[int] = set()  # servers told they're falling behind
        # Per Discord server, for the end-of-session summary (#109).
        # Per session (Utterance.session), for the end-of-session summary (#109).
        self.missed_in: Counter[int] = Counter()  # skipped or dropped clips
        self.failed_in: Counter[int] = Counter()
        # Clips queued but not finished, per session, so a stopping session can wait for
        # its own last words (not other servers' backlog).
        self.pending: Counter[int] = Counter()
        self._running = False  # workers are taking clips off the queues
        self._stop_waiting = False  # shutting down: nobody waits for the queue any more

    @property
    def backlog(self) -> int:
        """Clips waiting, across all servers."""
        return sum(len(q) for q in self._queues.values())

    def backlog_of(self, guild_id: int) -> int:
        queue = self._queues.get(guild_id)
        return len(queue) if queue else 0

    def enqueue(self, utterance: Utterance) -> bool:
        """Queue an utterance. Returns False (and counts a drop) if its server's queue is
        full: one busy table never pushes out another's speech."""
        guild = utterance.guild_id
        queue = self._queues.setdefault(guild, deque())
        if len(queue) >= self._queue_size or self.backlog >= self._max_queued:
            self.dropped += 1
            self.missed_in[utterance.session] += 1
            return False
        queue.append((time.monotonic(), utterance))
        self.pending[utterance.session] += 1
        if len(queue) == 1 and guild not in self._writing:
            self._turns.put_nowait(guild)  # nothing of this server waiting or being written
        return True

    async def drain(self, session: int, timeout_s: float) -> bool:
        """Wait until this session's queued clips are finished, or `timeout_s` passes,
        or DMbot is shutting down. True if it caught up. With no worker running nothing
        would finish, so it doesn't wait."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while self.pending[session] > 0:
            if not self._running:
                log.warning("Not waiting for the last words: transcription isn't running")
                return False
            if self._stop_waiting or loop.time() >= deadline:
                return False
            await asyncio.sleep(DRAIN_POLL_S)
        self.pending.pop(session, None)
        return True

    def stop_waiting(self) -> None:
        """Shutting down: every `drain` returns now."""
        self._stop_waiting = True

    def _allowed(self, utterance: Utterance) -> bool:
        return self._is_active(utterance) and self._consent.has_consent(
            utterance.guild_id, utterance.user_id
        )

    async def run(self) -> None:
        self._running = True
        try:
            results = await asyncio.gather(
                *(self._work() for _ in range(self._workers)), return_exceptions=True
            )
            for result in results:  # a worker only ends by a bug: never silently
                if isinstance(result, BaseException):
                    log.error("A transcription worker stopped: %r", result)
        finally:
            self._running = False

    async def _work(self) -> None:
        """One worker, for good: a bug in one clip is logged and the next clip taken."""
        while True:
            try:
                await self._take_turn()
            except Exception:
                log.exception("A transcription worker hit a problem; carrying on")

    async def _take_turn(self) -> None:
        guild = await self._turns.get()
        queue = self._queues[guild]
        if queue:
            queued_at, utterance = queue.popleft()
            self._writing.add(guild)
            try:
                with log_context(guild_id=guild):
                    await self._check_backlog(guild)
                    await self.process(utterance)
            except Exception:
                # Never let one clip stop a worker: the others would go on unseen.
                log.exception("Writing down a clip failed unexpectedly")
            finally:
                self.pending[utterance.session] -= 1
                self._writing.discard(guild)
                if queue:
                    self._turns.put_nowait(guild)  # back of the line: others go first
                else:
                    del self._queues[guild]
                    self._backlog_warned.discard(guild)
            self.last_latency_s = self.latency_of[guild] = time.monotonic() - queued_at

    def session_ended(self, guild_id: int) -> None:
        """A server's session ended: a new one there during a long outage is told again."""
        self._told_stopped.discard(guild_id)

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
                hints = await self._hints(utterance)
                started = time.monotonic()
                timer.reschedule(asyncio.get_running_loop().time() + budget)
                text = await self.transcriber.transcribe(utterance, hints)
        except TimeoutError as exc:
            if timer.expired():
                await self._on_skip(utterance, budget)
            else:
                await self._on_failure(utterance, exc)
        except Exception as exc:
            await self._on_failure(utterance, exc)
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
        self.missed_in[utterance.session] += 1
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
            await self._alert(
                utterance.guild_id, SKIPPED_ALERT_OUTSIDE if self._outside else SKIPPED_ALERT
            )

    async def _on_failure(self, utterance: Utterance, exc: Exception) -> None:
        guild_id = utterance.guild_id
        self.consecutive_failures += 1
        self.total_failures += 1
        self._ok_streak, self._ok_since = 0, None
        self.failed_in[utterance.session] += 1
        if self.total_failures == 1 or self.total_failures % LOG_EVERY_NTH_FAILURE == 0:
            # The details are for whoever hosts DMbot: the log, never Discord (#99).
            log.error(
                "Transcription failed (%d so far): %s: %s",
                self.total_failures,
                type(exc).__name__,
                exc,
            )
        # Every server with speech waiting is told once (the engine is shared), not just
        # the one whose clip failed third.
        if self.consecutive_failures < FAILURES_BEFORE_ALERT:
            return
        async with self._alerts_lock:
            busy = {guild_id, *self._writing, *self._queues} - self._told_stopped
            if busy:
                await self._tell_stopped(busy, exc)

    async def _tell_stopped(self, busy: set[int], exc: Exception) -> None:
        """Tell these servers writing stopped (under the alerts lock)."""
        if isinstance(exc, TranscriptionProblem):
            # Our own plain sentence about the problem (never raw error text).
            advice = (
                "Whoever hosts DMbot needs to check its speech-to-text settings, then restart it."
                if exc.host_can_fix
                else "This is on the speech-to-text company's side. You don't need to do "
                "anything: DMbot keeps trying and tells you when it works again."
            )
            text = (
                f"⚠️ **No transcript right now:** {exc.for_dm}. DMbot still hears "
                f"everyone who said yes, but writes nothing down. {advice}"
            )
        else:
            text = (
                "⚠️ **Writing things down stopped working.** DMbot still hears everyone "
                "who said yes, but no words are being written down. Whoever hosts "
                "DMbot should check its log. DMbot keeps trying."
            )
        for guild in sorted(busy):
            await self._alert(guild, text)
        self._told_stopped |= busy  # after sending: "working again" never comes first

    async def _on_success(self, guild_id: int) -> None:
        self.consecutive_failures = 0
        if not self._told_stopped:
            return
        now = time.monotonic()
        self._ok_streak += 1
        if self._ok_since is None:
            self._ok_since = now
        steady = (
            self._ok_streak >= SUCCESSES_BEFORE_ALL_CLEAR
            or now - self._ok_since >= ALL_CLEAR_AFTER_S
        )
        if not steady:
            return
        async with self._alerts_lock:
            told, self._told_stopped = self._told_stopped, set()
            self._ok_streak, self._ok_since = 0, None
            for guild in sorted(told):
                await self._alert(guild, "✅ Writing things down is working again.")

    async def _check_backlog(self, guild_id: int) -> None:
        depth = self.backlog_of(guild_id)
        if depth >= BACKLOG_WARN and guild_id not in self._backlog_warned:
            self._backlog_warned.add(guild_id)
            log.warning(
                "Transcription backlog: %d utterances waiting%s",
                depth,
                "" if self._outside else " (try a smaller WHISPER_MODEL or TRANSCRIBER=deepgram)",
            )
            await self._alert(
                guild_id,
                f"🐢 **Writing things down is falling behind** ({depth} bits of speech "
                "waiting), so words will show up late. "
                + (
                    "The speech-to-text company is slow right now; DMbot will catch up."
                    if self._outside
                    else "Whoever hosts DMbot can switch to a faster setting."
                ),
            )
        elif depth < BACKLOG_WARN // 2:
            self._backlog_warned.discard(guild_id)
