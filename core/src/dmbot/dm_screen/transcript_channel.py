"""Create a campaign's live transcript channel and keep it view-only (#124).

`#dmb-transcript-<short name>` (docs/PLAN.md, "Channel structure"): unrestricted, so
anyone in the server can read it, and only DMbot posts (no threads or reactions
either). Slash commands work there (#188): their replies are private, the pinned card
tells players to type `/consent revoke`, and a DM's first try is often `/dmbot` in the
channel they're looking at.

It sits next to the DM screen, in the same category, with a topic and a pinned "what's
this channel?" card. DMbot only ever changes a channel it named like a
transcript channel, and only edits it when something is actually different (Discord
allows few channel edits per ten minutes).

Shares the DM screen's helpers (private to that package) and its per-campaign lock.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import discord

from dmbot.campaigns import Campaign, CampaignStore
from dmbot.dm_screen import messages
from dmbot.dm_screen.channel import (
    CACHE_WAIT_S,
    CACHE_WAIT_STEPS,
    RENAME_TIMEOUT_S,
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
    everyone,
    merge_overwrites,
    restrict,
)

log = logging.getLogger(__name__)

KIND = "transcript"
NAME_PREFIX = f"{PREFIX}-{KIND}-"
RECENT_SCAN = 10  # an unpinned card is looked for among this many recent messages
PIN_REASON = "DMbot: transcript channel card"


class TranscriptChannelError(Exception):
    """Why the transcript channel couldn't be set up, as a plain cause for the DM."""


def transcript_channel_name(campaign: Campaign) -> str:
    return sub_channel_name(KIND, campaign.name, campaign.channel_number or 1)


def is_transcript_name(name: str) -> bool:
    return name.startswith(NAME_PREFIX)


# Read-only, but slash commands left alone (inherited, so the server's own setting
# applies): a blocked command can hang on "Sending command..." with no explanation.
VIEW_AND_COMMANDS: Perms = {k: v for k, v in READ_ONLY.items() if k != "use_application_commands"}


def transcript_plan(*, guild_id: int, bot_id: int) -> dict[Target, Perms]:
    """Everyone reads and can use slash commands, nobody posts; DMbot posts. DMs read
    like everyone else."""
    return {everyone(guild_id): VIEW_AND_COMMANDS, Target("member", bot_id): FULL}


async def _fresh(guild: discord.Guild, campaign: Campaign) -> discord.TextChannel | None:
    """The saved channel as Discord has it now; None if it's gone, hidden from DMbot (a
    new one is made), or not a transcript channel."""
    if campaign.transcript_channel_id is None:
        return None
    try:
        channel = await guild.fetch_channel(campaign.transcript_channel_id)
    except (discord.NotFound, discord.Forbidden):
        return None
    if not isinstance(channel, discord.TextChannel) or not is_transcript_name(channel.name):
        return None
    return channel


async def _ensure_card(
    channel: discord.TextChannel, campaign: Campaign, me: discord.Member
) -> None:
    """One current, pinned card. Looked for among the pins (a transcript channel soon has
    thousands of messages) and the last few messages. Best effort: the channel works
    without it."""
    text = messages.transcript_card(campaign.name)
    try:
        found: dict[int, discord.Message] = {}
        for pin in await channel.pins():
            if pin.author.id == me.id and pin.content.startswith(messages.TRANSCRIPT_CARD_TITLE):
                found[pin.id] = pin
        async for recent in channel.history(limit=RECENT_SCAN):
            if recent.author.id == me.id and recent.content.startswith(
                messages.TRANSCRIPT_CARD_TITLE
            ):
                found.setdefault(recent.id, recent)
        cards = list(found.values())
        if len(cards) == 1 and cards[0].content == text:
            if not cards[0].pinned:
                await _pin(cards[0], PIN_REASON)
            return
        for old in cards:
            with contextlib.suppress(discord.HTTPException):
                await old.delete()
        card = await channel.send(
            text, allowed_mentions=discord.AllowedMentions.none(), silent=True
        )
        await _pin(card, PIN_REASON)
    except discord.HTTPException as exc:
        log.warning("Transcript channel card not updated: %s", exc)


async def _repair(
    channel: discord.TextChannel,
    campaign: Campaign,
    plan: dict[Target, Perms],
    held: set[str],
    topic: str,
    reason: str,
) -> None:
    """Make an existing channel view-only again, changing only what differs."""
    guild = channel.guild
    current = current_overwrites(channel)
    merged = merge_overwrites(current, plan, guild_id=guild.id)
    wanted = {t: restrict(p, held) for t, p in merged.items()}
    # Compared as DMbot could set it: a permission it doesn't hold can't be changed, so a
    # stale value there mustn't trigger an edit at every start.
    have = {t: restrict(p, held) for t, p in current.items()}
    timeout = RENAME_TIMEOUT_S * 2
    if have != wanted:
        overwrites = _discord_overwrites(guild, merged, held)
        if channel.topic != topic:
            edit = channel.edit(overwrites=overwrites, topic=topic, reason=reason)
        else:
            edit = channel.edit(overwrites=overwrites, reason=reason)
        await asyncio.wait_for(edit, timeout)
    elif channel.topic != topic:
        await asyncio.wait_for(channel.edit(topic=topic, reason=reason), timeout)
    name = transcript_channel_name(campaign)
    if channel.name != name:  # a renamed campaign; best effort (rate limited)
        try:
            await asyncio.wait_for(channel.edit(name=name, reason=reason), RENAME_TIMEOUT_S)
        except (TimeoutError, discord.HTTPException) as exc:
            log.info("Rename of transcript channel %s postponed: %s", channel.id, exc)


async def setup_transcript_channel(
    guild: discord.Guild,
    campaign_id: str,
    store: CampaignStore,
    *,
    category: discord.CategoryChannel | None = None,
) -> discord.TextChannel:
    """Create the campaign's transcript channel, or make an existing one view-only again.

    Call after the DM screen is set up (which picks the campaign's channel number).
    Raises TranscriptChannelError with a plain cause for the DM.
    """
    try:
        me = _me(guild)
    except DMScreenError as exc:
        raise TranscriptChannelError(str(exc)) from exc
    async with campaign_lock(campaign_id):
        try:
            campaign = await store.get(guild.id, campaign_id)
            if campaign is None:
                raise TranscriptChannelError(messages.CAMPAIGN_GONE)
            held = held_permissions(me)
            plan = transcript_plan(guild_id=guild.id, bot_id=me.id)
            topic = messages.transcript_topic(campaign.name)
            reason = f"DMbot: live transcript for {campaign.name}"
            channel = await _fresh(guild, campaign)
            if channel is None:
                channel = await guild.create_text_channel(
                    transcript_channel_name(campaign),
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
                    log.warning(
                        "New transcript channel %s hasn't reached the cache yet", channel.id
                    )
                try:
                    await store.set_transcript_channel(guild.id, campaign.id, channel.id)
                except BaseException:
                    with contextlib.suppress(discord.HTTPException):
                        await channel.delete(reason="DMbot: could not save the transcript channel")
                    raise
            else:
                await _repair(channel, campaign, plan, held, topic, reason)
        except discord.Forbidden as exc:
            raise TranscriptChannelError(messages.TRANSCRIPT_FORBIDDEN) from exc
        except discord.HTTPException as exc:
            log.warning("Transcript channel setup failed: %s", exc)
            raise TranscriptChannelError(
                messages.transcript_discord_error(exc.text or str(exc.status))
            ) from exc
        except TimeoutError as exc:
            raise TranscriptChannelError(messages.TRANSCRIPT_NO_ANSWER) from exc
        await _ensure_card(channel, campaign, me)
    return channel
