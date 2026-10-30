"""Reading a lore upload from the website (#3022): the bot's half of Add to the story > Upload lore.

A DM's paste, file text or link was queued in `lore_uploads` (see `memory.lore_queue`). This reads
it the way Discord's 🤖 Find names does, through the same `read_background`, with no Discord
interaction: names go to Check new names as proposals, connections are queued for the DM's answers,
and what the page shows is counts and stage words only. The text is the DM's own writing and is
never kept or logged: the queue blanks it in the statement that ends the row, whichever way it
ends.

Checked again at the moment of use, because the queue-time check can be stale: the sender and the
person who confirmed the right to use it must still be DMs of the campaign.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from typing import Any

from dmbot.ai import AIError
from dmbot.campaigns.models import OWN_WRITING, CampaignError, fingerprint
from dmbot.memory import lore_queue as q
from dmbot.memory import lore_read
from dmbot.memory.lore_queue import Claim, LoreQueue
from dmbot.ui.name_background import BackgroundRead, characters, read_background
from dmbot.ui.name_lists import (
    BUSY,
    LIMIT,
    LINK_BUSY,
    Upload,
    end_ai_read,
    link_is_busy,
    read_link_once,
    start_ai_read,
)
from dmbot.ui.who_knows import player_characters

log = logging.getLogger(__name__)

# The one limit for a paste, a file's text and a page's text is the table's own (migration 0288's
# `TEXT_MAX`), so the column, the door and this worker cannot disagree.
TEXT_MAX: int = importlib.import_module("dmbot.migrations.0288_lore_uploads").TEXT_MAX
_RELEASED = "released"  # not an outcome: the claim went back to waiting, nothing to end
_LOST = "lost"  # not an outcome: the claim is another worker's now, so this one writes nothing
# The whole read (link, both passes, who-is-it, storing) stops at the read's own limit plus this, so
# a hung call can never hold a claim, or the server's queue, for good.
READ_MARGIN_S = 300.0
PROGRESS_EVERY_S = 2.0  # the page's counts move this often at most; a stage change is written now
LABELS = {"paste": "your lore", "file": "your file", "link": "the linked page"}

# The stage the page shows for each stage the read reports.
_STAGES = {
    "reading": q.READING,
    "looking": q.READING,
    "shaping": q.FINDING_NAMES,
    "who": q.FINDING_LINKS,
}


class _Reporter:
    """Writes the read's stage and counts for the page, without waiting on the read: the read tells
    it where it is (a plain call), and one task writes the latest at most every few seconds. Every
    write also keeps the claim alive."""

    def __init__(self, queue: LoreQueue, guild_id: int, claim: Claim) -> None:
        self._queue, self._guild, self._claim = queue, guild_id, claim
        self._latest: tuple[str, int, int] = (q.READING, 0, 0)
        self._task: asyncio.Task[None] | None = None
        self._reader: asyncio.Task[Any] | None = None  # the read this reports on
        self.lost = False  # the sweep gave the claim to another worker: this read must stop

    def __call__(self, stage: str, done: int, total: int) -> None:
        self._latest = (_STAGES.get(stage, q.READING), done, total)

    async def __aenter__(self) -> _Reporter:
        self._reader = asyncio.current_task()
        self._task = asyncio.ensure_future(self._run())
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            # (also written when nothing moved: the time is what keeps a quiet claim from being
            # taken for a dead one)
            stage, done, total = self._latest
            try:
                if not await self._queue.progress(self._guild, self._claim, stage, done, total):
                    # Not its row any more (a quiet claim was taken back): reading on would
                    # propose every name twice.
                    self.lost = True
                    if self._reader is not None:
                        self._reader.cancel()
                    return
            except Exception as exc:  # the type only: the read goes on without the page's counts
                log.warning("A lore upload's progress could not be written: %s", type(exc).__name__)
            await asyncio.sleep(PROGRESS_EVERY_S)


async def run_upload(bot: Any, queue: LoreQueue, claim: Claim, guild_id: int) -> None:
    """Read one claimed upload and end it. Never raises: the row must never stay "working" for a
    bug. Counts only in the log."""
    error, names, links = q.FAILED, 0, 0
    try:
        # The whole read has a limit: a hung call must not hold the claim (the reporter keeps it
        # alive) or the server's queue for good.
        async with asyncio.timeout(lore_read.LORE_TIME_LIMIT_S + READ_MARGIN_S):
            error, names, links = await _read(bot, queue, claim, guild_id)
    except TimeoutError:
        error = q.OUT_OF_TIME
    except asyncio.CancelledError:
        # Shutting down (or the claim was lost, below): put the claim back so the next start
        # reads it, shielded so the write is not cancelled too. The text stays only until then.
        await asyncio.shield(_put_back(queue, claim, guild_id))
        raise
    except (AIError, CampaignError) as exc:  # CampaignError: the campaign is gone
        log.warning("A lore upload failed: %s", type(exc).__name__)
    except Exception as exc:
        log.warning("A lore upload failed: %s", type(exc).__name__)
    if error in (_RELEASED, _LOST):
        return
    for attempt in (1, 2):  # once more: a row left working is read again, and proposed twice
        try:
            if not await queue.finish(guild_id, claim, error=error, names=names, links=links):
                log.warning("A lore upload was ended by another worker")
                return
            break
        except Exception as exc:  # the sweep takes it back if both fail
            log.warning("A lore upload could not be ended: %s", type(exc).__name__)
            if attempt == 2:
                return
    log.info(
        "Lore upload %s: %s (%d names, %d connections)", claim.source, error or "done", names, links
    )


async def _put_back(queue: LoreQueue, claim: Claim, guild_id: int) -> None:
    try:
        await queue.release(guild_id, claim)
    except Exception as exc:
        log.warning("A lore upload could not be put back: %s", type(exc).__name__)


async def _read(bot: Any, queue: LoreQueue, claim: Claim, guild_id: int) -> tuple[str, int, int]:
    """(error word or "", names, connections). An error word is an upload that read nothing."""
    campaign = await bot.campaigns.get(guild_id, claim.campaign_id)
    if campaign is None:
        return q.NOT_ALLOWED, 0, 0
    # Both must still be DMs: the sender, and the one who confirmed the right to use the text.
    if claim.user_id not in campaign.dm_user_ids or claim.rights_by not in campaign.dm_user_ids:
        return q.NOT_ALLOWED, 0, 0
    memory = bot.memory
    if bot.background_ai is None or memory is None:
        return q.NO_AI, 0, 0
    if await bot.plan_gate("ai", guild_id, campaign, claim.user_id) is not None:
        return q.NO_AI, 0, 0
    if len(claim.text) > TEXT_MAX or len(claim.link) > TEXT_MAX:
        return q.TOO_LONG, 0, 0
    # (a link still being opened for the server waits before it takes a read of the day's allowance)
    if claim.source == "link" and link_is_busy(guild_id):
        await queue.release(guild_id, claim)
        return _RELEASED, 0, 0
    # One reader at a time per server and the day's allowance, shared with Discord's button.
    why_not = start_ai_read(guild_id, time.time())
    if why_not == BUSY:
        await queue.release(guild_id, claim)  # not an answer: the next pass takes it again
        return _RELEASED, 0, 0
    if why_not == LIMIT:
        return q.NO_AI, 0, 0
    try:
        text = claim.text
        if claim.source == "link":
            # The same reader as Discord's link upload: https only, public addresses only (checked
            # after DNS and on every redirect), no credentials in the address, at most three
            # redirects, one page fetched once.
            got, problem = await read_link_once(guild_id, claim.link)
            if got is None:
                if problem == LINK_BUSY:
                    await queue.release(guild_id, claim)
                    return _RELEASED, 0, 0
                return q.LINK_FAILED, 0, 0
            text = got.text
            if len(text) > TEXT_MAX:
                return q.TOO_LONG, 0, 0
        if not text.strip():
            return q.NOTHING_FOUND, 0, 0
        # Whose backstory it is: the DM's answer on the page, checked against the campaign's own
        # characters (never taken on trust), and a player character is who knows the connections.
        speaker, known_by = await _speaker(memory, campaign, claim.speaker)
        # The DM said it is their own writing; a lasting record, before anything is read.
        await bot.campaigns.record_confirmation(
            guild_id, campaign.id, claim.rights_by, OWN_WRITING, fingerprint(text)
        )
        upload = Upload(text, LABELS.get(claim.source, "your lore"), document=True)
        # (`claim.choice`, what the lore is known as, is #2959's: until it lands the read is what
        # Discord's does today, "may change in play")
        report = _Reporter(queue, guild_id, claim)
        try:
            async with report:
                done = await read_background(
                    bot,
                    campaign,
                    upload,
                    truth=False,
                    speaker=speaker,
                    known_by=known_by,
                    started=time.monotonic(),
                    progress=report,
                )
        except asyncio.CancelledError:
            if (
                report.lost
            ):  # the claim is another worker's: it ends the row, this one writes nothing
                return _LOST, 0, 0
            raise
        return _outcome(done)
    finally:
        end_ai_read(guild_id)


async def _speaker(memory: Any, campaign: Any, entry_id: str) -> tuple[str | None, str]:
    """(the character's name, their id when they are a player character) for the speaker the DM
    picked; (None, "") for none, or one that is not a confirmed character any more."""
    if not entry_id:
        return None, ""
    names = dict(await characters(memory, campaign))
    name = names.get(entry_id)
    if name is None:
        return None, ""
    pcs = {c for c, _ in await player_characters(memory, campaign)}
    return name, entry_id if entry_id in pcs else ""


def _outcome(done: BackgroundRead) -> tuple[str, int, int]:
    """What to tell the page: the counts, or why nothing was made. A read cut off at the time limit
    that still found something is done (the page says part of it was missed through the cut note
    on Discord's side; here the counts are what is shown)."""
    names = sum(len(ids) for ids in done.stored.values())
    waiting = done.queued + done.dropped.get("which_waiting", 0)
    if names or waiting:
        return "", names, done.links_found
    if done.cut:
        return q.OUT_OF_TIME, 0, 0
    return q.NOTHING_FOUND, 0, done.links_found
