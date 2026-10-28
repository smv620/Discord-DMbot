"""The bot's side of the website's rule sharing (#2793; the page is #2783): what a DM chose there,
applied here.

The website has no right on the rule tables, so a choice (share my house rules, stop sharing, look a
code up, follow a set, stop following) is queued as a row (`web_rule_actions`). This module claims
the rows in the order made, one at a time for a server, and applies each with the same
`RuleSetStore`
calls and the same checks as Discord's ⚙️ Settings → 📚 Shared rules buttons, so the rules channel
note (`@notifies`), the borrowed definitions and the guess limit all follow, and the DM is told in
Discord's own words.

Checked again at the moment of use, because the queue-time check can be stale: the asker must still
be a DM of the campaign, and the campaign owner's plan must allow sharing (following is free). Share
codes are never made, turned off or shown from here (they stay in Discord, #2460), and no
relationship definitions travel with a publish from the website for now.

Idempotent, because the website's door puts a choice that has been "working" for ten minutes back to
"waiting": a retaken share, stop, follow or unfollow that finds its work already done ends `done`.
What the DM sees in `note` is Discord's sentence or the set's name, publisher and rule count: never
a rule's words, a code or a set's content, and the log has the outcome word only.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import discord

from dmbot.campaigns import Campaign
from dmbot.db import Database
from dmbot.rules import house, rule_sets
from dmbot.rules.house import HouseRule, HouseRuleError
from dmbot.rules.rule_sets import RuleSetError

log = logging.getLogger(__name__)

RETAKE_AFTER_S = 600  # a claim this old belongs to a bot that stopped: the door's own limit
KEEP_FOR_S = 86400  # as long as the website reads an answer back

PUBLISH, STOP_SHARING, LOOK_CODE = "publish", "stop_sharing", "look_code"
FOLLOW_HERE, FOLLOW_CODE, UNFOLLOW = "follow_here", "follow_code", "unfollow"

# What a failed choice says, in the hyphenated words the page keys its plain ones by (the door
# passes `error` on as it is). The Discord sentence behind it is in `note`.
NOT_ALLOWED = "not-allowed"  # the asker is not a DM of the campaign any more
NOT_FOUND = "not-found"  # the campaign is gone
PLAN_NEEDED = "plan-needed"  # the owner's plan has no sharing
NO_RULES = "no-rules"  # nothing to share yet
BAD_CHOICE = "bad-choice"  # a code or set that doesn't match, or a name that is not one
TOO_SOON = "too-soon"  # too many wrong codes
FAILED = "failed"  # the rules refused it (the sentence is in `note`), or anything unexpected
SERVER = "server"  # something broke; the choice can be sent again

ONLY_DMS = "Only this campaign's DMs can share house rules. Ask your DM."
GONE = "That campaign isn't here any more."
COULDNT = "That didn't work. Please try again in a moment."

# Discord's refusals, as the page's words.
_WORDS = {
    rule_sets.NO_RULES: NO_RULES,
    rule_sets.NEED_NAME: BAD_CHOICE,
    rule_sets.BAD_CODE: BAD_CHOICE,
    rule_sets.NO_SUCH_SET: BAD_CHOICE,
    rule_sets.TOO_MANY: TOO_SOON,
}


@dataclass(frozen=True, slots=True)
class Action:
    id: str
    campaign_id: str
    user_id: int
    action: str
    arg: str
    claimed_at: int = 0  # this worker's claim: only it may finish the row
    retried: bool = False  # the door put it back after a slow try: the first try may have done it


@dataclass(frozen=True, slots=True)
class Outcome:
    error: str = ""
    note: str = ""  # what the DM sees on the page afterwards


class Host(Protocol):
    """What applying a choice needs from the running bot, so tests can stand in for it."""

    rule_sets: Any  # the RuleSetStore

    async def get_campaign(self, guild_id: int, campaign_id: str) -> Campaign | None: ...

    async def share_refusal(self, campaign: Campaign, user_id: int) -> str | None:
        """Why the campaign's plan may not share house rules, in Discord's words, or None."""
        ...

    async def own_rules(self, campaign: Campaign) -> tuple[list[HouseRule], str | None]:
        """The campaign's house rules and its rules document link, as Discord's Share reads them."""
        ...

    async def settle(self, campaign: Campaign, *, following: bool) -> str:
        """After a follow or an unfollow: the borrowed definitions match the set again, as
        Discord does it. The sentence to add to the DM's note ("" when nothing to say). Never
        raises: the follow already happened."""
        ...


class ActionStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def claim_next(self, guild_id: int) -> Action | None:
        """The oldest waiting choice (or a stalled one) of this server, marked as being worked on,
        never past an earlier open one of the same campaign: the order made is the order done, so
        Share then Stop never run backwards."""
        now_ms = int(self._now() * 1000)  # milliseconds, so two claims of one row never match
        now = int(self._now())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "WITH pick AS (SELECT guild_id, id,"
                " (updated_at > created_at OR state = 'working') AS retried"
                " FROM web_rule_actions WHERE guild_id = %s AND (state = 'waiting'"
                "  OR (state = 'working' AND claimed_at < %s))"
                " AND NOT EXISTS (SELECT 1 FROM web_rule_actions p WHERE p.guild_id = %s"
                "  AND p.campaign_id = web_rule_actions.campaign_id"
                "  AND p.seq < web_rule_actions.seq"
                "  AND (p.state = 'waiting' OR (p.state = 'working' AND p.claimed_at >= %s)))"
                " ORDER BY seq LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " UPDATE web_rule_actions a SET state = 'working', stage = 'sharing',"
                " claimed_at = %s, updated_at = %s FROM pick"
                " WHERE a.guild_id = pick.guild_id AND a.id = pick.id"
                " RETURNING a.id, a.campaign_id, a.user_id, a.action, a.arg, pick.retried",
                (
                    guild_id,
                    now_ms - RETAKE_AFTER_S * 1000,
                    guild_id,
                    now_ms - RETAKE_AFTER_S * 1000,
                    now_ms,
                    now,
                ),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Action(
            row["id"],
            row["campaign_id"],
            int(row["user_id"]),
            row["action"],
            row["arg"],
            now_ms,
            bool(row["retried"]),
        )

    async def touch(self, guild_id: int, item: Action) -> bool:
        """The choice is still moving: the page's "no news" line and the door's ten-minute
        put-back both read `updated_at`. Only the claiming worker."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE web_rule_actions SET updated_at = %s"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (int(self._now()), guild_id, item.id, item.claimed_at),
            )
            return bool(cur.rowcount)

    async def finish(self, guild_id: int, item: Action, outcome: Outcome) -> bool:
        """Done, or failed with why, for the page to read. Only the worker that holds the current
        claim can write. False: not its row any more."""
        state = "failed" if outcome.error else "done"
        async with self._db.guild(guild_id) as conn:
            # A share code in `arg` is blanked as the row ends (kept until then, so a retake can
            # run again): a code does not sit in the table for the day the row lives.
            cur = await conn.execute(
                "UPDATE web_rule_actions SET state = %s, stage = %s, error = %s, note = %s,"
                " updated_at = %s,"
                " arg = CASE WHEN action IN ('look_code', 'follow_code') THEN '' ELSE arg END"
                " WHERE guild_id = %s AND id = %s AND state = 'working' AND claimed_at = %s",
                (
                    state,
                    state,
                    outcome.error[:40],
                    outcome.note[:1000],
                    int(self._now()),
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
                "DELETE FROM web_rule_actions WHERE guild_id = %s AND created_at < %s",
                (guild_id, int(self._now()) - KEEP_FOR_S),
            )
            return int(cur.rowcount)


def _rules(n: int) -> str:
    return f"{n} house rule" + ("" if n == 1 else "s")


def _count(content: dict[str, Any]) -> int:
    rules = content.get("rules")
    return len(rules) if isinstance(rules, list) else 0


def _refused(exc: RuleSetError) -> Outcome:
    """A refusal of the rules: Discord's sentence in `note`, and the page's word for it."""
    message = str(exc)
    return Outcome(_WORDS.get(message, FAILED), message)


async def apply(
    host: Host,
    guild_id: int,
    item: Action,
    store: ActionStore | None = None,
) -> Outcome:
    """Apply one queued choice the way Discord's button would. Never raises for a refusal: the
    outcome carries the word and Discord's sentence."""
    campaign = await host.get_campaign(guild_id, item.campaign_id)
    if campaign is None:
        return Outcome(NOT_FOUND, GONE)
    if item.user_id not in campaign.dm_user_ids:  # checked again: they may have handed over
        return Outcome(NOT_ALLOWED, ONLY_DMS)
    sets: rule_sets.RuleSetStore = host.rule_sets
    try:
        if item.action == PUBLISH:
            return await _publish(host, sets, guild_id, campaign, item)
        if item.action == STOP_SHARING:
            return await _stop(sets, guild_id, campaign, item)
        if item.action == LOOK_CODE:
            return await _look(sets, guild_id, item)
        if item.action in (FOLLOW_CODE, FOLLOW_HERE):
            if store is not None:
                await store.touch(guild_id, item)
            return await _follow(host, sets, guild_id, campaign, item)
        if item.action == UNFOLLOW:
            return await _unfollow(host, sets, guild_id, campaign, item)
    except RuleSetError as exc:
        return _refused(exc)
    except HouseRuleError as exc:  # the DM was removed, or the campaign deleted, since the check
        gone = str(exc) == house.NO_CAMPAIGN
        return Outcome(NOT_FOUND if gone else NOT_ALLOWED, str(exc))
    return Outcome(SERVER, COULDNT)


async def _publish(
    host: Host, sets: rule_sets.RuleSetStore, guild_id: int, campaign: Campaign, item: Action
) -> Outcome:
    refused = await host.share_refusal(campaign, item.user_id)
    if refused is not None:
        return Outcome(PLAN_NEEDED, refused)
    rules, link = await host.own_rules(campaign)
    try:
        shared = await sets.publish(guild_id, campaign.id, item.user_id, item.arg, rules, link)
    except RuleSetError as exc:
        if str(exc) == rule_sets.ALREADY_SHARED and item.retried:
            mine = await sets.published(guild_id, campaign.id)  # the first try did it
            if mine is not None and mine.shared:
                return Outcome(note=f"✅ Sharing **{_escape(mine.name)}**.")
        raise
    return Outcome(note=f"✅ Sharing **{_escape(shared.name)}**.")


async def _stop(
    sets: rule_sets.RuleSetStore, guild_id: int, campaign: Campaign, item: Action
) -> Outcome:
    had = await sets.stop_sharing(guild_id, campaign.id, item.user_id)
    if had or item.retried:  # a retaken stop that finds nothing shared: the first try did it
        return Outcome(
            note="Done. Your rules aren't shared now and their codes stopped working. "
            "Tables using them keep their copy."
        )
    return Outcome(FAILED, rule_sets.NOT_SHARED)


async def _look(sets: rule_sets.RuleSetStore, guild_id: int, item: Action) -> Outcome:
    """What a code opens, before anything is copied: the set's name, its publisher's name and how
    many rules it would copy. A wrong guess counts toward Discord's limit."""
    offer = await sets.by_code(guild_id, item.user_id, item.arg)
    if offer.kind != rule_sets.HOUSE_RULES:  # a homebrew pack's code
        return Outcome(FAILED, rule_sets.WRONG_KIND)
    return Outcome(
        note=f"**{_escape(offer.name)}** from {_escape(offer.publisher)}: "
        f"{_rules(_count(offer.content))} (version {offer.version})."
    )


async def _follow(
    host: Host,
    sets: rule_sets.RuleSetStore,
    guild_id: int,
    campaign: Campaign,
    item: Action,
) -> Outcome:
    if item.action == FOLLOW_CODE:
        offer = await sets.by_code(guild_id, item.user_id, item.arg)
    else:
        found = [o for o in await sets.shared_here(guild_id, campaign.id) if o.set_id == item.arg]
        if not found:
            if item.retried and await _follows(sets, guild_id, campaign, item.arg):
                return await _followed(host, campaign, None)
            raise RuleSetError(rule_sets.NO_SUCH_SET)
        offer = found[0]
    if offer.kind != rule_sets.HOUSE_RULES:
        return Outcome(FAILED, rule_sets.WRONG_KIND)
    try:
        follow = await sets.follow(guild_id, campaign.id, item.user_id, offer)
    except RuleSetError as exc:
        first_try_did_it = (
            str(exc) == rule_sets.ALREADY_FOLLOWING
            and item.retried
            and await _follows(sets, guild_id, campaign, offer.set_id)
        )
        if first_try_did_it:
            return await _followed(host, campaign, offer.name)
        raise
    return await _followed(host, campaign, follow.name)


async def _follows(
    sets: rule_sets.RuleSetStore, guild_id: int, campaign: Campaign, set_id: str
) -> bool:
    return any(
        f.set_id == set_id and f.kind == rule_sets.HOUSE_RULES
        for f in await sets.following(guild_id, campaign.id)
    )


async def _followed(host: Host, campaign: Campaign, name: str | None) -> Outcome:
    extra = await host.settle(campaign, following=True)
    shown = f" **{_escape(name)}**" if name else " those rules"
    return Outcome(note=f"✅ Now using{shown}. DMbot checks it along with your own rules." + extra)


async def _unfollow(
    host: Host, sets: rule_sets.RuleSetStore, guild_id: int, campaign: Campaign, item: Action
) -> Outcome:
    try:
        follow = await sets.unfollow(guild_id, campaign.id, item.user_id)
    except RuleSetError as exc:
        if str(exc) == rule_sets.NOT_FOLLOWING and item.retried:  # the first try did it
            return Outcome(note="Stopped using the shared rules. Your own rules are unchanged.")
        raise
    extra = await host.settle(campaign, following=False)
    return Outcome(
        note=f"Stopped using **{_escape(follow.name)}**. Your own rules are unchanged." + extra
    )


def _escape(text: str) -> str:
    """Discord's markdown escape, so a set's name can't change the sentence around it."""
    return str(discord.utils.escape_markdown(text))


async def run_one(host: Host, store: ActionStore, guild_id: int, item: Action) -> bool:
    """Apply one claimed choice and mark it. A bug never leaves the row "working": it ends as
    `failed` with `server` for the page to offer a retry. False when the row could not be marked
    (the loop stops; the row is taken again later and `apply` is safe to run twice). Only the
    outcome word is logged: never a code, a set's name or a rule."""
    try:
        done = await apply(host, guild_id, item, store)
    except Exception as exc:
        log.warning("A website rule choice failed: %s", type(exc).__name__)
        done = Outcome(SERVER, COULDNT)
    try:
        if not await store.finish(guild_id, item, done):
            log.info("A website rule choice was taken over by another worker")
    except Exception as exc:
        log.warning("A website rule choice could not be marked: %s", type(exc).__name__)
        return False
    log.info("Website rule %s: %s", item.action, done.error or "done")
    return True
