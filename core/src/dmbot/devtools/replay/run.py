"""Plays a recording through core's real speech path, with no Discord and no ears:
pieces of speech go frame by frame into the Segmenter, then through the
TranscriptionPipeline and the configured transcriber, exactly as live audio would.

Capture is what's bypassed, not the checks: the pipeline still checks consent before
and after writing each piece down. The twin's consent says yes only for its own made-up
speaker in its own made-up server, and never reads or writes the real consent store.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from dmbot.audio.segmenter import Segmenter, Utterance
from dmbot.devtools.replay.audio import FRAME_MS, SPEECH_END_MS, Piece
from dmbot.ears.protocol import AudioFrame
from dmbot.transcription.base import MIN_UTTERANCE_S, Transcriber
from dmbot.transcription.pipeline import QUEUE_SIZE, TranscriptionPipeline

# Made-up IDs: no real Discord server or person has these.
TWIN_GUILD = 1
TWIN_SPEAKER = 1001
DRAIN_TIMEOUT_S = 600.0
# Live, core hears a piece has ended when ears says so: the client's hangover after the
# last loud frame, then ears' 800 ms with no packets. The same quiet that ends a piece.
END_DELAY_MS = SPEECH_END_MS


class TwinConsent:
    """Yes for the twin's own speaker only. Never touches the real consent store."""

    def has_consent(self, guild_id: int, user_id: int) -> bool:
        return guild_id == TWIN_GUILD and user_id == TWIN_SPEAKER


@dataclass(frozen=True, slots=True)
class Heard:
    start_ms: int
    end_ms: int
    text: str | None
    wait_s: float  # from when core would hear the piece ended to its text
    seconds: float  # of audio sent (quiet left out inside the piece isn't)

    @property
    def written_down(self) -> bool:
        """Long enough to go to speech-to-text (shorter ones are counted, not sent)."""
        return self.seconds >= MIN_UTTERANCE_S


@dataclass(slots=True)
class Replay:
    heard: list[Heard] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)  # what the DM screen would show
    failed: int = 0
    skipped: int = 0
    dropped: int = 0
    finished: bool = True  # every piece was written down before the time limit
    realtime: bool = False
    hints: int = 0
    took_s: float = 0.0

    @property
    def speech_s(self) -> float:
        return sum(h.seconds for h in self.heard)

    @property
    def sent_s(self) -> float:
        """Speech the engine was sent, which is what an outside engine charges for (#523):
        pieces too short to write down are counted in `speech_s` but never sent. A piece
        the engine failed on still counts (see `failed`), and a retry counts once."""
        return sum(h.seconds for h in self.heard if h.written_down)


async def replay(
    pieces: Sequence[Piece],
    transcriber: Transcriber,
    *,
    hints: Sequence[str] = (),
    realtime: bool = False,
    outside: bool = False,
    end_delay_ms: int = END_DELAY_MS,
) -> Replay:
    """Write the pieces down. `realtime` sends each frame at its time and ends each piece
    when ears would, so waits mean what they mean live; otherwise everything is queued
    as soon as there's room, and only the scores mean anything."""
    result = Replay(realtime=realtime, hints=len(hints))
    segmenter = Segmenter(TWIN_GUILD)
    queued_at: dict[int, float] = {}

    def deliver(utterance: Utterance, text: str | None) -> None:
        wait = time.monotonic() - queued_at.get(utterance.start_ms, time.monotonic())
        # The Segmenter's end is the last frame's start; the piece runs a frame longer.
        end = utterance.end_ms + FRAME_MS
        result.heard.append(Heard(utterance.start_ms, end, text, wait, utterance.duration_s))

    async def alert(guild_id: int, text: str) -> None:
        result.alerts.append(text)

    async def name_hints(utterance: Utterance) -> list[str]:
        return list(hints)

    pipeline = TranscriptionPipeline(
        transcriber,
        TwinConsent(),
        is_active=lambda utterance: utterance.session == segmenter.session,
        hints=name_hints,
        deliver=deliver,
        alert=alert,
        outside=outside,
    )

    async def enqueue(utterance: Utterance) -> None:
        if pipeline.backlog >= QUEUE_SIZE:  # a replay waits; live speech would be lost
            await pipeline.drain(segmenter.session, DRAIN_TIMEOUT_S)
        queued_at[utterance.start_ms] = time.monotonic()
        pipeline.enqueue(utterance)

    async def until(at_ms: int) -> None:
        if realtime:
            await asyncio.sleep(max(0.0, at_ms / 1000 - (time.monotonic() - started)))

    worker: asyncio.Task[None] | None = None
    started = time.monotonic()
    try:
        await transcriber.warm_up()
        worker = asyncio.create_task(pipeline.run(), name="replay-transcribe")
        await asyncio.sleep(0)  # let the worker start, or drain() won't wait for it
        started = time.monotonic()
        for piece in pieces:
            for at_ms, pcm in piece.frames:
                await until(at_ms)
                if full := segmenter.add(AudioFrame(TWIN_GUILD, TWIN_SPEAKER, at_ms, pcm)):
                    await enqueue(full)  # the 15 s cut
            await until(piece.end_ms + end_delay_ms)
            if (utterance := segmenter.end(TWIN_SPEAKER)) is not None:
                await enqueue(utterance)
        result.finished = await pipeline.drain(segmenter.session, DRAIN_TIMEOUT_S)
    finally:
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        await transcriber.close()
    assert worker is not None  # warm_up() didn't raise
    if worker.done() and not worker.cancelled() and (crash := worker.exception()) is not None:
        raise crash  # the pipeline itself broke: don't score it as a bad transcript
    result.took_s = time.monotonic() - started
    result.failed = pipeline.total_failures
    result.skipped = pipeline.skipped
    result.dropped = pipeline.dropped
    result.heard.sort(key=lambda h: h.start_ms)
    return result
