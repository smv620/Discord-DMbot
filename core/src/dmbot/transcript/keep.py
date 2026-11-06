"""Keep transcripts for (#1533; docs/PLAN.md, "Retention"): the part of the daily retention job
that deletes the transcripts of a campaign whose DM set a limit (30 days, 90 days or 1 year;
the default, forever, is today's behaviour).

For each campaign with a limit it deletes, oldest first, every session that **ended** more than
that long ago: the lines (both versions), the fix suggestions and the story-memory mentions of
those lines, one transaction per session (`TranscriptStore.delete_session_transcript`), so one
failing session rolls back only itself and the rest still go. A session still running, or saved
without an end, is never touched. The session's row stays with its number, so session numbers
never shift; recaps and the facts the DM confirmed are not touched. A night the job missed is
caught up by the next run, which deletes everything past the limit. Nothing crosses campaigns or
servers: every call names one server and one campaign.

The DM screen says so once (the first night something is deleted; changing the choice lets it
say so again). Players are told nothing. Counts only go in the log.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from dmbot.campaigns import Campaign

log = logging.getLogger(__name__)

DAY = 24 * 3600
KEEP_LABELS = {365: "1 year", 90: "90 days", 30: "30 days"}
MAX_SESSIONS_PER_RUN = 200  # per campaign; the rest wait for tomorrow, so one night stays short

PostScreen = Callable[[Campaign, str], Awaitable[bool]]


@dataclass(slots=True)
class Result:
    campaigns: int = 0  # with a limit set
    sessions: int = 0  # transcripts deleted
    lines: int = 0
    failed: int = 0  # sessions that rolled back (tried again tomorrow)


def note_text(sessions: int, days: int) -> str:
    """What the DM screen says once: "🗑 Deleted 3 transcripts older than 90 days. …"."""
    things = "1 transcript" if sessions == 1 else f"{sessions} transcripts"
    age = KEEP_LABELS.get(days, f"{days} days")
    return (
        f"🗑 Deleted {things} older than {age}. Change this under "
        "⚙️ Manage DMbot → 🗂️ Campaign management → 🗄️ Transcript retention."
    )


class KeepJob:
    def __init__(
        self,
        *,
        campaigns: Any,
        transcripts: Any,
        guild_ids: Callable[[], Awaitable[list[int]]],
        post_screen: PostScreen,
    ) -> None:
        """`campaigns`: the CampaignStore; `transcripts`: the TranscriptStore; `guild_ids()`:
        every server that holds campaigns; `post_screen(campaign, text)`: say it on the DM screen
        (False if it could not be posted)."""
        self._campaigns = campaigns
        self._transcripts = transcripts
        self._guild_ids = guild_ids
        self._post_screen = post_screen

    async def run_once(self, now: int) -> Result:
        """Safe to run twice: a session already done is skipped."""
        result = Result()
        for guild_id in await self._guild_ids():
            for campaign in await self._campaigns.list_campaigns(guild_id):
                days = campaign.transcript_keep_days
                if days is None:
                    continue
                result.campaigns += 1
                try:
                    await self._one(campaign, days, now, result)
                except Exception:  # one campaign's failure never stops the others
                    result.failed += 1
                    log.exception("Keep transcripts: a campaign failed; trying again tomorrow")
        if result.campaigns:
            log.info(
                "Keep transcripts: %d campaigns with a limit, %d transcripts deleted "
                "(%d lines), %d failed",
                result.campaigns,
                result.sessions,
                result.lines,
                result.failed,
            )
        return result

    async def _one(self, campaign: Campaign, days: int, now: int, result: Result) -> None:
        cutoff = now - days * DAY
        due = await self._transcripts.expired_sessions(campaign.guild_id, campaign.id, cutoff)
        deleted = 0
        for session_id in due[:MAX_SESSIONS_PER_RUN]:
            try:
                lines = await self._transcripts.delete_session_transcript(
                    campaign.guild_id, campaign.id, session_id, cutoff, now
                )
            except Exception:  # this session rolled back by itself; the others go on
                result.failed += 1
                log.exception("Keep transcripts: one session failed; trying again tomorrow")
                continue
            deleted += 1
            result.sessions += 1
            result.lines += lines
        if not deleted or campaign.transcript_deleted_noted:
            return
        # Post first and mark only when it went out: a post that fails is tried again the next
        # time something is deleted, rather than the note being lost.
        if await self._post_screen(campaign, note_text(deleted, days)):
            await self._campaigns.mark_transcript_deletion_noted(campaign.guild_id, campaign.id)
        else:
            log.warning("Keep transcripts: couldn't post the DM-screen note")
