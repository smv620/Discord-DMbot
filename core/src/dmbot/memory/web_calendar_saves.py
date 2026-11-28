"""The bot's side of saving a campaign's calendar from the website (#5364 slice 3b): a queued row
(`web_calendar_saves`, migration 0654) is claimed in order and saved with the very store call
Discord's setting makes (`ClockStore.set_calendar` / `set_custom_calendar`), which checks again
that the person is a DM of the campaign, that it has a game clock, and that every value is one the
calendar has.

A save is instant, so the row ends `done` or `failed` with an error word the page turns into words
(`bad_calendar` is the one generic sentence: the website already checked the body and said what was
wrong before it queued anything, so this is a rare race, and the read door never returns the body).
A row holds the DM's own names for months and weekdays: logs carry outcome words only.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Database
from dmbot.timebot import calendar as cal
from dmbot.timebot import calendar_setting as setting
from dmbot.timebot.store import BAD_CALENDAR, NO_CLOCK, NOT_A_DM, ClockStore

log = logging.getLogger(__name__)

RETAKE_AFTER_S = 600  # the door's own limit for a claim that went quiet
KEEP_FOR_S = 86400  # as long as the website reads an answer back

NOT_ALLOWED = "not_allowed"  # no longer a DM of the campaign
NO_CLOCK_ERROR = "no_clock"  # the campaign has no game clock to save onto
BAD_CALENDAR_ERROR = "bad_calendar"  # a value the calendar has not got
FAILED = "failed"  # anything else


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    campaign_id: str
    user_id: int
    body: Any
    claimed_at: int


class SaveStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Item | None:
        """The oldest waiting save of this server (or a stalled one), marked as taken."""
        now_ms = int(self._now() * 1000)  # milliseconds, so two claims of one row never match
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "WITH pick AS (SELECT guild_id, id FROM web_calendar_saves"
                " WHERE guild_id = %s AND (state = 'waiting'"
                "  OR (state = 'working' AND claimed_at < %s))"
                " ORDER BY seq LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " UPDATE web_calendar_saves a SET state = 'working', claimed_at = %s,"
                " updated_at = %s FROM pick WHERE a.guild_id = pick.guild_id AND a.id = pick.id"
                " RETURNING a.id, a.campaign_id, a.user_id, a.body",
                (guild_id, now_ms - RETAKE_AFTER_S * 1000, now_ms, now),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Item(row["id"], row["campaign_id"], int(row["user_id"]), row["body"], now_ms)

    async def finish(self, guild_id: int, item: Item, error: str) -> bool:
        """End the save. Only the worker that holds the current claim can write."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_calendar_saves SET state = %s, error = %s, updated_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (
                    "failed" if error else "done",
                    error[:40],
                    int(self._now()),
                    guild_id,
                    item.id,
                    item.claimed_at,
                ),
            )
            return bool(cur.rowcount)

    async def sweep(self, guild_id: int) -> int:
        """Delete saves older than `KEEP_FOR_S`, whatever their state."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM web_calendar_saves WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


async def apply(clocks: ClockStore, guild_id: int, item: Item) -> str:
    """Save one calendar and say how it ended: '' for saved, else an error word. The body is made
    into a calendar again here (the website did it before queuing), so a row nobody checked is
    never saved."""
    try:
        calendar = setting.from_body(item.body)
    except setting.CalendarProblem:
        return BAD_CALENDAR_ERROR
    try:
        if calendar.kind == cal.CUSTOM and calendar.layout is not None:
            one = setting.day_one_of(calendar)
            if one is None:
                return BAD_CALENDAR_ERROR
            await clocks.set_custom_calendar(
                guild_id,
                item.campaign_id,
                item.user_id,
                calendar.layout,
                year=one["year"],
                name=one["part"],
                day=one["day"],
                weekday=one["weekday"],
            )
        elif calendar.kind == cal.HARPTOS:
            one = setting.day_one_of(calendar)
            if one is None:
                return BAD_CALENDAR_ERROR
            await clocks.set_calendar(
                guild_id,
                item.campaign_id,
                item.user_id,
                cal.HARPTOS,
                year=one["year"],
                name=one["part"],
                day=one["day"],
            )
        else:
            await clocks.set_calendar(guild_id, item.campaign_id, item.user_id, cal.DAYS)
    except CampaignError as exc:
        text = str(exc)
        if text == NOT_A_DM:
            return NOT_ALLOWED
        if text == NO_CLOCK:
            return NO_CLOCK_ERROR
        return BAD_CALENDAR_ERROR if text == BAD_CALENDAR else FAILED
    return ""


async def run_one(clocks: ClockStore, store: SaveStore, guild_id: int, item: Item) -> str:
    """Do one save and finish its row. Outcome words only in the log, never a name."""
    try:
        error = await apply(clocks, guild_id, item)
    except Exception as exc:  # the type only: nothing here may quote the DM's words
        log.warning("A calendar save failed: %s", type(exc).__name__)
        error = FAILED
    await store.finish(guild_id, item, error)
    log.info("A calendar save ended: %s", error or "saved")
    return error
