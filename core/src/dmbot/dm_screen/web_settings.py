"""The bot's side of the website's Settings page (#2463): what a DM chose there, applied here.

The website has no right on the campaign tables, so a choice (who can see the DM screen, how much
DMbot says, how long transcripts are kept) is queued as a row (`setting_actions`). This module
claims the rows one at a time in the order made and applies each exactly as Discord's ⚙️ Settings
buttons do: the same store calls, so channel permissions, the notice in the DM screen and a running
session all follow, and the DM is told in the same words. Not here: game masters, hand over, pause
and delete (the page sends the DM to Discord for those).

Checked again at the moment of use, because the queue-time check can be stale: the sender must still
be a DM of that campaign (Discord lets a server manager change visibility too; the website asks
for a DM only, which is the narrower rule). A shorter transcript limit deletes old sessions every
night, so it needs the DM's yes with the choice (`flag`), as Discord asks before saving it. The
choice itself is never logged, only the outcome word.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from dmbot.campaigns import Campaign, CampaignError
from dmbot.campaigns.models import DM_SCREEN_LEVELS_OFFERED
from dmbot.db import Database
from dmbot.dm_screen import messages
from dmbot.dm_screen.channel import DMScreenError
from dmbot.dm_screen.transcript_keep import CHOICES, _shorter, days_of, saved_text
from dmbot.rules import optional
from dmbot.rules.optional import NO_SCREEN_NOTE, OptionalRule

log = logging.getLogger(__name__)

SETTINGS_PER_ROUND = 10  # choices applied for one server in one round
RETAKE_AFTER_S = 120  # a claim this old belongs to a bot that stopped
KEEP_FOR_S = 3600  # nothing waits on a row longer than this

VISIBILITY, LEVEL, RETENTION, OPTIONAL_RULE = "visibility", "level", "retention", "optional_rule"
VISIBILITIES = ("private", "peek", "open")
RETENTION_CHOICES = tuple(choice for choice, _days in CHOICES)

# What a failed choice says, in words the page can turn into plain ones.
NOT_ALLOWED = "not_allowed"  # the sender is not a DM of the campaign any more
NOT_FOUND = "not_found"  # the campaign is gone
BAD_VALUE = "bad_value"  # a choice Discord does not offer
NEEDS_CONFIRM = "needs_confirm"  # a shorter transcript limit without the DM's yes
REFUSED = "refused"  # the rules refused it; `note` has the words Discord would show
FAILED = "failed"  # anything unexpected; the choice can be sent again


@dataclass(frozen=True, slots=True)
class Setting:
    id: str
    campaign_id: str
    user_id: int
    setting: str
    value: str
    flag: bool = False
    claimed_at: int = 0  # this worker's claim: only it may finish the row
    turn_on: bool | None = None  # an optional rule: on or off; None for every other setting


@dataclass(frozen=True, slots=True)
class Outcome:
    error: str = ""
    note: str = ""  # what the DM sees on the page afterwards


class Host(Protocol):
    """What applying a choice needs from the running bot, so tests can stand in for Discord."""

    async def get_campaign(self, guild_id: int, campaign_id: str) -> Campaign | None: ...

    async def apply_visibility(self, campaign: Campaign, visibility: str) -> str:
        """Save and apply who can see the DM screen, as the Discord button does. The notice
        for the DM. Raises `DMScreenError` with words the DM can read."""
        ...

    async def apply_level(self, campaign: Campaign, level: str) -> Campaign: ...

    async def apply_retention(self, campaign: Campaign, days: int | None) -> Campaign: ...

    async def apply_optional_rule(
        self, campaign: Campaign, rule: OptionalRule, turn_on: bool, sender_id: int
    ) -> bool:
        """Save an optional rule on or off and note it in the DM screen, as `/dmbot optionalrules`
        does. True when the DM screen has its line (or the campaign has none); False when the line
        could not be posted: the rule is saved either way. Raises `CampaignError` when the
        campaign is gone."""
        ...


class SettingStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Setting | None:
        """The oldest waiting choice (or a stalled one) of this server, marked as being worked
        on, never past an earlier open one of the same campaign: the order made is the order
        done, so Private then Open never run backwards."""
        now = int(self._now() * 1000)  # milliseconds, so two claims of one row never match
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE setting_actions SET state = 'working', claimed_at = %s"
                " WHERE (guild_id, id) = (SELECT guild_id, id FROM setting_actions"
                " WHERE guild_id = %s AND (state = 'waiting' OR (state = 'working'"
                " AND claimed_at < %s))"
                " AND NOT EXISTS (SELECT 1 FROM setting_actions p WHERE p.guild_id = %s"
                " AND p.campaign_id = setting_actions.campaign_id AND p.seq < setting_actions.seq"
                " AND (p.state = 'waiting' OR (p.state = 'working' AND p.claimed_at >= %s)))"
                " ORDER BY seq LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING id, campaign_id, user_id, setting, value, flag, turn_on",
                (now, guild_id, now - RETAKE_AFTER_S * 1000, guild_id, now - RETAKE_AFTER_S * 1000),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Setting(
            row["id"],
            row["campaign_id"],
            int(row["user_id"]),
            row["setting"],
            row["value"],
            bool(row["flag"]),
            now,
            None if row["turn_on"] is None else bool(row["turn_on"]),
        )

    async def finish(self, guild_id: int, item: Setting, outcome: Outcome) -> bool:
        """Done, or failed with why, for the page to read. Only the worker that holds the current
        claim can write. False: not its row any more."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE setting_actions SET state = %s, error = %s, note = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (
                    "failed" if outcome.error else "done",
                    outcome.error,
                    outcome.note[:1000],
                    guild_id,
                    item.id,
                    item.claimed_at,
                ),
            )
            return bool(cur.rowcount)

    async def sweep(self, guild_id: int) -> int:
        """Delete choices older than `KEEP_FOR_S`, whatever their state."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM setting_actions WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


async def apply(host: Host, guild_id: int, item: Setting) -> Outcome:
    """Apply one queued choice the way Discord's button would. Never raises for a refusal: the
    outcome carries the word (and, for a rule the DM can fix, Discord's own words)."""
    campaign = await host.get_campaign(guild_id, item.campaign_id)
    if campaign is None:
        return Outcome(NOT_FOUND)
    if item.user_id not in campaign.dm_user_ids:  # checked again: they may have handed over since
        return Outcome(NOT_ALLOWED)
    if item.setting == OPTIONAL_RULE:
        return await _optional_rule(host, campaign, item)
    try:
        if item.setting == VISIBILITY:
            if item.value not in VISIBILITIES:
                return Outcome(BAD_VALUE)
            # Always applied, even when it is the saved value: Discord's button does too, which
            # repairs a channel whose permissions drifted, and a retry after a half-done try
            # still catches the live session up.
            return Outcome(note=await host.apply_visibility(campaign, item.value))
        if item.setting == LEVEL:
            if item.value not in DM_SCREEN_LEVELS_OFFERED:
                return Outcome(BAD_VALUE)
            if item.value != campaign.dm_screen_level:
                await host.apply_level(campaign, item.value)
            return Outcome(note=messages.level_changed(item.value))  # (the line the screen gets)
        if item.setting == RETENTION:
            if item.value not in RETENTION_CHOICES:
                return Outcome(BAD_VALUE)
            days = days_of(item.value)
            if days != campaign.transcript_keep_days:
                if _shorter(days, campaign.transcript_keep_days) and not item.flag:
                    return Outcome(NEEDS_CONFIRM)  # deleted transcripts can't come back
                await host.apply_retention(campaign, days)
            return Outcome(note=saved_text(days))
    except (CampaignError, DMScreenError) as exc:
        return Outcome(REFUSED, str(exc))  # words written for the DM, as Discord shows them
    return Outcome(BAD_VALUE)


async def _optional_rule(host: Host, campaign: Campaign, item: Setting) -> Outcome:
    """An optional rule on or off (#2755), as Discord's menu does it. The website checked the id is
    in the catalog; whether it applies to THIS campaign's main rules is checked here, because the
    list can change under a page that stayed open (the page says to reload). What a campaign
    follows in a shared ruleset is not looked at, as in Discord."""
    chosen = optional.rule(item.value)
    if (
        item.turn_on is None
        or chosen is None
        or chosen not in optional.applying(campaign.target_ruleset)
    ):
        return Outcome(BAD_VALUE)
    try:
        noted = await host.apply_optional_rule(campaign, chosen, item.turn_on, item.user_id)
    except CampaignError:
        return Outcome(NOT_FOUND)  # the campaign went while the choice was being saved
    note = f"{chosen.name} is now {'on' if item.turn_on else 'off'}."
    return Outcome(note=note if noted else f"{note} {NO_SCREEN_NOTE}")


async def run_one(host: Host, store: SettingStore, guild_id: int, item: Setting) -> bool:
    """Apply one claimed choice and mark it. A bug never leaves the row "working": it ends as
    `failed` for the page to offer a retry. False when the row could not be marked (the loop
    stops; the row is taken again later and `apply` sees it already done). Only the outcome word
    is logged, never the choice."""
    try:
        done = await apply(host, guild_id, item)
    except Exception as exc:
        log.warning("A website setting failed: %s", type(exc).__name__)
        done = Outcome(FAILED)
    try:
        if not await store.finish(guild_id, item, done):
            log.info("A website setting was taken over by another worker")
    except Exception as exc:
        log.warning("A website setting could not be marked: %s", type(exc).__name__)
        return False
    log.info("Website setting %s: %s", item.setting, done.error or "done")
    return True
