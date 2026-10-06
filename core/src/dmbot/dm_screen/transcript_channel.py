"""Create a campaign's live transcript channel and keep it view-only (#124).

`#dmb-transcript-<short name>` (docs/PLAN.md, "Channel structure"): unrestricted, so
anyone in the server can read it, and nobody but DMbot can post (no threads, reactions
or commands either). It sits next to the DM screen, in the same category. It has a topic
and a pinned "what's this channel?" card. DMbot only ever changes a channel it named
like a transcript channel.
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
    HELP_CARD_SCAN,
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
from dmbot.dm_screen.rules import FULL, READ_ONLY, Perms, Target, everyone, merge_overwrites

log = logging.getLogger(__name__)

KIND = "transcript"
NAME_PREFIX = f"{PREFIX}-{KIND}-"


def transcript_channel_name(campaign: Campaign) -> str:
    return sub_channel_name(KIND, campaign.name, campaign.channel_number or 1)


def is_transcript_name(name: str) -> bool:
    return name.startswith(NAME_PREFIX)


def transcript_plan(*, guild_id: int, bot_id: int) -> dict[Target, Perms]:
    """Everyone reads, nobody posts; DMbot posts. DMs read like everyone else."""
    return {everyone(guild_id): READ_ONLY, Target("member", bot_id): FULL}


async def _fresh(guild: discord.Guild, campaign: Campaign) -> discord.TextChannel | None:
    if campaign.transcript_channel_id is None:
        return None
    try:
        channel = await guild.fetch_channel(campaign.transcript_channel_id)
    except discord.NotFound:
        return None
    if not isinstance(channel, discord.TextChannel) or not is_transcript_name(channel.name):
        return None
    return channel


async def _ensure_card(
    channel: discord.TextChannel, campaign: Campaign, me: discord.Member
) -> None:
    """One current, pinned "what's this channel?" card (best effort: the channel works
    without it)."""
    text = messages.transcript_card(campaign.name)
    cards = [
        m
        async for m in channel.history(limit=HELP_CARD_SCAN)
        if m.author.id == me.id and m.content.startswith(messages.TRANSCRIPT_CARD_TITLE)
    ]
    if len(cards) == 1 and cards[0].content == text:
        if not cards[0].pinned:
            await _pin(cards[0])
        return
    for old in cards:
        with contextlib.suppress(discord.HTTPException):
            await old.delete()
    card = await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
    await _pin(card)


async def setup_transcript_channel(
    guild: discord.Guild,
    campaign_id: str,
    store: CampaignStore,
    *,
    category: discord.CategoryChannel | None = None,
) -> discord.TextChannel:
    """Create the campaign's transcript channel, or make an existing one view-only again.

    Call after the DM screen is set up (which picks the campaign's channel number).
    Raises DMScreenError with a message for the DM.
    """
    me = _me(guild)
    async with campaign_lock(campaign_id):
        try:
            campaign = await store.get(guild.id, campaign_id)
            if campaign is None:
                raise DMScreenError(messages.CAMPAIGN_GONE)
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
                try:
                    await store.set_transcript_channel(guild.id, campaign.id, channel.id)
                except BaseException:
                    with contextlib.suppress(discord.HTTPException):
                        await channel.delete(reason="DMbot: could not save the transcript channel")
                    raise
            else:
                merged = merge_overwrites(current_overwrites(channel), plan, guild_id=guild.id)
                await channel.edit(
                    overwrites=_discord_overwrites(guild, merged, held), topic=topic, reason=reason
                )
                name = transcript_channel_name(campaign)
                if channel.name != name:  # a renamed campaign; best effort (rate limited)
                    try:
                        await asyncio.wait_for(
                            channel.edit(name=name, reason=reason), RENAME_TIMEOUT_S
                        )
                    except (TimeoutError, discord.HTTPException) as exc:
                        log.info("Rename of transcript channel %s postponed: %s", channel.id, exc)
            await _ensure_card(channel, campaign, me)
        except discord.Forbidden as exc:
            raise DMScreenError(messages.FORBIDDEN_HERE) from exc
        except discord.HTTPException as exc:
            log.warning("Transcript channel setup failed: %s", exc)
            raise DMScreenError(messages.discord_error(exc.text or str(exc.status))) from exc
    return channel
