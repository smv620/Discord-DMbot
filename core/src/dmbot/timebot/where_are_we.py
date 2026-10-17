"""TimeBot's quiet "where are we" note (#1738; docs/PLAN.md, "TimeBot"): during a session, if the
game clock has not been shown or moved for a while, one short line on the DM screen says where
the game is, so a DM who hasn't looked at the pinned clock is reminded. No AI, no stored state.

When: only while DMbot is listening, for a campaign whose clock the DM set, never in combat (the
rounds already speak), and only if the clock was not shown or moved in the last half hour of real
time (every quarter hour on Chatty; none on Quiet). Anything that posts or edits the clock counts
as shown (`shown` below, called by `dm_screen.clock`): a dawn, noon or dusk line, an effect line, a
rest, a button, a skip. It is a reminder, not a warning, so the DM screen's level governs it.

The last time it was shown is kept in memory on the session's table and starts at the session's
start. A restart just starts the wait again from the restart, so the next line may come a little
early or late, and that is fine. Players never see it: DM screen only, like the other TimeBot
notes. The words are the clock's own (`timebot.clock`), never new ones.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from dmbot.dm_screen import levels
from dmbot.timebot import clock as game
from dmbot.timebot.split import Group
from dmbot.timebot.store import Stored

log = logging.getLogger(__name__)

TICK_S = 60  # how often the loop looks; the gap itself is the level's


def when(minute: int) -> str:
    """ "afternoon of Day 4 (14:30)": the clock's own words for the time of day."""
    return f"{game.day_part(minute)} of Day {game.day_of(minute)} ({game.clock_time(minute)})"


def _group(group: Group) -> str:
    return f"the {group.name} group, {when(group.clock.minute)}"


def line(stored: Stored) -> str:
    """The note: one time, or one for each group while the party is split."""
    if stored.split:
        return "🕰️ Where we are: " + "; ".join(_group(g) for g in stored.groups) + "."
    return f"🕰️ Where we are: {when(stored.clock.minute)}."


def in_combat(stored: Stored) -> bool:
    """A fight in any group is a fight at the table."""
    return game.in_combat(stored.clock) or any(game.in_combat(g.clock) for g in stored.groups)


def due(level: str, shown_at: float, now: float) -> bool:
    """Has the clock been quiet for a whole gap at this level? (Never on Quiet.)"""
    gap = levels.reminder_gap_s(level)
    return gap is not None and now - shown_at >= gap


def shown(client: Any, campaign: Any) -> None:
    """The clock was just shown or moved for this campaign: the wait starts again. Called by
    everything that posts or edits it. Only the running session's table of that campaign counts,
    so one campaign's clock never delays another's."""
    table = getattr(client, "tables", {}).get(campaign.guild_id)
    if table is not None and table.campaign_id == campaign.id:
        table.clock_shown_at = time.monotonic()


async def tick(bot: Any, table: Any, now: float | None = None) -> bool:
    """One look: say where we are if it is time. True when a line was posted."""
    now = time.monotonic() if now is None else now
    if not table.listening or table.campaign_id is None or bot.clocks is None:
        return False
    if not due(table.screen_level, table.clock_shown_at, now):
        return False
    stored = await bot.clocks.get(table.guild_id, table.campaign_id)
    if stored is None or in_combat(stored):
        return False
    campaign = await bot.campaigns.get(table.guild_id, table.campaign_id)
    if campaign is None or campaign.dm_screen_channel_id is None:
        return False
    from dmbot.dm_screen import clock as clock_screen

    await clock_screen.say(bot, campaign, [line(stored)])  # (saying it counts as shown)
    return True


async def run(bot: Any, table: Any) -> None:
    """The loop for one session: look every minute; ends with the session."""
    while bot.tables.get(table.guild_id) is table:
        await asyncio.sleep(TICK_S)
        try:
            await tick(bot, table)
        except Exception as exc:  # never the text
            log.error("Where are we: a pass failed (%s)", type(exc).__name__)
