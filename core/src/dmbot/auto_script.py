"""A script for every session, made by itself (#3901; docs/PLAN.md, "A script for every session").

With `DMBOT_THEATRE_ON` on and a plan with story memory, a session that has ended gets its script
without a question: a live one a couple of minutes after it ended (its transcript is final), an
imported one once its name scan has finished. It is the same job the button ran
(`transcript.theatre_job` through `ui.theatre_button.make_for`), so the consent re-check, the
"what the party knows" rule, the one-run-at-a-time row and DM-only reading are unchanged.

It is a round, not an event: a restart loses nothing, because "no run row yet" is the question each
round asks of the database. A failed run is not tried again by itself (the website's button makes
it again). Sessions that ended more than a week ago are left to that button too. The DM Bot
channel gets one counts-only line when a script is made or cannot be. Nothing from a session is
logged.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from dmbot.logs import log_context
from dmbot.transcript.theatre_runs import TheatreRuns
from dmbot.ui import theatre_button

log = logging.getLogger(__name__)

SETTLE_S = 120  # a live session's transcript is final this long after it ended
SINCE_S = 7 * 24 * 3600  # older sessions are made from the website
SEEN = 50  # candidates read each round (some may be for campaigns whose plan has no scripts)
PER_ROUND = 2  # scripts begun each round, so one server never takes the AI all at once
LINE_FAILED = "📜 A script couldn't be made for session {number}: {why} Make it from the website."


async def run_round(bot: Any, guild_id: int, now: int | None = None) -> int:
    """Begin the scripts that are due on one server; returns how many were made or tried."""
    if (
        not bot.settings.theatre_on
        or bot.theatre_ai is None
        or bot.memory is None
        or bot.transcripts is None
    ):
        return 0
    when = int(time.time()) if now is None else now
    runs = TheatreRuns(bot.campaigns.db)
    due = await runs.sessions_without_script(
        guild_id, since=when - SINCE_S, settled_before=when - SETTLE_S, limit=SEEN
    )
    tried = 0
    for campaign_id, session_id in due:
        if tried >= PER_ROUND:
            break
        with log_context(guild_id=guild_id, campaign_id=campaign_id):
            try:
                tried += await _one(bot, guild_id, campaign_id, session_id)
            except Exception:
                log.exception("A script made by itself failed")
    return tried


async def _one(bot: Any, guild_id: int, campaign_id: str, session_id: str) -> int:
    """One session: 1 when a run was begun, 0 when this campaign's plan has no scripts (the
    session is looked at again next round, in case the plan changes)."""
    campaign = await bot.campaigns.get(guild_id, campaign_id)
    session = await bot.transcripts.session(guild_id, session_id)
    if campaign is None or session is None or session.lines == 0:
        return 0
    if not await bot.plan_allows("story", guild_id, campaign, fail_open=False):
        return 0

    async def quiet(_text: str) -> None:
        """The job's progress is its row (stage, part n of m, time), which the website shows; no
        private card is kept for a script nobody asked for."""

    outcome = await theatre_button.make_for(bot, guild_id, session_id, session, campaign, quiet)
    if not outcome.started or outcome.taken_over:
        return 1  # (another run has it)
    await _tell(bot, campaign, session.number, outcome)
    log.info("A script was made by itself: %s", "ok" if outcome.ok else "not ok")
    return 1


async def _tell(bot: Any, campaign: Any, number: int, outcome: Any) -> None:
    """One line in the DM Bot channel (where upkeep is told, not the DM screen): the job's own
    words and, when it worked, the link to the Script tab. Never raises."""
    channel_id = getattr(campaign, "dm_bot_channel_id", None)
    if not channel_id:
        return
    text = outcome.text if outcome.ok else LINE_FAILED.format(number=number, why=outcome.text)
    try:
        await bot.post_message(channel_id, text)
    except Exception:
        log.warning("The script line could not be posted")
