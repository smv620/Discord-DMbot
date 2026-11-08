"""The bot's side of 🔊 Hear it on the website's Names page (#3807): a name said aloud by the
recap narrator, so the DM can check how the narrator will really say it.

The website's container is never given the voice company's key (#2419), so a press is a row
(`said_as_hears`) that this module claims, speaks with the narrator's voice and the recaps' speech
model, and puts back on the row as a short WAV clip. The page fetches the clip once and the door
clears it. Nothing here is kept longer than the row (an hour) except the cache below.

The words are the DM's own typing and go to the voice company and nowhere else: only counts and
outcome words are logged. The sender must still be a DM of the campaign and the campaign's owner's
plan must still include narration (the recap voice-over's gate), both checked again here at the
moment of use. The cost is the voice company's per-character credits, counted and logged.
"""

from __future__ import annotations

import hashlib
import io
import logging
import time
import wave
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from dmbot.campaigns import Campaign
from dmbot.db import Database
from dmbot.recaps.audio.eleven import SPEECH_RATE, ElevenLabs, ProviderError

log = logging.getLogger(__name__)

PER_ROUND = 5  # presses answered in one round for one server
RETAKE_AFTER_S = 60  # a claim this old belongs to a bot that stopped
KEEP_FOR_S = 3600  # nothing waits on a row longer than this
CACHE_FOR_S = 86_400  # a clip made once is reused for a day
CACHE_MAX = 100  # about 100 KB each: a bound on memory, oldest out first

# What a failed press says, as one word for the page.
NOT_ALLOWED = "not_allowed"  # the sender is not a DM of the campaign any more
NOT_FOUND = "not_found"  # the campaign is gone
VOICE_OFF = "voice_off"  # no voice set up here, or the owner's plan has no narration
FAILED = "failed"  # anything unexpected
BUSY = "busy"  # the voice company was busy or out of credits; trying again later may work


@dataclass(frozen=True, slots=True)
class Hear:
    id: str
    campaign_id: str
    user_id: int
    text: str  # the DM's words: never logged
    claimed_at: int


class Host(Protocol):
    """What a press needs from the running bot, so tests can stand in for it."""

    async def get_campaign(self, guild_id: int, campaign_id: str) -> Campaign | None: ...

    async def plan_allows_narration(self, guild_id: int, campaign: Campaign) -> bool:
        """The owner's plan includes narration (`recap_audio`), failing closed."""
        ...

    def narrator(self) -> ElevenLabs | None:
        """A fresh voice client, or None when the voice is not set up."""
        ...


class HearCache:
    """Clips by (voice, model, text) for a day, so pressing Hear it twice costs one call. Only
    in memory: it goes with the process, and it holds the words as a hash, never as text."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._now = clock
        self._clips: OrderedDict[str, tuple[float, bytes]] = OrderedDict()

    @staticmethod
    def key(voice: str, model: str, text: str) -> str:
        return hashlib.sha256(f"{voice}\0{model}\0{text}".encode()).hexdigest()

    def get(self, key: str) -> bytes | None:
        found = self._clips.get(key)
        if found is None:
            return None
        if found[0] < self._now():
            del self._clips[key]
            return None
        return found[1]

    def put(self, key: str, clip: bytes) -> None:
        self._clips[key] = (self._now() + CACHE_FOR_S, clip)
        self._clips.move_to_end(key)
        while len(self._clips) > CACHE_MAX:
            self._clips.popitem(last=False)


def wav(pcm: bytes) -> bytes:
    """The speech call's 24 kHz 16-bit mono PCM as a WAV file every browser plays."""
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SPEECH_RATE)
        w.writeframes(pcm)
    return out.getvalue()


@dataclass(frozen=True, slots=True)
class Outcome:
    error: str = ""
    audio: bytes | None = None
    credits: int = 0


class HearStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Hear | None:
        """The oldest waiting press of this server (or a stalled one), marked as being worked on."""
        now = int(self._now() * 1000)  # milliseconds, so two claims of one row never match
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE said_as_hears SET state = 'working', claimed_at = %s"
                " WHERE (guild_id, id) = (SELECT guild_id, id FROM said_as_hears"
                " WHERE guild_id = %s AND (state = 'waiting' OR (state = 'working'"
                " AND claimed_at < %s)) ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, user_id, text",
                (now, guild_id, now - RETAKE_AFTER_S * 1000),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Hear(row["id"], row["campaign_id"], int(row["user_id"]), row["text"], now)

    async def finish(self, guild_id: int, item: Hear, outcome: Outcome) -> bool:
        """Done with the clip, or failed with why. Only the worker holding the current claim can
        write; False means it is not this worker's row any more."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE said_as_hears SET state = %s, error = %s, audio = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (
                    "failed" if outcome.error else "done",
                    outcome.error,
                    None if outcome.error else outcome.audio,
                    guild_id,
                    item.id,
                    item.claimed_at,
                ),
            )
            return bool(cur.rowcount)

    async def sweep(self, guild_id: int) -> int:
        """Delete presses older than `KEEP_FOR_S`, whatever their state."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM said_as_hears WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


async def apply(host: Host, cache: HearCache, guild_id: int, item: Hear) -> Outcome:
    """Make the clip for one press. Never raises for a refusal: the outcome carries the word."""
    campaign = await host.get_campaign(guild_id, item.campaign_id)
    if campaign is None:
        return Outcome(NOT_FOUND)
    if item.user_id not in campaign.dm_user_ids:  # checked again: they may have handed over since
        return Outcome(NOT_ALLOWED)
    voice = host.narrator()
    if voice is None or not await host.plan_allows_narration(guild_id, campaign):
        return Outcome(VOICE_OFF)
    key = HearCache.key(voice.voice_id, voice.speech_model, item.text)
    clip = cache.get(key)
    if clip is not None:
        return Outcome(audio=clip)  # the second press of the same words costs nothing
    try:
        # The seed comes from the words, so the same words sound the same each time.
        seed = int.from_bytes(hashlib.sha256(item.text.encode()).digest()[:4], "big")
        speech = await voice.speech(item.text, seed=seed)
    except ProviderError as exc:
        log.info("Hear it: the voice company said %s", exc.kind)  # the kind, never the words
        return Outcome(
            VOICE_OFF if exc.kind == "auth" else BUSY if exc.kind in ("busy", "plan") else FAILED
        )
    clip = wav(speech.pcm)
    cache.put(key, clip)
    return Outcome(audio=clip, credits=voice.credits)


async def run_one(
    host: Host, store: HearStore, cache: HearCache, guild_id: int, item: Hear
) -> bool:
    """Answer one claimed press and mark it. A bug ends as `failed`, never "working". False when
    the row could not be marked (the loop stops; the row is taken again later). Only the outcome
    word and the credits are logged."""
    try:
        done = await apply(host, cache, guild_id, item)
    except Exception as exc:
        log.warning("Hear it failed: %s", type(exc).__name__)
        done = Outcome(FAILED)
    try:
        if not await store.finish(guild_id, item, done):
            log.info("Hear it was taken over by another worker")
    except Exception as exc:
        log.warning("Hear it could not be marked: %s", type(exc).__name__)
        return False
    log.info("Hear it: %s, %d credits", done.error or "done", done.credits)
    return True
