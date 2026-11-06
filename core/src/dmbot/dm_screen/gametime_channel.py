"""The players' game-time channel (#1116; docs/PLAN.md, "TimeBot", the game-time channel).

`#dmb-gametime-<short name>`: everyone in the server can read it, and only DMbot posts, as
with the transcript channel (it reuses that module's permissions, repair and per-campaign
lock). It holds the players' pinned clock, "🕰️ Day 5, morning" (see `dm_screen/clock.py`
`show_players`): the day and a rough time of day, never the exact time, a timer or a button.
`/dmbot start` creates or repairs it, and a failure never blocks the session.

DMbot only ever changes a channel it named like a game-time channel, and only edits it when
something is actually different (Discord allows few channel edits per ten minutes).
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
    DMScreenError,
    _discord_overwrites,
    _me,
    campaign_lock,
    held_permissions,
)
from dmbot.dm_screen.names import PREFIX, sub_channel_name
from dmbot.dm_screen.transcript_channel import _repair, transcript_plan

log = logging.getLogger(__name__)

KIND = "gametime"
NAME_PREFIX = f"{PREFIX}-{KIND}-"


class GametimeChannelError(Exception):
    """Why the game-time channel couldn't be set up, as a plain cause for the DM."""


def gametime_channel_name(campaign: Campaign) -> str:
    return sub_channel_name(KIND, campaign.name, campaign.channel_number or 1)


def is_gametime_name(name: str) -> bool:
    return name.startswith(NAME_PREFIX)


async def _fresh(guild: discord.Guild, campaign: Campaign) -> discord.TextChannel | None:
    """The saved channel as Discord has it now; None if it's gone, hidden from DMbot (a new
    one is made), or not a game-time channel."""
    if campaign.gametime_channel_id is None:
        return None
    try:
        channel = await guild.fetch_channel(campaign.gametime_channel_id)
    except (discord.NotFound, discord.Forbidden):
        return None
    if not isinstance(channel, discord.TextChannel) or not is_gametime_name(channel.name):
        return None
    return channel


async def setup_gametime_channel(
    guild: discord.Guild,
    campaign_id: str,
    store: CampaignStore,
    *,
    category: discord.CategoryChannel | None = None,
) -> discord.TextChannel:
    """Create the campaign's game-time channel, or make an existing one view-only again.

    Call after the DM screen is set up (which picks the campaign's channel number). Raises
    GametimeChannelError with a plain cause for the DM.
    """
    try:
        me = _me(guild)
    except DMScreenError as exc:
        raise GametimeChannelError(str(exc)) from exc
    async with campaign_lock(campaign_id):
        try:
            campaign = await store.get(guild.id, campaign_id)
            if campaign is None:
                raise GametimeChannelError(messages.CAMPAIGN_GONE)
            held = held_permissions(me)
            plan = transcript_plan(guild_id=guild.id, bot_id=me.id)
            topic = messages.gametime_topic(campaign.name)
            reason = f"DMbot: game time for {campaign.name}"
            channel = await _fresh(guild, campaign)
            if channel is None:
                channel = await guild.create_text_channel(
                    gametime_channel_name(campaign),
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
                    log.warning("New game-time channel %s hasn't reached the cache yet", channel.id)
                try:
                    await store.set_gametime_channel(guild.id, campaign.id, channel.id)
                except BaseException:
                    with contextlib.suppress(discord.HTTPException):
                        await channel.delete(reason="DMbot: could not save the game-time channel")
                    raise
            else:
                await _repair(channel, gametime_channel_name(campaign), plan, held, topic, reason)
        except discord.Forbidden as exc:
            raise GametimeChannelError(messages.TRANSCRIPT_FORBIDDEN) from exc
        except discord.HTTPException as exc:
            log.warning("Game-time channel setup failed: %s", exc)
            raise GametimeChannelError(
                messages.transcript_discord_error(exc.text or str(exc.status))
            ) from exc
        except TimeoutError as exc:
            raise GametimeChannelError(messages.TRANSCRIPT_NO_ANSWER) from exc
    return channel
