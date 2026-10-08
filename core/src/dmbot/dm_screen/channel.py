"""Create a campaign's DM screen channel and keep its permissions matching its visibility.

Every change to a screen's permissions (setup, visibility change, peek, hide) runs under
that campaign's lock and starts from a fresh copy of the channel, so two changes can't
race or work from stale overwrites.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Protocol

import discord

from dmbot.campaigns import Campaign, CampaignStore
from dmbot.dm_screen import messages
from dmbot.dm_screen.names import (
    clashes,
    is_screen_name,
    pick_channel_number,
    screen_channel_name,
)
from dmbot.dm_screen.rules import (
    Perms,
    Target,
    find_exposure,
    is_peeker,
    merge_overwrites,
    missing_required,
    overwrite_plan,
    restrict,
)

log = logging.getLogger(__name__)

OverwriteKey = discord.Role | discord.Member | discord.Object
HELP_CARD_SCAN = 50  # recent messages searched for an old help card to replace
CACHE_WAIT_STEPS, CACHE_WAIT_S = 30, 0.1  # up to 3 s for a new channel to reach the cache

RENAME_TIMEOUT_S = 5  # give up on a rate-limited rename and retry at the next setup

_locks: dict[str, asyncio.Lock] = {}
_guild_locks: dict[int, asyncio.Lock] = {}


def campaign_lock(campaign_id: str) -> asyncio.Lock:
    """One lock per campaign for anything that changes its channels' permissions (the DM
    screen, the transcript channel)."""
    return _locks.setdefault(campaign_id, asyncio.Lock())


class DMScreenError(Exception):
    """A problem the DM can fix. The message is safe to show them as-is."""


@dataclass(frozen=True, slots=True)
class ScreenResult:
    channel: discord.TextChannel
    campaign: Campaign  # as stored when the screen was set up
    # For the DM: who unexpectedly can see the screen, and/or that the help card
    # couldn't be pinned.
    warning: str | None


def _target(key: OverwriteKey) -> Target:
    # Uncached members come back as discord.Object(type=User); only roles are roles.
    if isinstance(key, discord.Role) or (
        isinstance(key, discord.Object) and key.type is discord.Role
    ):
        return Target("role", key.id)
    return Target("member", key.id)


# discord.py reports some permissions under older names than the ones it accepts (and
# that Discord shows): View Channel iterates as "read_messages". Rules use the new names.
_RENAMED = {"read_messages": "view_channel"}


def _perms(overwrite: discord.PermissionOverwrite) -> dict[str, bool]:
    return {_RENAMED.get(name, name): value for name, value in overwrite if value is not None}


def current_overwrites(channel: discord.abc.GuildChannel) -> dict[Target, Perms]:
    return {_target(key): _perms(ow) for key, ow in channel.overwrites.items()}


def _key(guild: discord.Guild, target: Target) -> OverwriteKey:
    if target.kind == "role":
        return guild.get_role(target.id) or discord.Object(target.id, type=discord.Role)
    # Members may not be cached (no members intent); an Object works for overwrites.
    return guild.get_member(target.id) or discord.Object(target.id, type=discord.Member)


def held_permissions(me: discord.Member) -> set[str]:
    perms = me.guild_permissions
    if perms.administrator:
        names = set(discord.Permissions.VALID_FLAGS)
    else:
        names = {name for name, value in perms if value}
    return {_RENAMED.get(name, name) for name in names} | names


def _discord_overwrites(
    guild: discord.Guild, overwrites: dict[Target, Perms], held: set[str]
) -> dict[OverwriteKey, discord.PermissionOverwrite]:
    return {
        _key(guild, target): discord.PermissionOverwrite(**restrict(perms, held))
        for target, perms in overwrites.items()
    }


def _me(guild: discord.Guild) -> discord.Member:
    me = guild.me
    if me is None:
        raise DMScreenError("I'm still starting up. Try again in a moment.")
    missing = missing_required(held_permissions(me))
    if missing:
        raise DMScreenError(messages.needs_permissions(missing))
    return me


async def fresh_screen(guild: discord.Guild, campaign: Campaign) -> discord.TextChannel | None:
    """The campaign's screen as Discord has it now (not the possibly stale cache).

    None if there's no screen, it was deleted, or the saved channel isn't a DM screen
    (for example a channel saved before DMbot made screens itself): DMbot only ever
    changes the permissions of a channel named like one of its DM screens.
    """
    if campaign.dm_screen_channel_id is None:
        return None
    try:
        channel = await guild.fetch_channel(campaign.dm_screen_channel_id)
    except discord.NotFound:
        return None
    except discord.Forbidden as exc:
        raise DMScreenError(messages.lost_access(campaign.dm_screen_channel_id)) from exc
    if not isinstance(channel, discord.TextChannel) or not is_screen_name(channel.name):
        return None
    return channel


def screen_channel(guild: discord.Guild, campaign: Campaign) -> discord.TextChannel | None:
    """The cached screen channel, for quick checks that don't change permissions."""
    if campaign.dm_screen_channel_id is None:
        return None
    channel = guild.get_channel(campaign.dm_screen_channel_id)
    return channel if isinstance(channel, discord.TextChannel) else None


def _exposure_warning(
    overwrites: dict[Target, Perms], guild: discord.Guild, me: discord.Member, campaign: Campaign
) -> str | None:
    exposure = find_exposure(
        campaign.dm_screen_visibility,
        overwrites,
        guild_id=guild.id,
        bot_id=me.id,
        dm_ids=campaign.dm_user_ids,
    )
    return messages.exposure_warning(exposure)


async def _apply(
    channel: discord.TextChannel, campaign: Campaign, me: discord.Member
) -> dict[Target, Perms]:
    """Send the screen's full overwrite set; returns what was sent (not the stale cache)."""
    guild = channel.guild
    current = current_overwrites(channel)
    peekers = [t.id for t, perms in current.items() if t.kind == "member" and is_peeker(perms)]
    plan = overwrite_plan(
        campaign.dm_screen_visibility,
        guild_id=guild.id,
        bot_id=me.id,
        dm_ids=campaign.dm_user_ids,
        peeker_ids=peekers,
    )
    held = held_permissions(me)
    merged = merge_overwrites(current, plan, guild_id=guild.id)
    sent: dict[Target, Perms] = {t: restrict(p, held) for t, p in merged.items()}
    # Permissions only: a rename is a separate, best-effort step (`_try_rename`), so
    # Discord's rename rate limit can never hold up who can see the screen.
    await channel.edit(
        overwrites=_discord_overwrites(guild, merged, held),
        topic=messages.topic(campaign.name, campaign.dm_screen_visibility),
        reason=f"DMbot: DM screen for {campaign.name}",
    )
    return sent


async def _try_rename(channel: discord.TextChannel, campaign: Campaign) -> None:
    """Give an old-style `dm-screen-…` screen, or a renamed campaign's, its current name.

    Discord allows two renames per channel every 10 minutes and makes the bot wait
    otherwise, so this gives up after a few seconds and tries again at the next setup.
    """
    name = screen_channel_name(campaign.name, campaign.channel_number or 1)
    if channel.name == name:
        return
    try:
        await asyncio.wait_for(
            channel.edit(name=name, reason=f"DMbot: DM screen for {campaign.name}"),
            RENAME_TIMEOUT_S,
        )
    except (TimeoutError, discord.HTTPException) as exc:
        log.info("Rename of DM screen %s postponed: %s", channel.id, exc)


def guild_lock(guild_id: int) -> asyncio.Lock:
    """One lock per server around choosing channel numbers, so two campaigns set up at
    the same moment can't both take the same number."""
    return _guild_locks.setdefault(guild_id, asyncio.Lock())


async def _with_channel_number(
    guild_id: int, campaign: Campaign, store: CampaignStore
) -> tuple[Campaign, bool]:
    """The campaign with its clash number chosen, and whether its saved screen channel
    is shared with another campaign (then DMbot never renames it).

    A number is chosen once. It's only chosen again if the campaign itself was renamed
    so that its names now clash with another campaign's; other campaigns never shift.
    """
    async with guild_lock(guild_id):
        everyone_else = [c for c in await store.list_campaigns(guild_id) if c.id != campaign.id]
        others = [(c.name, c.channel_number) for c in everyone_else]
        shared = campaign.dm_screen_channel_id is not None and any(
            c.dm_screen_channel_id == campaign.dm_screen_channel_id for c in everyone_else
        )
        if campaign.channel_number is None:
            number = pick_channel_number(campaign.name, others)
            campaign = await store.set_channel_number(guild_id, campaign.id, number)
        elif clashes(campaign.name, campaign.channel_number, others):
            number = pick_channel_number(campaign.name, others)
            campaign = await store.set_channel_number(guild_id, campaign.id, number, replace=True)
    return campaign, shared


async def _pin(card: discord.Message, reason: str = "DMbot: DM screen help card") -> bool | None:
    """Pin the help card: True if pinned, False if DMbot isn't allowed, None otherwise.

    Needs Pin Messages, which is optional: the card works unpinned. Other failures (the
    channel's pin limit, a Discord outage) aren't the DM's to fix, so they're only
    logged; the next setup tries again.
    """
    try:
        await card.pin(reason=reason)
    except discord.Forbidden as exc:
        log.info("Could not pin card %s: %s", card.id, exc)
        return False
    except discord.HTTPException as exc:
        log.warning("Could not pin card %s: %s", card.id, exc)
        return None
    return True


async def _update_help_card(
    channel: discord.TextChannel, campaign: Campaign, me: discord.Member
) -> str | None:
    """Make sure the screen has one up-to-date, pinned help card.

    Leaves a current card alone (so starting a session doesn't repost it), only pinning
    it if it isn't yet. Otherwise deletes old cards, whose text and buttons may be out of
    date, and posts a new one.

    Returns a note for the DM if DMbot isn't allowed to pin the card and the screen
    doesn't already show that note. Once the card is pinned, old notes are deleted.
    """
    from dmbot.dm_screen.buttons import card_view  # buttons imports this module

    text = messages.help_card(campaign.name, campaign.dm_screen_visibility)
    old_cards: list[discord.Message] = []
    pin_notes: list[discord.Message] = []
    async for m in channel.history(limit=HELP_CARD_SCAN):
        if m.author.id != me.id:
            continue
        if m.content.startswith(messages.HELP_CARD_TITLE):
            old_cards.append(m)
        elif messages.CANT_PIN in m.content:  # may share a message with another warning
            pin_notes.append(m)
    view = card_view(campaign)
    if (
        len(old_cards) == 1
        and old_cards[0].content == text
        and _button_ids(old_cards[0]) == [getattr(i, "custom_id", None) for i in view.children]
    ):  # the same words and buttons: keep it (a new button alone also re-posts it, #553)
        pinned = True if old_cards[0].pinned else await _pin(old_cards[0])
    else:
        for old in old_cards:
            with contextlib.suppress(discord.HTTPException):
                await old.delete()
        pinned = await _pin(await channel.send(text, view=view))
    if pinned:
        for note in pin_notes:
            if note.content == messages.CANT_PIN:  # keep other warnings sent with it
                with contextlib.suppress(discord.HTTPException):
                    await note.delete()
    elif pinned is False and not pin_notes:
        return messages.CANT_PIN
    return None


def _button_ids(message: discord.Message) -> list[str | None]:
    """The custom IDs of a message's buttons, in order."""
    return [
        getattr(child, "custom_id", None)
        for row in message.components
        for child in getattr(row, "children", ())
    ]


async def setup_dm_screen(
    guild: discord.Guild,
    campaign_id: str,
    store: CampaignStore,
    *,
    category: discord.CategoryChannel | None = None,
    visibility: str | None = None,
) -> ScreenResult:
    """Create or update the campaign's DM screen. See `ensure_dm_screen`.

    `visibility`, if given, is saved first. Saving and applying both happen under the
    campaign's lock, from a fresh read of the campaign, so two changes at once can't
    leave the channel with a different setting from the one stored.
    """
    me = _me(guild)
    async with campaign_lock(campaign_id):
        try:
            if visibility is not None:
                await store.set_dm_screen_visibility(guild.id, campaign_id, visibility)
            campaign = await store.get(guild.id, campaign_id)
            if campaign is None:
                raise DMScreenError(messages.CAMPAIGN_GONE)
            campaign, shared = await _with_channel_number(guild.id, campaign, store)
            channel = await fresh_screen(guild, campaign)
            if channel is None:
                channel, overwrites = await _create(guild, campaign, store, me, category)
            else:
                overwrites = await _apply(channel, campaign, me)
                if not shared:  # two campaigns renaming one channel would fight over it
                    await _try_rename(channel, campaign)
            pin_warning = await _update_help_card(channel, campaign, me)
        except discord.Forbidden as exc:
            # The server-wide check passed, so something more local blocks it, such as
            # the category's own permissions.
            raise DMScreenError(messages.FORBIDDEN_HERE) from exc
        except discord.HTTPException as exc:
            log.warning("DM screen setup failed: %s", exc)
            raise DMScreenError(messages.discord_error(exc.text or str(exc.status))) from exc
    warnings = [_exposure_warning(overwrites, guild, me, campaign), pin_warning]
    return ScreenResult(channel, campaign, "\n\n".join(w for w in warnings if w) or None)


async def _create(
    guild: discord.Guild,
    campaign: Campaign,
    store: CampaignStore,
    me: discord.Member,
    category: discord.CategoryChannel | None,
) -> tuple[discord.TextChannel, dict[Target, Perms]]:
    held = held_permissions(me)
    plan = overwrite_plan(
        campaign.dm_screen_visibility,
        guild_id=guild.id,
        bot_id=me.id,
        dm_ids=campaign.dm_user_ids,
    )
    name = screen_channel_name(campaign.name, campaign.channel_number or 1)
    channel = await guild.create_text_channel(
        name,
        category=category,
        overwrites=_discord_overwrites(guild, dict(plan), held),
        topic=messages.topic(campaign.name, campaign.dm_screen_visibility),
        reason=f"DMbot: DM screen for {campaign.name}",
    )
    # Callers look the new channel up in the cache, which fills in from Discord's
    # gateway event shortly after the create call returns.
    for _ in range(CACHE_WAIT_STEPS):
        if guild.get_channel(channel.id) is not None:
            break
        await asyncio.sleep(CACHE_WAIT_S)
    else:
        log.warning("New DM screen %s hasn't reached the cache yet", channel.id)
    try:
        await store.set_dm_screen(guild.id, campaign.id, channel.id)
    except BaseException:
        # Don't leave a channel behind that no campaign knows about.
        with contextlib.suppress(discord.HTTPException):
            await channel.delete(reason="DMbot: could not save the DM screen")
        raise
    return channel, {target: restrict(perms, held) for target, perms in plan.items()}


class HasCampaigns(Protocol):
    campaigns: CampaignStore


async def ensure_dm_screen(
    bot: HasCampaigns, interaction: discord.Interaction, campaign: Campaign
) -> int:
    """The hook `/dmbot start` calls (#48): the campaign's DM screen channel ID.

    Creates `#dmb-dm-screen-<campaign>` if the campaign has none (or it was deleted), and
    makes its permissions match the campaign's visibility. Call it again after changing
    the campaign's DMs or visibility. Raises DMScreenError with a message for the DM if
    something needs fixing. Anyone unexpected who can see the screen, and a help card
    that couldn't be pinned, are warned about in the screen itself.
    """
    guild = interaction.guild
    if guild is None:
        raise DMScreenError("Use this in a server.")
    category = getattr(interaction.channel, "category", None)
    result = await setup_dm_screen(
        guild,
        campaign.id,
        bot.campaigns,
        category=category if isinstance(category, discord.CategoryChannel) else None,
    )
    if result.warning:
        with contextlib.suppress(discord.HTTPException):
            await result.channel.send(
                result.warning, allowed_mentions=discord.AllowedMentions.none()
            )
    return result.channel.id
