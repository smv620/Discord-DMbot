"""The website's "Make the script" press, answered by the bot (#3899; docs/PLAN.md, "A script for
every session"). The page queues a row in `script_makes` (ids and counts only, never a line of the
session); this round claims the oldest, checks again that the sender is still a DM of the campaign,
that the switch and the plan allow it and that the session is the campaign's own, then runs the
same job as everything else (`ui.theatre_button.make_for`) so its one-at-a-time row in
`theatre_runs` keeps a press, the automatic round (`auto_script`) and the Discord button from ever
starting a second AI run for one session. While it runs the row's part and of_parts follow the
job's own, which is what keeps the page's "last moved" fresh. It ends `done`, or `failed` with a
short word (at most 40 characters) the page turns into plain words. Counts only in the log.
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
from dmbot.transcript.theatre_runs import TheatreRuns
from dmbot.ui import theatre_button

log = logging.getLogger(__name__)

GIVE_UP_S = 30 * 60  # as the door's: a press older than this was taken as lost
RETAKE_AFTER_S = 10 * 60  # a claim that stopped moving this long ago (the bot restarted) is retaken
WATCH_EVERY_S = 3.0  # how often the row follows the job's progress

# The words a failed press ends with (the page turns them into plain words).
# ("failed" is the general one; it is not "no-script", which the Transcripts page already uses for
# something else.) The door also writes "lost" itself.
NOT_ALLOWED, OFF, PLAN, GONE, BUSY, NO_SCRIPT = (
    "not-allowed",
    "off",
    "plan",
    "gone",
    "busy",
    "failed",
)
WORDS = (NOT_ALLOWED, OFF, PLAN, GONE, BUSY, NO_SCRIPT)
MAX_TRIES = 3  # a press that keeps stopping the bot is failed, not retaken for ever


@dataclass(slots=True)
class Press:
    id: str
    campaign_id: str
    session_id: str
    user_id: int
    claimed_at: int  # milliseconds: the token that lets only this worker write
    tries: int = 1


class ScriptMakes:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Press | None:
        """The oldest waiting press (or one that stopped moving), marked as being worked on."""
        now_ms = int(self._now() * 1000)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE script_makes SET state = 'working', claimed_at = %s, tries = tries + 1"
                " WHERE (guild_id, id) = (SELECT guild_id, id FROM script_makes"
                " WHERE guild_id = %s AND created_at >= %s AND (state = 'waiting'"
                " OR (state = 'working' AND claimed_at < %s))"
                " ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, session_id, user_id, tries",
                (now_ms, guild_id, now_ms // 1000 - GIVE_UP_S, now_ms - RETAKE_AFTER_S * 1000),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Press(
            str(row["id"]), str(row["campaign_id"]), str(row["session_id"]),
            int(row["user_id"]), now_ms, int(row["tries"]),
        )  # fmt: skip

    async def moved(self, guild_id: int, press: Press, part: int, of_parts: int) -> bool:
        """Follow the job's progress, and show the page it is alive. False: the press is not this
        worker's any more."""
        stamp = int(self._now() * 1000)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE script_makes SET part = %s, of_parts = %s, claimed_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (part, of_parts, stamp, guild_id, press.id, press.claimed_at),
            )
            moved = bool(cur.rowcount)
        if moved:
            press.claimed_at = stamp
        return moved

    async def end(self, guild_id: int, press: Press, *, ok: bool, error: str = "") -> bool:
        """Close the press, only if this worker still holds it."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE script_makes SET state = %s, error = %s WHERE guild_id = %s AND id = %s"
                " AND state = 'working' AND claimed_at = %s",
                ("done" if ok else "failed", "" if ok else error[:40], guild_id, press.id,
                 press.claimed_at),
            )  # fmt: skip
            return bool(cur.rowcount)


async def run_round(bot: Any, guild_id: int) -> int:
    """Answer one waiting press on a server; returns how many were taken (0 or 1)."""
    if bot.theatre_ai is None or bot.memory is None or bot.transcripts is None:
        return 0
    makes = ScriptMakes(bot.campaigns.db)
    press = await makes.claim_next(guild_id)
    if press is None:
        return 0
    with log_context(guild_id=guild_id, campaign_id=press.campaign_id):
        try:
            word = (
                NO_SCRIPT if press.tries > MAX_TRIES else await _answer(bot, makes, guild_id, press)
            )
        except Exception as exc:
            log.warning("A website script press failed (%s)", type(exc).__name__)
            word = NO_SCRIPT
        try:
            held = await makes.end(guild_id, press, ok=not word, error=word)
        except Exception as exc:  # the row stays working and is retaken, then failed at MAX_TRIES
            log.warning("A website script press could not be closed (%s)", type(exc).__name__)
            return 1
        log.info("A website script press ended: %s%s", word or "done", "" if held else " (lost)")
    return 1


async def _answer(bot: Any, makes: ScriptMakes, guild_id: int, press: Press) -> str:
    """The word the press ends with ("" for a script made). Every check is made again here: a
    minute may have passed since the page queued it."""
    campaign = await bot.campaigns.get(guild_id, press.campaign_id)
    session = await bot.transcripts.session(guild_id, press.session_id)
    if campaign is None or session is None or session.campaign_id != press.campaign_id:
        return GONE
    if not campaign.is_dm(press.user_id):  # still a DM of this campaign
        return NOT_ALLOWED
    if not bot.settings.theatre_on:
        return OFF
    if not await bot.plan_allows("story", guild_id, campaign, fail_open=False):
        return PLAN

    async def quiet(_text: str) -> None:
        """The page shows the row's counts; no private card is kept."""

    runs = TheatreRuns(bot.campaigns.db)
    if await runs.state(guild_id, press.session_id) == "done":
        return ""  # a script was made meanwhile (the automatic round, the button, a retaken press)
    watcher = asyncio.create_task(_follow(makes, runs, guild_id, press))
    try:
        outcome = await theatre_button.make_for(
            bot, guild_id, press.session_id, session, campaign, quiet
        )
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
    if not outcome.started:
        return BUSY  # another run has the session: the page asks again, nothing is paid twice
    return "" if outcome.ok else NO_SCRIPT


async def _follow(makes: ScriptMakes, runs: TheatreRuns, guild_id: int, press: Press) -> None:
    """Copy the job's part and count to the press while it runs. Its own failures end it quietly:
    the job must never stop for the page."""
    while True:
        await asyncio.sleep(WATCH_EVERY_S)
        try:
            found = await runs.progress(guild_id, press.session_id)
            if found is not None and not await makes.moved(guild_id, press, *found):
                return  # (the press is not ours any more)
        except Exception as exc:  # one failed look must not stop the heartbeat
            log.warning("Following a script press missed a look (%s)", type(exc).__name__)
