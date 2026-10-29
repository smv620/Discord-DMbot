"""The DM Bot channel (#1666; docs/PLAN.md, "The DM Bot channel").

`#dmb-dm-bot-<short name>`: everyone in the server can read it, and only DMbot posts, as with
the game-time channel (it reuses the transcript module's permissions, repair and per-campaign
lock). It holds one panel message of buttons by role (`dm_screen/dm_bot_panel.py`); every
answer to a press is private, and nothing from the DM screen is ever posted here.
`/dmbot start` creates or repairs it, and a failure never blocks the session.

DMbot only ever changes a channel it named like a DM Bot channel, and only edits it when
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

KIND = "dm-bot"
NAME_PREFIX = f"{PREFIX}-{KIND}-"


class DmBotChannelError(Exception):
    """Why the DM Bot channel couldn't be set up, as a plain cause for the DM."""


def dm_bot_channel_name(campaign: Campaign) -> str:
    return sub_channel_name(KIND, campaign.name, campaign.channel_number or 1)


def is_dm_bot_name(name: str) -> bool:
    return name.startswith(NAME_PREFIX)


async def _fresh(guild: discord.Guild, campaign: Campaign) -> discord.TextChannel | None:
    """The saved channel as Discord has it now; None if it's gone, hidden from DMbot (a new
    one is made), or not a DM Bot channel."""
    if campaign.dm_bot_channel_id is None:
        return None
    try:
        channel = await guild.fetch_channel(campaign.dm_bot_channel_id)
    except (discord.NotFound, discord.Forbidden):
        return None
    if not isinstance(channel, discord.TextChannel) or not is_dm_bot_name(channel.name):
        return None
    return channel


async def setup_dm_bot_channel(
    guild: discord.Guild,
    campaign_id: str,
    store: CampaignStore,
    *,
    category: discord.CategoryChannel | None = None,
) -> discord.TextChannel:
    """Create the campaign's DM Bot channel, or make an existing one view-only again.

    Call after the DM screen is set up (which picks the campaign's channel number). Raises
    DmBotChannelError with a plain cause for the DM.
    """
    try:
        me = _me(guild)
    except DMScreenError as exc:
        raise DmBotChannelError(str(exc)) from exc
    async with campaign_lock(campaign_id):
        try:
            campaign = await store.get(guild.id, campaign_id)
            if campaign is None:
                raise DmBotChannelError(messages.CAMPAIGN_GONE)
            held = held_permissions(me)
            plan = transcript_plan(guild_id=guild.id, bot_id=me.id)
            topic = messages.dm_bot_topic(campaign.name)
            reason = f"DMbot: settings for {campaign.name}"
            channel = await _fresh(guild, campaign)
            if channel is None:
                channel = await guild.create_text_channel(
                    dm_bot_channel_name(campaign),
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
                    log.warning("New DM Bot channel %s hasn't reached the cache yet", channel.id)
                try:
                    await store.set_dm_bot_channel(guild.id, campaign.id, channel.id)
                except BaseException:
                    with contextlib.suppress(discord.HTTPException):
                        await channel.delete(reason="DMbot: could not save the DM Bot channel")
                    raise
            else:
                await _repair(channel, dm_bot_channel_name(campaign), plan, held, topic, reason)
        except discord.Forbidden as exc:
            raise DmBotChannelError(messages.TRANSCRIPT_FORBIDDEN) from exc
        except discord.HTTPException as exc:
            log.warning("DM Bot channel setup failed: %s", exc)
            raise DmBotChannelError(
                messages.transcript_discord_error(exc.text or str(exc.status))
            ) from exc
        except TimeoutError as exc:
            raise DmBotChannelError(messages.TRANSCRIPT_NO_ANSWER) from exc
    return channel
