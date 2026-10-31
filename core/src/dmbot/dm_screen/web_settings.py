"""The bot's side of the website's Settings page (#2463): what a DM chose there, applied here.

The website has no right on the campaign tables, so a choice (who can see the DM screen, how much
DMbot says, how long transcripts are kept) is queued as a row (`setting_actions`). This module
claims the rows one at a time in the order made and applies each exactly as Discord's ⚙️ Manage DMbot
buttons do: the same store calls, so channel permissions, the notice in the DM screen and a running
session all follow, and the DM is told in the same words. Not here: hand over, pause and delete (the
page sends the DM to Discord for those). A game master added or removed (#2776) goes through the
same store calls and the same follow-up as Discord's 👑 Game masters.

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
from dmbot.campaigns.models import DM_SCREEN_LEVELS_OFFERED, STORY_MODES
from dmbot.campaigns.store import OWNER_STAYS
from dmbot.db import Database
from dmbot.dm_screen import game_masters, messages
from dmbot.dm_screen.channel import DMScreenError
from dmbot.dm_screen.transcript_keep import CHOICES, _shorter, days_of, saved_text
from dmbot.rules import optional
from dmbot.rules.optional import NO_SCREEN_NOTE, OptionalRule

log = logging.getLogger(__name__)

SETTINGS_PER_ROUND = 10  # choices applied for one server in one round
RETAKE_AFTER_S = 120  # a claim this old belongs to a bot that stopped
KEEP_FOR_S = 3600  # nothing waits on a row longer than this

VISIBILITY, LEVEL, RETENTION, OPTIONAL_RULE = "visibility", "level", "retention", "optional_rule"
CO_DM = "co_dm"  # a game master added or removed (#2776)
STORY_MODE = "story_mode"  # the Shared story switch (#3078)
RULESETS = "rulesets"  # the main rules and what to use if they miss, "2024/2014" (#3112)
PERSON_ID_MAX = 19  # digits in a Discord id
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
class Member:
    """A person of the server, as far as a game master change needs to know."""

    name: str
    bot: bool


@dataclass(frozen=True, slots=True)
class CoDmDone:
    """What the bot did after saving a game master change: whether the one private message
    reached a new game master (always True for a removal), and Discord's `ACCESS_LATE` words when
    the DM screen's permissions could not be updated yet ("" when they were)."""

    told: bool = True
    late: str = ""


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

    async def apply_story_mode(
        self, campaign: Campaign, mode: str, confirmed_by: int | None
    ) -> Campaign:
        """Save the Shared story switch with the store's own rules (#3078): turning it on needs
        the DM's confirmation on record or `confirmed_by` now. Raises `CampaignError` with the
        words Discord shows."""
        ...

    async def apply_rulesets(self, campaign: Campaign, main: str, fallback: str) -> Campaign:
        """Save the main rules and the fallback with the store's own check (`check_rulesets`).
        Raises `CampaignError` with the store's words."""
        ...

    async def find_member(self, guild_id: int, user_id: int) -> Member | None:
        """The person in the server, or None when Discord says they are not in it. Raises when
        Discord cannot be asked: that is a failed try, never "they left"."""
        ...

    async def apply_co_dm(
        self, campaign: Campaign, user_id: int, add: bool, asker_id: int
    ) -> CoDmDone:
        """Save the change with the store's own rules (raises `CampaignError` with Discord's
        words), then do what Discord's menu does: the running session takes the new set, the
        DM screen's permissions are applied again, and a new game master
        gets the one private message. The caller has checked an added person is in the server and
        not a bot. Nothing here touches anyone's consent."""
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
    if item.setting == CO_DM:
        return await _co_dm(host, campaign, item)
    if item.setting == STORY_MODE:
        return await _story_mode(host, campaign, item)
    if item.setting == RULESETS:
        return await _rulesets(host, campaign, item)
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


async def _story_mode(host: Host, campaign: Campaign, item: Setting) -> Outcome:
    """The Shared story switch (#3078), by the rules of Discord's. Turning it on needs the DM's
    confirmation of the right to use the material (CLAUDE.md, IP rule): the website sends `flag`
    when the DM ticked it, and the store records who and when. Without one on record or given
    now the store refuses and its words go to the page. Off never asks."""
    if item.value not in STORY_MODES:
        return Outcome(BAD_VALUE)
    try:
        await host.apply_story_mode(campaign, item.value, item.user_id if item.flag else None)
    except CampaignError as exc:
        return Outcome(REFUSED, str(exc))
    return Outcome()


async def _rulesets(host: Host, campaign: Campaign, item: Setting) -> Outcome:
    """The main rules and the fallback (#3112), as "<main>/<fallback>". The store checks the pair
    again (both words offered, the fallback not the main rules) and its words go to the page.
    Everything built on the rules (optional rules, lookups) reads the campaign's saved pair each
    time, so saving it is the whole change, as in Discord. Applying the same pair twice is the
    same save."""
    if item.value.count("/") != 1:
        return Outcome(BAD_VALUE)
    main, fallback = item.value.split("/")
    try:
        await host.apply_rulesets(campaign, main, fallback)
    except CampaignError as exc:
        return Outcome(REFUSED, str(exc))
    return Outcome()


async def _co_dm(host: Host, campaign: Campaign, item: Setting) -> Outcome:
    """A game master added or removed (#2776), by the rules of Discord's 👑 Game masters. Their
    words are reused, not copied: the store's constants and `game_masters`'. Checked again now:
    the asker is a DM (by `apply`), an added person is in the server and not a bot. Only ids are
    logged, never in a channel."""
    if (
        item.turn_on is None
        or not item.value.isascii()
        or not item.value.isdigit()
        or len(item.value) > PERSON_ID_MAX
    ):
        return Outcome(BAD_VALUE)
    person, add = int(item.value), item.turn_on
    if person < 1 or person >= 2**63:  # (the database's id range)
        return Outcome(BAD_VALUE)
    guild_id = campaign.guild_id
    try:
        member = await host.find_member(guild_id, person)
    except Exception as exc:
        if add:
            raise  # Discord could not be asked: the choice can be sent again
        log.info("A game master's name could not be looked up: %s", type(exc).__name__)
        member = None
    # The page shows these words as plain text, so names go in raw, not escaped for Discord.
    if add:
        if member is None:
            return Outcome(REFUSED, game_masters.NOT_IN_SERVER)
        if member.bot:
            return Outcome(REFUSED, game_masters.NOT_A_PERSON)
        if person in campaign.dm_user_ids:
            return Outcome(REFUSED, game_masters.ALREADY.format(name=member.name))
    else:
        if person not in campaign.dm_user_ids:
            return Outcome(REFUSED, game_masters.GONE_FROM_LIST)
    name = member.name if member is not None else game_masters.LEFT  # (a removal; an add has one)
    try:
        done = await host.apply_co_dm(campaign, person, add, item.user_id)
    except CampaignError as exc:
        if str(exc) == OWNER_STAYS:
            words = (
                game_masters.PLAN_HOLDER_SELF
                if person == item.user_id
                else game_masters.PLAN_HOLDER_OTHER.format(name=name)
            )
            return Outcome(REFUSED, words)
        return Outcome(REFUSED, str(exc))  # (the limit, the last game master, a campaign gone)
    if member is None:  # a removal and no name to give: the page uses its own
        return Outcome()
    first = (
        f"**{name}** is now a game master." if add else f"**{name}** is no longer a game master."
    )
    extra = ("" if done.told else game_masters.NOT_TOLD) + done.late
    return Outcome(note=(first + extra.replace("\n", " ")).strip())


async def run_one(host: Host, store: SettingStore, guild_id: int, item: Setting) -> bool:
    """Apply one claimed choice and mark it. A bug never leaves the row "working": it ends as
    `failed` for the page to offer a retry. False when the row could not be marked (the loop
    stops; the row is taken again later and `apply` sees it already done: for a game master
    change that reads as a refusal, and the change itself stays saved). Only the outcome word is
    logged, never the choice."""
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
