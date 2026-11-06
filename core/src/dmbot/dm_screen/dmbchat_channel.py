"""The table's DMbot chat channel (#1187; docs/PLAN.md, "Table questions: anyone may ask").

`#dmb-chat-<short name>`: everyone in the server can read it and type in it, and DMbot posts the
answers to the table's questions there. It is made like the game-time channel (it reuses that
channel's repair, per-campaign lock and error handling) but, unlike the transcript and game-time
channels, people may post in it: typing a question is how it is used. `/dmbot start` creates or
repairs it, and a failure never blocks the session.

DMbot only ever changes a channel it named like a DMbot chat channel, and only edits it when
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
from dmbot.dm_screen.rules import FULL, Perms, Target, everyone
from dmbot.dm_screen.transcript_channel import _repair

log = logging.getLogger(__name__)

KIND = "chat"
NAME_PREFIX = f"{PREFIX}-{KIND}-"

# Everyone at the table may read and type here (that is the point of the channel). Threads and
# reactions stay as the server has them: DMbot answers only plain messages.
TYPING: Perms = {"view_channel": True, "read_message_history": True, "send_messages": True}


class DmbchatChannelError(Exception):
    """Why the DMbot chat channel couldn't be set up, as a plain cause for the DM."""


def dmbchat_channel_name(campaign: Campaign) -> str:
    return sub_channel_name(KIND, campaign.name, campaign.channel_number or 1)


def is_dmbchat_name(name: str) -> bool:
    return name.startswith(NAME_PREFIX)


def dmbchat_plan(*, guild_id: int, bot_id: int) -> dict[Target, Perms]:
    """Everyone reads and types; DMbot posts."""
    return {everyone(guild_id): TYPING, Target("member", bot_id): FULL}


async def _fresh(guild: discord.Guild, campaign: Campaign) -> discord.TextChannel | None:
    """The saved channel as Discord has it now; None if it's gone, hidden from DMbot (a new
    one is made), or not a DMbot chat channel."""
    if campaign.dmbchat_channel_id is None:
        return None
    try:
        channel = await guild.fetch_channel(campaign.dmbchat_channel_id)
    except (discord.NotFound, discord.Forbidden):
        return None
    if not isinstance(channel, discord.TextChannel) or not is_dmbchat_name(channel.name):
        return None
    return channel


async def setup_dmbchat_channel(
    guild: discord.Guild,
    campaign_id: str,
    store: CampaignStore,
    *,
    category: discord.CategoryChannel | None = None,
) -> discord.TextChannel:
    """Create the campaign's DMbot chat channel, or put an existing one right again.

    Call after the DM screen is set up (which picks the campaign's channel number). Raises
    DmbchatChannelError with a plain cause for the DM.
    """
    try:
        me = _me(guild)
    except DMScreenError as exc:
        raise DmbchatChannelError(str(exc)) from exc
    async with campaign_lock(campaign_id):
        try:
            campaign = await store.get(guild.id, campaign_id)
            if campaign is None:
                raise DmbchatChannelError(messages.CAMPAIGN_GONE)
            held = held_permissions(me)
            plan = dmbchat_plan(guild_id=guild.id, bot_id=me.id)
            topic = messages.DMBCHAT_TOPIC
            reason = f"DMbot: chat for {campaign.name}"
            channel = await _fresh(guild, campaign)
            if channel is None:
                channel = await guild.create_text_channel(
                    dmbchat_channel_name(campaign),
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
                        "New DMbot chat channel %s hasn't reached the cache yet", channel.id
                    )
                try:
                    await store.set_dmbchat_channel(guild.id, campaign.id, channel.id)
                except BaseException:
                    with contextlib.suppress(discord.HTTPException):
                        await channel.delete(reason="DMbot: could not save the chat channel")
                    raise
            else:
                await _repair(channel, dmbchat_channel_name(campaign), plan, held, topic, reason)
        except discord.Forbidden as exc:
            raise DmbchatChannelError(messages.TRANSCRIPT_FORBIDDEN) from exc
        except discord.HTTPException as exc:
            log.warning("DMbot chat channel setup failed: %s", exc)
            raise DmbchatChannelError(
                messages.transcript_discord_error(exc.text or str(exc.status))
            ) from exc
        except TimeoutError as exc:
            raise DmbchatChannelError(messages.TRANSCRIPT_NO_ANSWER) from exc
    return channel
