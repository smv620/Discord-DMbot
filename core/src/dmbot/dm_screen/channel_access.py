"""Keeping a campaign's DM Bot and transcript channels in step with who may see them (#3891).

`channel_people.py` plans the overwrites as plain data; this module gathers the campaign's people
from the stores and applies the plan through the two channel modules. The sweep looks at the
channels Discord's cache already holds and only calls the channel setup when an overwrite differs,
so an hourly look at every campaign costs no Discord calls when nothing changed. Ids are never
logged, only counts.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import discord

from dmbot.campaigns import Campaign, CampaignStore
from dmbot.dm_screen import channel_people as cp
from dmbot.dm_screen.channel import current_overwrites, held_permissions
from dmbot.dm_screen.dm_bot_channel import DmBotChannelError, setup_dm_bot_channel
from dmbot.dm_screen.rules import Perms, Target, merge_overwrites, restrict
from dmbot.dm_screen.transcript_channel import TranscriptChannelError, setup_transcript_channel
from dmbot.memory.store import MemoryStore
from dmbot.transcript.attendance import campaign_speakers

log = logging.getLogger(__name__)


async def campaign_people(
    campaigns: CampaignStore, memory: MemoryStore | None, campaign: Campaign, bot_id: int
) -> frozenset[int]:
    """Everyone who may see the campaign's channels: its DMs (every Discord id of each DM account),
    the members who play a confirmed character in it, the members recorded at one of its sessions
    and anyone a DM added by hand. A store that is off adds no one, but the DMs are always in."""
    players = await memory.players_of(campaign.guild_id, campaign.id) if memory else set()
    return cp.people_of(
        dm_ids=campaign.dm_user_ids,
        player_ids=players,
        recorded_ids=await campaign_speakers(campaigns.db, campaign.guild_id, campaign.id),
        by_hand=await campaigns.channel_people_by_hand(campaign.guild_id, campaign.id),
        bot_id=bot_id,
    )


def in_step(
    channel: discord.abc.GuildChannel,
    plan: dict[Target, Perms],
    held: set[str],
    guild_id: int,
) -> bool:
    """Whether the channel's overwrites already are what the plan would leave (compared as DMbot
    could set them: a permission it does not hold cannot be changed)."""
    current = current_overwrites(channel)
    merged = merge_overwrites(current, plan, guild_id=guild_id)
    wanted = {t: restrict(p, held) for t, p in merged.items()}
    return {t: restrict(p, held) for t, p in current.items()} == wanted


def exposure(
    channel: discord.abc.GuildChannel, campaign: Campaign, bot_id: int, people: Iterable[int]
) -> cp.Exposure:
    """Who outside the campaign can see this channel: a server role that allows View, or a member
    who is not one of the people. The server's role overwrites are never changed, only reported."""
    return cp.find_exposure(
        campaign.channels_visibility,
        current_overwrites(channel),
        guild_id=channel.guild.id,
        bot_id=bot_id,
        people=people,
    )


async def reconcile(
    guild: discord.Guild,
    campaign: Campaign,
    campaigns: CampaignStore,
    memory: MemoryStore | None,
) -> str | None:
    """Make the campaign's two channels match its choice, changing only what differs and only
    channels that exist (they are made at a session start, not here). Returns a plain cause for the
    DM when it could not (a missing permission, with its name), None when the channels are right.
    Never raises."""
    me = guild.me
    if me is None:
        return None  # (still starting: the next sweep looks again)
    try:
        people = await campaign_people(campaigns, memory, campaign, me.id)
        plan = cp.channel_plan(
            campaign.channels_visibility,
            guild_id=guild.id,
            bot_id=me.id,
            people={*people, *campaign.dm_user_ids},
        )
        held = held_permissions(me)
        changed = 0
        for channel_id, setup in (
            (campaign.transcript_channel_id, setup_transcript_channel),
            (campaign.dm_bot_channel_id, setup_dm_bot_channel),
        ):
            channel = guild.get_channel(channel_id) if channel_id else None
            if channel is None or in_step(channel, plan, held, guild.id):
                continue
            await setup(guild, campaign.id, campaigns, people=people)
            changed += 1
        if changed:
            log.info("Channel access: %d channel(s) of a campaign put in step", changed)
        return None
    except (TranscriptChannelError, DmBotChannelError) as exc:
        return str(exc)  # already plain words, with the permission's name when one is missing
    except Exception:
        log.exception("Channel access: couldn't put a campaign's channels in step")
        return None
