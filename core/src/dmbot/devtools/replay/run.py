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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal, NamedTuple

from dmbot.audio.segmenter import Segmenter, Utterance
from dmbot.devtools.replay.audio import FRAME_MS, SPEECH_END_MS, Piece

# Re-exported: the twin's made-up speakers live with the audio they say.
from dmbot.devtools.replay.audio import TWIN_PLAYER as TWIN_PLAYER
from dmbot.devtools.replay.audio import TWIN_SPEAKER as TWIN_SPEAKER
from dmbot.ears.protocol import AudioFrame
from dmbot.transcription.base import MIN_UTTERANCE_S, Transcriber
from dmbot.transcription.pipeline import QUEUE_SIZE, TranscriptionPipeline

# A made-up server: no real Discord server has this ID.
TWIN_GUILD = 1
DRAIN_TIMEOUT_S = 600.0
# Live, core hears a piece has ended when ears says so: the client's hangover after the
# last loud frame, then ears' 800 ms with no packets. The same quiet that ends a piece.
END_DELAY_MS = SPEECH_END_MS


class TwinConsent:
    """Yes for the twin's own speakers only, and only while they agree. Never touches the
    real consent store."""

    def __init__(self, speakers: Iterable[int] = (TWIN_SPEAKER,)) -> None:
        self.agreed = set(speakers)

    def has_consent(self, guild_id: int, user_id: int) -> bool:
        return guild_id == TWIN_GUILD and user_id in self.agreed


@dataclass(frozen=True, slots=True)
class ConsentChange:
    """A speaker stops being recorded (Stop recording me), or says yes for the first
    time (the first-time question), at a moment in the replay (#534)."""

    at_ms: int
    speaker: int
    agrees: bool


@dataclass(frozen=True, slots=True)
class Heard:
    start_ms: int
    end_ms: int
    text: str | None
    wait_s: float  # from when core would hear the piece ended to its text
    seconds: float  # of audio sent (quiet left out inside the piece isn't)
    speaker: int = TWIN_SPEAKER

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
    changes: tuple[ConsentChange, ...] = ()

    @property
    def speech_s(self) -> float:
        return sum(h.seconds for h in self.heard)

    @property
    def sent_s(self) -> float:
        """Speech the engine was sent, which is what an outside engine charges for (#523):
        pieces too short to write down are counted in `speech_s` but never sent. A piece
        the engine failed on still counts (see `failed`), and a retry counts once."""
        return sum(h.seconds for h in self.heard if h.written_down)


class _Event(NamedTuple):
    when_ms: int  # when it's played: never before the speaker's previous event
    kind: Literal["consent", "frame", "end"]  # "end": a piece ends
    order: int  # keeps each speaker's own events in order
    speaker: int
    at_ms: int = 0  # a frame's own time, as ears stamps it
    pcm: bytes = b""
    agrees: bool = False


def _timeline(
    pieces: Sequence[Piece], changes: Sequence[ConsentChange], end_delay_ms: int
) -> list[_Event]:
    """Everything that happens, in time order. Each speaker's own events keep their
    order, so a piece's lead-in never joins the piece before it; at the same moment a
    consent change comes first (stopped at 0:25 sends nothing at 0:25)."""
    events: list[_Event] = []
    last: dict[int, int] = {}
    for piece in pieces:
        for at_ms, pcm in piece.frames:
            when = max(at_ms, last.get(piece.speaker, at_ms))
            last[piece.speaker] = when
            events.append(_Event(when, "frame", len(events), piece.speaker, at_ms, pcm))
        end = max(piece.end_ms + end_delay_ms, last[piece.speaker])
        last[piece.speaker] = end
        events.append(_Event(end, "end", len(events), piece.speaker))
    for change in changes:
        events.append(
            _Event(change.at_ms, "consent", len(events), change.speaker, agrees=change.agrees)
        )
    events.sort(key=lambda e: (e.when_ms, e.kind != "consent", e.order))
    return events


async def replay(
    pieces: Sequence[Piece],
    transcriber: Transcriber,
    *,
    hints: Sequence[str] = (),
    realtime: bool = False,
    outside: bool = False,
    end_delay_ms: int = END_DELAY_MS,
    changes: Sequence[ConsentChange] = (),
) -> Replay:
    """Write the pieces down. `realtime` sends each frame at its time and ends each piece
    when ears would, so waits mean what they mean live; otherwise everything is queued
    as soon as there's room, and only the scores mean anything.

    Each piece is its speaker's. `changes` stop or start a speaker's recording part-way,
    as ears and core do live: a speaker who hasn't agreed is never captured (ears'
    consent list), and a stop drops what's being heard (`bot.stop_recording`) while the
    pipeline's own checks drop what's waiting to be written down."""
    result = Replay(realtime=realtime, hints=len(hints), changes=tuple(changes))
    segmenter = Segmenter(TWIN_GUILD)
    first_change: dict[int, ConsentChange] = {}
    for change in sorted(changes, key=lambda c: c.at_ms):
        first_change.setdefault(change.speaker, change)
    speakers = {p.speaker for p in pieces} | set(first_change)
    consent = TwinConsent(
        s for s in speakers if s not in first_change or not first_change[s].agrees
    )
    queued_at: dict[tuple[int, int], float] = {}

    def deliver(utterance: Utterance, text: str | None) -> None:
        key = (utterance.user_id, utterance.start_ms)
        wait = time.monotonic() - queued_at.get(key, time.monotonic())
        # The Segmenter's end is the last frame's start; the piece runs a frame longer.
        end = utterance.end_ms + FRAME_MS
        result.heard.append(
            Heard(utterance.start_ms, end, text, wait, utterance.duration_s, utterance.user_id)
        )

    async def alert(guild_id: int, text: str) -> None:
        result.alerts.append(text)

    async def name_hints(utterance: Utterance) -> list[str]:
        return list(hints)

    pipeline = TranscriptionPipeline(
        transcriber,
        consent,
        is_active=lambda utterance: utterance.session == segmenter.session,
        hints=name_hints,
        deliver=deliver,
        alert=alert,
        outside=outside,
    )

    async def enqueue(utterance: Utterance) -> None:
        if pipeline.backlog >= QUEUE_SIZE:  # a replay waits; live speech would be lost
            await pipeline.drain(segmenter.session, DRAIN_TIMEOUT_S)
        queued_at[(utterance.user_id, utterance.start_ms)] = time.monotonic()
        pipeline.enqueue(utterance)

    async def until(at_ms: int) -> None:
        if realtime:
            await asyncio.sleep(max(0.0, at_ms / 1000 - (time.monotonic() - started)))

    timeline = _timeline(pieces, changes, end_delay_ms)  # before the clock starts
    agreed_at = {c.speaker: c.at_ms for c in changes if c.agrees}
    worker: asyncio.Task[None] | None = None
    started = time.monotonic()
    try:
        await transcriber.warm_up()
        worker = asyncio.create_task(pipeline.run(), name="replay-transcribe")
        await asyncio.sleep(0)  # let the worker start, or drain() won't wait for it
        started = time.monotonic()
        for event in timeline:
            await until(event.when_ms)
            speaker = event.speaker
            if event.kind == "consent":
                if not realtime:
                    # Live, what was said before the change was written down long
                    # before it; queued all at once, it would still be waiting.
                    caught_up = await pipeline.drain(segmenter.session, DRAIN_TIMEOUT_S)
                    result.finished &= caught_up
                if event.agrees:
                    consent.agreed.add(speaker)
                else:
                    consent.agreed.discard(speaker)
                    segmenter.drop(speaker)
            elif event.kind == "frame":
                if speaker not in consent.agreed or event.at_ms < agreed_at.get(speaker, 0):
                    # Ears never sends a speaker who hasn't agreed, nor what they said
                    # before (a lead-in played late keeps its own, earlier time).
                    continue
                frame = AudioFrame(TWIN_GUILD, speaker, event.at_ms, event.pcm)
                if full := segmenter.add(frame):
                    await enqueue(full)  # the 15 s cut
            elif (utterance := segmenter.end(speaker)) is not None:
                await enqueue(utterance)
        result.finished &= await pipeline.drain(segmenter.session, DRAIN_TIMEOUT_S)
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
    result.heard.sort(key=lambda h: (h.start_ms, h.speaker))
    return result
