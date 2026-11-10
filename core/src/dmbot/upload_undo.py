"""The website's "Undo this upload" press, applied by the bot (#4078; the bot half of #3879). The
page queues a row in `upload_undos` (ids and the outcome, never a name or a label); this round
claims the oldest, then runs the Discord button's own code (`ui.upload_notice.undo_upload_result`),
which checks again that the sender is still a DM of the campaign, that the upload can still be
undone (7 days, not taken back) and takes its changes back newest first. It ends `done` with the
count of changes taken back, or `failed` with one word of at most 40 characters the page turns into
plain words: `gone`, `not_yours`, `already`, `too_late` or `failed`. The words of the Discord answer
never go into the row. While it runs the row's claim is moved every few seconds (the heartbeat), so
a long undo is not taken for a lost one. Counts only in the log.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dmbot.db import Database
from dmbot.logs import log_context
from dmbot.ui import upload_notice as un

log = logging.getLogger(__name__)

GIVE_UP_S = 30 * 60  # as the door's: a press older than this was taken as lost
RETAKE_AFTER_S = 10 * 60  # a claim that stopped moving this long ago (the bot restarted) is retaken
WATCH_EVERY_S = 5.0  # how often a running undo moves its claim
MAX_TRIES = 3  # an undo that keeps stopping the bot is failed, not retaken for ever

# The words a failed press ends with (the page turns them into plain words). The door also writes
# "lost" itself.
GONE, NOT_YOURS, ALREADY, TOO_LATE, FAILED = "gone", "not_yours", "already", "too_late", "failed"
WORDS = (GONE, NOT_YOURS, ALREADY, TOO_LATE, FAILED)

# The Discord answers that name a refusal, and the word each becomes. Any other answer is a result
# (done, or partly) or a stop (`failed`): its numbers are in `Undone`, never parsed from its words.
_REFUSALS = {
    un.GONE: GONE,
    un.NOT_YOURS: NOT_YOURS,
    un.ALREADY: ALREADY,
    un.TOO_LATE: TOO_LATE,
}


@dataclass(slots=True)
class Press:
    id: str
    campaign_id: str
    notice_id: str
    user_id: int
    claimed_at: int  # milliseconds: the token that lets only this worker write
    tries: int = 1


class UploadUndos:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Press | None:
        """The oldest waiting press (or one that stopped moving), marked as being worked on."""
        now_ms = int(self._now() * 1000)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE upload_undos SET state = 'working', claimed_at = %s, tries = tries + 1"
                " WHERE (guild_id, id) = (SELECT guild_id, id FROM upload_undos"
                " WHERE guild_id = %s AND created_at >= %s AND (state = 'waiting'"
                " OR (state = 'working' AND claimed_at < %s))"
                " ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, notice_id, user_id, tries",
                (now_ms, guild_id, now_ms // 1000 - GIVE_UP_S, now_ms - RETAKE_AFTER_S * 1000),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Press(
            str(row["id"]), str(row["campaign_id"]), str(row["notice_id"]),
            int(row["user_id"]), now_ms, int(row["tries"]),
        )  # fmt: skip

    async def moved(self, guild_id: int, press: Press) -> bool:
        """Show the page the undo is alive. False: the press is not this worker's any more."""
        stamp = int(self._now() * 1000)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE upload_undos SET claimed_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (stamp, guild_id, press.id, press.claimed_at),
            )
            moved = bool(cur.rowcount)
        if moved:
            press.claimed_at = stamp
        return moved

    async def end(
        self, guild_id: int, press: Press, *, ok: bool, error: str = "", undone: int = 0
    ) -> bool:
        """Close the press, only if this worker still holds it."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE upload_undos SET state = %s, error = %s, undone = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                ("done" if ok else "failed", "" if ok else error[:40], undone, guild_id,
                 press.id, press.claimed_at),
            )  # fmt: skip
            return bool(cur.rowcount)


def outcome(result: un.Undone) -> tuple[str, int]:
    """The word a result ends with ("" for done) and the changes taken back. Pure.

    A refusal ends with its word. An undo that stopped part way (an unexpected error: `STOPPED`) or
    could not start (`COULD_NOT`) is `failed`; one that took back some or all is done, with the
    count: a change made since is left as it is, which is a result, not a failure."""
    if result.answer in _REFUSALS:
        return _REFUSALS[result.answer], 0
    if result.answer == un.COULD_NOT:
        return FAILED, 0
    if result.stopped:
        return FAILED, result.done
    return "", result.done


async def run_round(bot: Any, guild_id: int) -> int:
    """Apply one waiting press on a server; returns how many were taken (0 or 1)."""
    if bot.memory is None or bot.upload_notices is None:
        return 0
    undos = UploadUndos(bot.campaigns.db)
    press = await undos.claim_next(guild_id)
    if press is None:
        return 0
    with log_context(guild_id=guild_id, campaign_id=press.campaign_id):
        word, taken = "", 0
        try:
            if press.tries > MAX_TRIES:
                word = FAILED
            else:
                word, taken = await _apply(bot, undos, guild_id, press)
        except Exception as exc:
            log.warning("A website upload undo failed (%s)", type(exc).__name__)
            word = FAILED
        try:
            held = await undos.end(guild_id, press, ok=not word, error=word, undone=taken)
        except Exception as exc:  # the row stays working and is retaken, then failed at MAX_TRIES
            log.warning("A website upload undo could not be closed (%s)", type(exc).__name__)
            return 1
        log.info(
            "A website upload undo ended: %s, %d taken back%s",
            word or "done", taken, "" if held else " (lost)",
        )  # fmt: skip
    return 1


async def _apply(bot: Any, undos: UploadUndos, guild_id: int, press: Press) -> tuple[str, int]:
    """The press's word and count. The Discord button's own code makes every check again: a minute
    may have passed since the page queued it."""
    watcher = asyncio.create_task(_follow(undos, guild_id, press))
    try:
        result = await un.undo_upload_result(
            bot, guild_id, press.campaign_id, press.notice_id, press.user_id
        )
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
    return outcome(result)


async def _follow(undos: UploadUndos, guild_id: int, press: Press) -> None:
    """Move the claim while the undo runs. Its own failures end it quietly: the undo must never stop
    for the page."""
    while True:
        await asyncio.sleep(WATCH_EVERY_S)
        try:
            if not await undos.moved(guild_id, press):
                return  # (the press is not ours any more)
        except Exception as exc:  # one failed look must not stop the heartbeat
            log.warning("Following an upload undo missed a look (%s)", type(exc).__name__)
