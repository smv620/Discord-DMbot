"""The House-rules file requests from the website (#3087): the bot's half of its first slice.

A DM's request (connect or change the file, stop using it, compare now, upload one text file) was
queued in `web_house_file_actions` (see `rules.house_file_queue`). This applies it through the same
calls as Discord's 📄 House-rules file buttons: `HouseFileLinkStore` to set or clear the link, then
`house_sync` to read and compare, and the usual offer (Accept all / Review / Not now) is posted on
the campaign's DM screen, as at a session start. Nothing is changed in the house rules here: only
a DM's press on that offer does.

What the page gets back is a stage word, counts (to add, to change, to remove, rules read) and, when
it stopped, one word for why. The link and the file's words are the DM's own: the queue blanks them
in the statement that ends the row, whichever way it ends, and nothing about them is logged.

Checked again at the moment of use, because the queue-time check can be stale: the sender must
still be a DM of the campaign.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord

from dmbot.dm_screen import house_sync
from dmbot.rules import house_file_queue as q
from dmbot.rules.house import HouseRuleError
from dmbot.rules.house_file_queue import Claim, Counts, HouseFileQueue

log = logging.getLogger(__name__)

# The whole request stops at this limit: a hung fetch or AI call must never hold a claim, or the
# server's queue, for good.
TIME_LIMIT_S = 900.0
KEEPALIVE_S = 30.0  # the claim is written this often while it runs, so a quiet one is not retaken

# What the comparison said (its words), as the word the page turns into plain ones.
_FAILED_WORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    ((house_sync.TOO_BIG, house_sync.TOO_MANY_RULES, house_sync.TOO_MANY_RULES_LINKED), q.TOO_BIG),
    ((house_sync.NOT_READ,), q.UNREADABLE),
    ((house_sync.NO_AI_PLAN,), q.NO_AI),
    ((house_sync.BUSY,), q.BUSY),
    ((house_sync.TOO_MANY_READS,), q.DAILY_LIMIT),
    ((house_sync.BOOK,), q.NO_RULES),
    ((house_sync.FAILED,), q.FAILED),
)
# Results, not failures: the files match, or the AI found nothing new.
_NOTHING_TO_OFFER = (house_sync.SAME, house_sync.NOTHING_FOUND)
_NO_RULES_START = "I couldn't find any house rules in your file"


def error_of(words: str) -> str:
    """The word for a comparison's words that say it did not get done."""
    if words.startswith(house_sync.COULDNT_READ_START):
        return q.UNREADABLE
    if words.startswith(_NO_RULES_START):
        return q.NO_RULES
    for known, word in _FAILED_WORDS:
        if words in known:
            return word
    return q.FAILED


class _Keepalive:
    """Writes the stage now and then, so a long read keeps its claim; tells the request when the
    claim has gone to another worker."""

    def __init__(self, queue: HouseFileQueue, guild_id: int, claim: Claim) -> None:
        self._queue, self._guild, self._claim = queue, guild_id, claim
        self.stage = q.FETCHING
        self.lost = False
        self._task: asyncio.Task[None] | None = None
        self._worker: asyncio.Task[Any] | None = None
        self._writes: set[asyncio.Task[None]] = set()

    def set(self, stage: str) -> None:
        """The stage is now this (a plain call, so a progress callback can make it)."""
        self.stage = stage
        write = asyncio.ensure_future(self._write())
        self._writes.add(write)
        write.add_done_callback(self._writes.discard)

    async def _write(self) -> None:
        try:
            if not await self._queue.progress(self._guild, self._claim, self.stage):
                self._lose()
        except Exception as exc:  # the type only: the request goes on without the page's stage
            log.warning(
                "A house-rules file request's stage could not be written: %s", type(exc).__name__
            )

    def _lose(self) -> None:
        self.lost = True
        if self._worker is not None:
            self._worker.cancel()

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(KEEPALIVE_S)
            await self._write()

    async def __aenter__(self) -> _Keepalive:
        self._worker = asyncio.current_task()
        self._task = asyncio.ensure_future(self._run())
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._task is not None:
            self._task.cancel()
        await asyncio.gather(
            *([self._task] if self._task else []), *self._writes, return_exceptions=True
        )


async def run_request(bot: Any, queue: HouseFileQueue, claim: Claim, guild_id: int) -> None:
    """Do one claimed request and end it. Never raises: the row must never stay "working" for a bug.
    Counts and words only in the log, never the link or the file."""
    error, stage, counts = q.FAILED, q.DONE, Counts()
    keep = _Keepalive(queue, guild_id, claim)
    try:
        async with keep, asyncio.timeout(TIME_LIMIT_S):
            error, stage, counts = await _apply(bot, queue, claim, guild_id, keep)
    except TimeoutError:
        error = q.UNREADABLE
    except asyncio.CancelledError:
        if keep.lost:  # the claim is another worker's: it ends the row, this one writes nothing
            return
        # Shutting down: put the claim back so the next start does it again, shielded so the write
        # is not cancelled too. The link and text stay only until then.
        await asyncio.shield(_put_back(queue, claim, guild_id))
        raise
    except Exception as exc:
        log.warning("A house-rules file request failed: %s", type(exc).__name__)
    for attempt in (1, 2):  # once more: a row left working is done again, and offered twice
        try:
            if not await queue.finish(guild_id, claim, error=error, stage=stage, counts=counts):
                log.warning("A house-rules file request was ended by another worker")
                return
            break
        except Exception as exc:  # the sweep takes it back if both fail
            log.warning("A house-rules file request could not be ended: %s", type(exc).__name__)
            if attempt == 2:
                return
    log.info(
        "House-rules file %s: %s (%d to add, %d to change, %d to remove)",
        claim.action,
        error or "done",
        counts.to_add,
        counts.to_change,
        counts.to_remove,
    )


async def _put_back(queue: HouseFileQueue, claim: Claim, guild_id: int) -> None:
    try:
        await queue.release(guild_id, claim)
    except Exception as exc:
        log.warning("A house-rules file request could not be put back: %s", type(exc).__name__)


async def _apply(
    bot: Any, queue: HouseFileQueue, claim: Claim, guild_id: int, keep: _Keepalive
) -> tuple[str, str, Counts]:
    """(error word or "", stage, counts)."""
    campaign = await bot.campaigns.get(guild_id, claim.campaign_id)
    if campaign is None or claim.user_id not in campaign.dm_user_ids:
        return q.NOT_DM, q.DONE, Counts()
    links = bot.house_file_links
    if links is None:
        return q.FAILED, q.DONE, Counts()
    if claim.action == q.UNLINK:
        try:
            await links.clear(guild_id, campaign.id, claim.user_id)
        except HouseRuleError:
            return q.NOT_DM, q.DONE, Counts()
        return "", q.DONE, Counts()  # (the rules already made from the file are kept)
    if claim.action == q.LINK:
        try:
            await links.set_link(guild_id, campaign.id, claim.user_id, claim.link)
        except HouseRuleError:
            return q.BAD_LINK, q.DONE, Counts()
    elif claim.action == q.CHECK:
        if await links.get(guild_id, campaign.id) is None:
            return q.BAD_LINK, q.DONE, Counts()  # nothing is linked to compare
    elif claim.action == q.UPLOAD:
        if not claim.text.strip():
            return q.NO_RULES, q.DONE, Counts()
    else:
        return q.FAILED, q.DONE, Counts()
    return await _compare(bot, campaign, claim, keep)


async def _compare(
    bot: Any, campaign: Any, claim: Claim, keep: _Keepalive
) -> tuple[str, str, Counts]:
    """Read the file (the linked one, or the upload's text), compare it with the campaign's house
    rules and post the offer on its DM screen. Shares Discord's turn-taking (one comparison of a
    campaign at a time, and a short cooldown between two asked for by hand)."""
    from dmbot.ui import house_file as discord_side  # (that module leads back here via the menus)

    wait = discord_side.take_turn(campaign)
    if wait is not None:
        return (q.BUSY if wait == discord_side.ALREADY else q.TOO_OFTEN), q.DONE, Counts()
    try:
        offers: list[bool] = []

        async def send(text: str, view: discord.ui.View) -> bool:
            screen = campaign.dm_screen_channel_id
            if screen is None:
                offers.append(False)
                return False
            posted = await bot.post_message(screen, text, view if view.children else None)
            offers.append(posted is not None)
            return posted is not None

        keep.set(q.FETCHING)
        if claim.action == q.UPLOAD:
            text, problem = claim.text, None
        else:
            text, problem = await house_sync.read_link(bot, campaign)
        if text is None:
            return error_of(problem or house_sync.FAILED), q.DONE, Counts()
        keep.set(q.COMPARING)
        tally = house_sync.Tally()
        words = await house_sync.compare_text(
            bot,
            campaign,
            text,
            send,
            from_link=claim.action != q.UPLOAD,
            respect_ignore=False,  # the DM asked to look, as Discord's Compare now
            progress=lambda _step: keep.set(q.READING),
            tally=tally,
        )
        counts = Counts(tally.to_add, tally.to_change, tally.to_remove, tally.rules_read)
        if words is not None and words not in _NOTHING_TO_OFFER:
            return error_of(words), q.DONE, Counts()
        if offers and not all(offers):
            # The DM would be promised a choice that is not there: say so instead.
            return q.NO_OFFER, q.DONE, Counts()
        return "", q.COMPARED, counts
    finally:
        discord_side.give_back_turn(campaign)
