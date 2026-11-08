"""The bot's side of 🎤 Say it on the website's Names page (#2316): a DM's clip of a name, turned
into a respelling.

The website's container does no listening, so a press is a row (`said_as_listens`). This module
claims it, reads the clip into 16 kHz audio in memory (at most 10 seconds), has the speech-sound
recogniser (a worker process apart from the bot) write the sounds, and turns them into plain
letters (`said_as.respell`). The clip is cleared in the same statement that stores the answer, or
the failure, so it never outlives the work; it is never written to disk, logged or put in a
transcript or a backup. Only an outcome word is logged.

Live recording always comes first: a clip is not started while any table is listening (the row
waits), and the worker runs at a lower priority and is unloaded after ten quiet minutes. The sender
must still be a DM of the campaign and the owner's plan must still include narration (the same gate
as Hear it), checked again here. DMbot suggests; the DM decides what to save.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from dmbot import audio_decode
from dmbot.campaigns import Campaign
from dmbot.db import Database
from dmbot.said_as.recogniser import FAILED, VOICE_OFF, Recogniser, RecogniserError
from dmbot.said_as.respell import respell

log = logging.getLogger(__name__)

PER_ROUND = 3  # clips worked in one round for one server
RETAKE_AFTER_S = 300  # a claim this old belongs to a bot that stopped
KEEP_FOR_S = 3600  # nothing waits on a row longer than this
TRIES_MAX = 2  # a clip is worked at most twice: a stalled claim is retaken once, then fails
MAX_CLIP_BYTES = 1_000_000
MAX_CLIP_S = 10.0
MIN_CLIP_S = 0.4  # shorter is a click, not a name
DECODE_BUDGET_S = 10.0
DEMUXERS = {"webm": "matroska,webm", "ogg": "ogg", "wav": "wav"}
PROBE = {"probesize": "65536", "analyzeduration": "1000000"}

# What a failed clip says, as one word for the page.
NOT_ALLOWED = "not_allowed"  # the sender is not a DM of the campaign any more
NOT_FOUND = "not_found"  # the campaign is gone
BAD_CLIP = "bad_clip"  # not audio we can read, too long or short, or nothing made out of it


@dataclass(frozen=True, slots=True)
class Listen:
    id: str
    campaign_id: str
    user_id: int
    container: str
    clip: bytes  # the DM's voice: held in memory only
    claimed_at: int
    tries: int = 1


@dataclass(frozen=True, slots=True)
class Outcome:
    error: str = ""
    respelling: str = ""
    ipa: str = ""


class Host(Protocol):
    async def get_campaign(self, guild_id: int, campaign_id: str) -> Campaign | None: ...

    async def plan_allows_narration(self, guild_id: int, campaign: Campaign) -> bool: ...

    def recogniser(self) -> Recogniser | None:
        """The speech-sound recogniser, or None when "🎤 Say it" is switched off here."""
        ...


class ListenStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Listen | None:
        """The oldest waiting clip of this server (or a stalled one), marked as being worked on."""
        now = int(self._now() * 1000)  # milliseconds, so two claims of one row never match
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE said_as_listens SET state = 'working', claimed_at = %s, tries = tries + 1"
                " WHERE (guild_id, id) = (SELECT guild_id, id FROM said_as_listens"
                " WHERE guild_id = %s AND (state = 'waiting' OR (state = 'working'"
                " AND claimed_at < %s)) ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, user_id, container, clip, tries",
                (now, guild_id, now - RETAKE_AFTER_S * 1000),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Listen(
            row["id"],
            row["campaign_id"],
            int(row["user_id"]),
            row["container"],
            bytes(row["clip"] or b""),
            now,
            int(row["tries"]),
        )

    async def finish(self, guild_id: int, item: Listen, outcome: Outcome) -> bool:
        """Done with the respelling, or failed with why: the clip is cleared in this same
        statement. Only the worker holding the current claim can write; False means it is not this
        worker's row any more."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE said_as_listens SET state = %s, error = %s, respelling = %s, ipa = %s,"
                " clip = NULL WHERE guild_id = %s AND id = %s AND state = 'working'"
                " AND claimed_at = %s",
                (
                    "failed" if outcome.error else "done",
                    outcome.error,
                    outcome.respelling,
                    outcome.ipa[:400],
                    guild_id,
                    item.id,
                    item.claimed_at,
                ),
            )
            return bool(cur.rowcount)

    async def sweep(self, guild_id: int) -> int:
        """Delete clips older than `KEEP_FOR_S`, whatever their state."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM said_as_listens WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


def decode(item: Listen) -> bytes:
    """The clip as 16 kHz mono PCM, or BAD_CLIP. Blocking: run it off the event loop."""
    demuxer = DEMUXERS.get(item.container)
    if demuxer is None:
        raise RecogniserError(BAD_CLIP)
    try:
        return audio_decode.decode_pcm(
            item.clip,
            container=demuxer,
            max_bytes=MAX_CLIP_BYTES,
            max_s=MAX_CLIP_S,
            min_s=MIN_CLIP_S,
            budget_s=DECODE_BUDGET_S,
            options=PROBE,
        )
    except audio_decode.Refused:
        raise RecogniserError(BAD_CLIP) from None


async def apply(host: Host, guild_id: int, item: Listen) -> Outcome:
    """Turn one clip into a respelling. Never raises for a refusal: the outcome carries the word."""
    if item.tries > TRIES_MAX:  # taken again after a stall: it was started once already
        return Outcome(FAILED)
    campaign = await host.get_campaign(guild_id, item.campaign_id)
    if campaign is None:
        return Outcome(NOT_FOUND)
    if item.user_id not in campaign.dm_user_ids:  # checked again: they may have handed over since
        return Outcome(NOT_ALLOWED)
    recogniser = host.recogniser()
    if recogniser is None or not await host.plan_allows_narration(guild_id, campaign):
        return Outcome(VOICE_OFF)
    try:
        pcm = await asyncio.to_thread(decode, item)
        ipa = await recogniser.recognise(pcm)
    except RecogniserError as exc:
        return Outcome(exc.code)
    spelling = respell(ipa)
    if not spelling:  # no vowel heard: the page says it could not make it out
        return Outcome(BAD_CLIP)
    return Outcome(respelling=spelling, ipa=ipa)


async def run_one(host: Host, store: ListenStore, guild_id: int, item: Listen) -> bool:
    """Work one claimed clip and mark it, clearing the clip. A bug ends as `failed`, never
    "working". False when the row could not be marked (the loop stops; it is retaken later). Only
    the outcome word is logged."""
    try:
        done = await apply(host, guild_id, item)
    except Exception as exc:
        log.warning("Say it failed: %s", type(exc).__name__)
        done = Outcome(FAILED)
    try:
        if not await store.finish(guild_id, item, done):
            log.info("Say it was taken over by another worker")
    except Exception as exc:
        log.warning("Say it could not be marked: %s", type(exc).__name__)
        return False
    log.info("Say it: %s", done.error or "done")
    return True
