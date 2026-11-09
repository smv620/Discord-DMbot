"""Keeping a campaign's DM Bot and transcript channels in step with who may see them (#3891).

`channel_people.py` plans the overwrites as plain data; this module gathers the campaign's people
from the stores and applies the plan through the two channel modules. The sweep looks at the
channels Discord's cache already holds and only calls the channel setup when an overwrite differs,
so an hourly look at every campaign costs no Discord calls when nothing changed. Ids are never
logged, only counts.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable

import discord

from dmbot.campaigns import Campaign, CampaignStore
from dmbot.dm_screen import channel_people as cp
from dmbot.dm_screen.channel import current_overwrites, held_permissions
from dmbot.dm_screen.dm_bot_channel import DmBotChannelError, setup_dm_bot_channel
from dmbot.dm_screen.rules import Perms, Target, merge_overwrites, restrict
from dmbot.dm_screen.transcript_channel import (
    ChannelNotThere,
    TranscriptChannelError,
    setup_transcript_channel,
)
from dmbot.memory.store import MemoryStore
from dmbot.transcript.attendance import campaign_speakers

log = logging.getLogger(__name__)

MEMBER_CHECK_S = 6 * 3600  # how long "still in the server" or "gone" is believed
_seen: dict[tuple[int, int], tuple[bool, float]] = {}  # (server, member) -> here?, checked at


async def present_only(guild: discord.Guild, ids: Iterable[int]) -> frozenset[int]:
    """The ids that are still members of the server. Discord may refuse an overwrite for someone
    who left, and one such id would block every later change for the campaign, so a member the
    cache does not hold is asked for once (and believed for a few hours). A failure other than
    "not a member" keeps the id: only a clear NotFound drops one."""
    here: set[int] = set()
    now = time.monotonic()
    for user_id in ids:
        if guild.get_member(user_id) is not None:
            here.add(user_id)
            continue
        known = _seen.get((guild.id, user_id))
        if known is not None and now - known[1] < MEMBER_CHECK_S:
            if known[0]:
                here.add(user_id)
            continue
        try:
            await guild.fetch_member(user_id)
            _seen[(guild.id, user_id)] = (True, now)
            here.add(user_id)
        except discord.NotFound:
            _seen[(guild.id, user_id)] = (False, now)
        except discord.HTTPException:
            here.add(user_id)  # (could not tell: keep them rather than hide a channel)
    return frozenset(here)


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
        people = await present_only(
            guild, await campaign_people(campaigns, memory, campaign, me.id)
        )
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
            try:
                await setup(guild, campaign.id, campaigns, people=people, create=False)
            except ChannelNotThere:
                continue  # (gone: a session start makes a new one, never a sweep)
            changed += 1
        if changed:
            log.info("Channel sync: %d channel(s) of a campaign put in step", changed)
        return None
    except (TranscriptChannelError, DmBotChannelError) as exc:
        return str(exc)  # already plain words, with the permission's name when one is missing
    except Exception:
        log.exception("Channel sync: couldn't put a campaign's channels in step")
        return None


def exposed_roles(guild: discord.Guild, campaign: Campaign) -> int:
    """How many server roles can see the campaign's channels although only its people should.
    The server's role overwrites are never changed here, only counted so the DM can be told."""
    if campaign.channels_visibility != cp.PEOPLE:
        return 0
    roles: set[int] = set()
    for channel_id in (campaign.transcript_channel_id, campaign.dm_bot_channel_id):
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None:
            continue
        found = cp.find_exposure(
            campaign.channels_visibility,
            current_overwrites(channel),
            guild_id=guild.id,
            bot_id=guild.me.id if guild.me is not None else 0,
            people=(),
        )
        roles.update(found.roles)
    return len(roles)
