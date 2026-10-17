"""The rules archive channel (#1752; docs/PLAN.md, "Channels"): `#dmb-rules-<short name>`.

The first controlled channel besides the DM screen: it follows the campaign's DM-screen
visibility (private, opt-in peek, open), is read only for everyone (the DM too: only DMbot
posts), and shows the campaign's saved house rules, one message each, so a player does not have
to look a rule up. Slice 1 is house rules only; overrides and rulings wait for the AI rules
alerts.

What it never holds: anything proposed or waiting (a rule appears only after the DM saved it),
DM-screen content (no secret names, notes or story-plan text), or text from a shared rulebook.
A house rule is the DM's own words.

It is made like the DMbot chat channel (same repair, per-campaign lock and error handling) and
kept in step by `sync_rules`: a message is edited when its rule is, deleted when the rule is
removed, and only DMbot's own messages are ever touched. Messages are found again by their first
words (`RULE_TITLE`, `FOLLOW_TITLE`), so nothing about them is stored: a restart, or a channel
made again by hand-deletion, rebuilds the same list.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import discord

from dmbot.campaigns import Campaign, CampaignStore
from dmbot.dm_screen import messages
from dmbot.dm_screen.channel import (
    CACHE_WAIT_S,
    CACHE_WAIT_STEPS,
    DMScreenError,
    _discord_overwrites,
    _me,
    _pin,
    campaign_lock,
    current_overwrites,
    held_permissions,
)
from dmbot.dm_screen.names import PREFIX, sub_channel_name
from dmbot.dm_screen.rules import (
    FULL,
    READ_ONLY,
    Perms,
    Target,
    is_peeker,
    overwrite_plan,
)
from dmbot.dm_screen.transcript_channel import _repair
from dmbot.rules.house import HouseRule, HouseRuleStore
from dmbot.rules.rule_sets import HOUSE_RULES, RuleSetStore

log = logging.getLogger(__name__)

KIND = "rules"
NAME_PREFIX = f"{PREFIX}-{KIND}-"
PIN_REASON = "DMbot: rules channel card"
RECENT_SCAN = 10  # an unpinned card is looked for among this many recent messages
HISTORY_SCAN = 400  # DMbot's own messages are looked for among this many (far above the cap)
MAX_EDITS = 5  # edits and deletes per run: Discord's rate limits; the rest is done next time
MAX_SENDS = 25  # new messages per run, for the same reason

RULE_TITLE = "🏠 **House rule "
FOLLOW_TITLE = "📚 Following "
CARD_TITLE = "📖 **What's this channel?**"
NO_PINGS = discord.AllowedMentions.none()


class RulesChannelError(Exception):
    """Why the rules channel couldn't be set up, as a plain cause for the DM."""


def rules_channel_name(campaign: Campaign) -> str:
    return sub_channel_name(KIND, campaign.name, campaign.channel_number or 1)


def is_rules_name(name: str) -> bool:
    return name.startswith(NAME_PREFIX)


def rules_plan(
    visibility: str,
    *,
    guild_id: int,
    bot_id: int,
    dm_ids: Sequence[int],
    peeker_ids: Sequence[int] = (),
) -> dict[Target, Perms]:
    """The overwrites for this visibility: the DM screen's plan (the same people see it, and
    peekers are the DM screen's), except that nobody posts here, the DM included. Only DMbot."""
    plan = dict(
        overwrite_plan(
            visibility, guild_id=guild_id, bot_id=bot_id, dm_ids=dm_ids, peeker_ids=peeker_ids
        )
    )
    for user_id in dm_ids:
        if user_id != bot_id:
            plan[Target("member", user_id)] = READ_ONLY
    plan[Target("member", bot_id)] = FULL
    return plan


def screen_peekers(screen: discord.abc.GuildChannel | None) -> list[int]:
    """Who is peeking behind the DM screen now: the rules channel lets in the same people."""
    if screen is None:
        return []
    return [
        t.id for t, perms in current_overwrites(screen).items()
        if t.kind == "member" and is_peeker(perms)
    ]  # fmt: skip


# ---- what each message says -------------------------------------------------------------------


def rule_text(rule: HouseRule) -> str:
    """One house rule as its message: its words, what it replaces, where it came from. Never the
    "what happened" note (a DM's own story), never anything but the rule."""
    if rule.from_set:
        came = f"Came from: {rule.from_set}"
    elif rule.session_id:
        came = "Came from: said at the table"
    else:
        came = "Came from: your DM"
    lines = [f"{RULE_TITLE}{rule.number}**", rule.rule]
    if rule.supersedes:
        lines.append(f"Instead of: {rule.supersedes}")
    lines.append(f"_{came}_")
    return "\n".join(lines)


def follow_text(set_name: str) -> str:
    return f"{FOLLOW_TITLE}**{set_name}**"


def _rule_number(content: str) -> int | None:
    """The rule number a message of DMbot's is for, or None if it isn't a rule message."""
    if not content.startswith(RULE_TITLE):
        return None
    head = content[len(RULE_TITLE) :].split("**", 1)[0]
    return int(head) if head.isdigit() else None


@dataclass(slots=True)
class SyncResult:
    sent: int = 0
    edited: int = 0
    deleted: int = 0
    left_for_next_time: int = 0


async def sync_rules(
    channel: discord.TextChannel,
    me_id: int,
    rules: Sequence[HouseRule],
    following: Sequence[str],
) -> SyncResult:
    """Make the channel show exactly these rules and followed sets. Looks at DMbot's own messages
    only; edits rather than reposts; at most `MAX_EDITS` edits and deletes, `MAX_SENDS` new
    messages: the rest is picked up next time. Idempotent, so every start can run it."""
    result = SyncResult()
    wanted_rules = {r.number: rule_text(r) for r in sorted(rules, key=lambda r: r.number)}
    wanted_follows = {follow_text(name): name for name in dict.fromkeys(following)}
    have_rules: dict[int, discord.Message] = {}
    have_follows: dict[str, discord.Message] = {}
    extras: list[discord.Message] = []  # a second copy of one thing, or a stale one
    async for message in channel.history(limit=HISTORY_SCAN, oldest_first=True):
        if message.author.id != me_id:
            continue
        number = _rule_number(message.content)
        if number is not None:
            if number in have_rules:
                extras.append(message)
            else:
                have_rules[number] = message
        elif message.content.startswith(FOLLOW_TITLE):
            if message.content in have_follows or message.content not in wanted_follows:
                extras.append(message)
            else:
                have_follows[message.content] = message
    budget = MAX_EDITS
    for number, message in have_rules.items():
        if number not in wanted_rules:
            extras.append(message)
    for message in extras:
        if budget <= 0:
            result.left_for_next_time += 1
            continue
        budget -= 1
        with contextlib.suppress(discord.NotFound):
            await message.delete()
        result.deleted += 1
    for number, message in have_rules.items():
        if number in wanted_rules and message.content != wanted_rules[number]:
            if budget <= 0:
                result.left_for_next_time += 1
                continue
            budget -= 1
            await message.edit(content=wanted_rules[number], allowed_mentions=NO_PINGS)
            result.edited += 1
    sends = [(text, False) for number, text in wanted_rules.items() if number not in have_rules]
    sends += [(text, True) for text in wanted_follows if text not in have_follows]
    for text, _is_follow in sends:
        if result.sent >= MAX_SENDS:
            result.left_for_next_time += 1
            continue
        await channel.send(text, allowed_mentions=NO_PINGS, silent=True)
        result.sent += 1
    return result


# ---- the channel ------------------------------------------------------------------------------


async def _fresh(guild: discord.Guild, campaign: Campaign) -> discord.TextChannel | None:
    """The saved channel as Discord has it now; None if it's gone, hidden from DMbot (a new one
    is made), or not a rules channel."""
    if campaign.rules_channel_id is None:
        return None
    try:
        channel = await guild.fetch_channel(campaign.rules_channel_id)
    except (discord.NotFound, discord.Forbidden):
        return None
    if not isinstance(channel, discord.TextChannel) or not is_rules_name(channel.name):
        return None
    return channel


async def _ensure_card(channel: discord.TextChannel, campaign: Campaign, me_id: int) -> None:
    """One current, pinned "What's this channel?" card. Best effort: the channel works without
    it."""
    text = messages.rules_card(campaign.name)
    try:
        found: dict[int, discord.Message] = {}
        for pin in await channel.pins():
            if pin.author.id == me_id and pin.content.startswith(CARD_TITLE):
                found[pin.id] = pin
        async for recent in channel.history(limit=RECENT_SCAN):
            if recent.author.id == me_id and recent.content.startswith(CARD_TITLE):
                found.setdefault(recent.id, recent)
        cards = list(found.values())
        if len(cards) == 1 and cards[0].content == text:
            if not cards[0].pinned:
                await _pin(cards[0], PIN_REASON)
            return
        for old in cards:
            with contextlib.suppress(discord.HTTPException):
                await old.delete()
        card = await channel.send(text, allowed_mentions=NO_PINGS, silent=True)
        await _pin(card, PIN_REASON)
    except discord.HTTPException as exc:
        log.warning("Rules channel card not updated: %s", exc)


async def setup_rules_channel(
    guild: discord.Guild,
    campaign_id: str,
    store: CampaignStore,
    *,
    category: discord.CategoryChannel | None = None,
    screen: discord.abc.GuildChannel | None = None,
) -> discord.TextChannel:
    """Create the campaign's rules channel, or put an existing one right again (who may see it
    follows the DM screen's visibility; `screen` gives the peekers). Call after the DM screen is
    set up (which picks the campaign's channel number). Raises RulesChannelError with a plain
    cause for the DM."""
    try:
        me = _me(guild)
    except DMScreenError as exc:
        raise RulesChannelError(str(exc)) from exc
    async with campaign_lock(campaign_id):
        try:
            campaign = await store.get(guild.id, campaign_id)
            if campaign is None:
                raise RulesChannelError(messages.CAMPAIGN_GONE)
            held = held_permissions(me)
            plan = rules_plan(
                campaign.dm_screen_visibility,
                guild_id=guild.id,
                bot_id=me.id,
                dm_ids=sorted(campaign.dm_user_ids),
                peeker_ids=screen_peekers(screen),
            )
            topic = messages.RULES_TOPIC
            reason = f"DMbot: house rules for {campaign.name}"
            channel = await _fresh(guild, campaign)
            if channel is None:
                channel = await guild.create_text_channel(
                    rules_channel_name(campaign),
                    category=category,
                    overwrites=_discord_overwrites(guild, plan, held),
                    topic=topic,
                    reason=reason,
                )
                for _ in range(CACHE_WAIT_STEPS):
                    if guild.get_channel(channel.id) is not None:
                        break
                    await asyncio.sleep(CACHE_WAIT_S)
                else:
                    log.warning("New rules channel %s hasn't reached the cache yet", channel.id)
                try:
                    await store.set_rules_channel(guild.id, campaign.id, channel.id)
                except BaseException:
                    with contextlib.suppress(discord.HTTPException):
                        await channel.delete(reason="DMbot: could not save the rules channel")
                    raise
            else:
                await _repair(channel, rules_channel_name(campaign), plan, held, topic, reason)
            await _ensure_card(channel, campaign, me.id)
        except discord.Forbidden as exc:
            raise RulesChannelError(messages.TRANSCRIPT_FORBIDDEN) from exc
        except discord.HTTPException as exc:
            log.warning("Rules channel setup failed: %s", exc)
            raise RulesChannelError(
                messages.transcript_discord_error(exc.text or str(exc.status))
            ) from exc
        except TimeoutError as exc:
            raise RulesChannelError(messages.TRANSCRIPT_NO_ANSWER) from exc
    return channel


async def follow_screen(
    guild: discord.Guild, campaign: Campaign, screen: discord.TextChannel
) -> None:
    """The DM screen's visibility, DMs or peekers just changed: make the rules channel (if the
    campaign has one) match, now. Never raises: the DM screen's change has already happened, and
    the next start repairs the rest."""
    if campaign.rules_channel_id is None:
        return
    try:
        me = _me(guild)
        async with campaign_lock(campaign.id):
            channel = await _fresh(guild, campaign)
            if channel is None:
                return
            plan = rules_plan(
                campaign.dm_screen_visibility,
                guild_id=guild.id,
                bot_id=me.id,
                dm_ids=sorted(campaign.dm_user_ids),
                peeker_ids=screen_peekers(screen),
            )
            await _repair(
                channel,
                rules_channel_name(campaign),
                plan,
                held_permissions(me),
                messages.RULES_TOPIC,
                f"DMbot: house rules for {campaign.name}",
            )
    except Exception:
        log.warning("Couldn't update the rules channel's visibility", exc_info=True)


async def sync_campaign(
    guild: discord.Guild,
    campaign: Campaign,
    house_rules: HouseRuleStore,
    rule_sets: RuleSetStore | None,
) -> SyncResult | None:
    """Bring the campaign's rules channel up to date from the saved rules and the sets it
    follows. None if the campaign has no rules channel or it is gone (the next start makes it
    again). Raises on a Discord or database failure: the caller logs it."""
    channel = await _fresh(guild, campaign)
    me = guild.me
    if channel is None or me is None:
        return None
    rules = await house_rules.list(guild.id, campaign.id)
    names: list[str] = []
    if rule_sets is not None:
        names = [
            f.name
            for f in await rule_sets.following(guild.id, campaign.id)
            if f.kind == HOUSE_RULES
        ]
    return await sync_rules(channel, me.id, rules, names)
