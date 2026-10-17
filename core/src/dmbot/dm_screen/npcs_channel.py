"""The NPC roster channel (#1761; docs/PLAN.md, "Channels"): `#dmb-npcs-<short name>`.

The second controlled channel besides the DM screen, made and kept like the rules channel
(`rules_channel`): it follows the DM screen's visibility, is read only for everyone, and lists
the people and groups the table may know, one message each, groups first, then by name.

Players can peek here, and a list sits in view all session, so it holds only what recall's
players audience may say (`RecallService.roster`, the same rules and not a second copy): entries
the DM confirmed, never a secret name, a limited clue, a story thread, a deed, a DM note, a
condition (dead, missing: the table may not know yet), standing, or anything from the adventure
plan. There is no description line for that reason: the only description an entry has is the DM's
note.

Messages are found again by the entry id in their last line (`-# ref <id>`), so nothing about
them is stored: a rename edits a message in place, a merge or a rejection deletes it, a restart
or a channel made again rebuilds the same list. Only DMbot's own messages are ever touched.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
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
    fresh_screen,
    held_permissions,
)
from dmbot.dm_screen.names import PREFIX, sub_channel_name
from dmbot.dm_screen.rules_channel import (
    CARD_TITLE,
    HISTORY_SCAN,
    MAX_SENDS,
    MESSAGE_MAX,
    NO_PINGS,
    RECENT_SCAN,
    rules_plan,
    screen_peekers,
)
from dmbot.dm_screen.transcript_channel import _repair
from dmbot.memory.recall import RecallService, RosterEntry

log = logging.getLogger(__name__)

KIND = "npcs"
NAME_PREFIX = f"{PREFIX}-{KIND}-"
PIN_REASON = "DMbot: NPC channel card"
MAX_ENTRIES = 200  # messages for entries; past that, one last message says how many more
MAX_EDITS = 25  # edits per pass: a renamed group changes every member's message
MAX_DELETES = 25  # deletes per pass (Discord's rate limits); the rest is done next time
OTHER_NAMES_MAX = 8  # other names shown for one entry, then "+N more"

PERSON_MARK, GROUP_MARK = "👤", "👥"
SETTLE_S = 3.0  # a burst of changes (a merge, a confirm-all) becomes one pass this much later
PASS_GAP_S = 60.0  # at most one pass for a campaign a minute: a busy session mustn't flood Discord

MORE_TITLE = "➕ **+"
REF = "-# ref "  # the last line of an entry's message: Discord's small print, and its id

_SYNC_LOCKS: dict[str, asyncio.Lock] = {}


def sync_lock(campaign_id: str) -> asyncio.Lock:
    """One message-posting pass at a time for a campaign, apart from the rules channel's and
    from `campaign_lock` (see `rules_channel.sync_lock`)."""
    return _SYNC_LOCKS.setdefault(campaign_id, asyncio.Lock())


class NpcsChannelError(Exception):
    """Why the NPC channel couldn't be set up, as a plain cause for the DM."""


def npcs_channel_name(campaign: Campaign) -> str:
    return sub_channel_name(KIND, campaign.name, campaign.channel_number or 1)


def is_npcs_name(name: str) -> bool:
    return name.startswith(NAME_PREFIX)


def pass_delay(last_pass: float | None, now: float) -> float:
    """Seconds to wait before the next pass for a campaign: a short settle, and never sooner
    than `PASS_GAP_S` after the last one (`last_pass`, on the same clock as `now`)."""
    if last_pass is None:
        return SETTLE_S
    return max(SETTLE_S, last_pass + PASS_GAP_S - now)


# ---- what each message says -------------------------------------------------------------------


def _clean(text: str) -> str:
    return discord.utils.escape_markdown(text)


def _more(names: tuple[str, ...], more: int) -> str:
    shown = ", ".join(_clean(n) for n in names)
    return f"{shown}, and {more} more" if more else shown


def entry_text(entry: RosterEntry) -> str:
    """One entry as its message: name and kind, other names, its groups, who it is close to."""
    mark = GROUP_MARK if entry.is_group else PERSON_MARK
    lines = [f"{mark} **{_clean(entry.name)}** · {entry.kind}"]
    if entry.also_called:
        names = entry.also_called[:OTHER_NAMES_MAX]
        lines.append(f"Also called: {_more(names, len(entry.also_called) - len(names))}")
    if entry.groups:
        lines.append(f"In: {_more(entry.groups, entry.groups_more)}")
    if entry.ties:
        lines.append(f"Connected to: {_more(entry.ties, entry.ties_more)}")
    tail = f"{REF}{entry.id}"
    body = "\n".join(lines)[: MESSAGE_MAX - len(tail) - 1]  # (belt: names are capped already)
    return f"{body}\n{tail}"


def more_text(count: int) -> str:
    return f"{MORE_TITLE}{count} more.** Ask your DM for the rest."


def _ref(content: str) -> str | None:
    """The entry id a message of DMbot's is for, or None if it isn't an entry message."""
    last = content.rsplit("\n", 1)[-1]
    return last[len(REF) :] if last.startswith(REF) else None


@dataclass(slots=True)
class SyncResult:
    sent: int = 0
    edited: int = 0
    deleted: int = 0
    left_for_next_time: int = 0  # over the caps, or a message Discord refused: run again soon


_MORE = "more"  # the key of the "+N more" message


def wanted_messages(entries: list[RosterEntry]) -> list[tuple[str, str]]:
    """(key, text) in channel order: the first `MAX_ENTRIES`, then the "+N more" message."""
    out = [(e.id, entry_text(e)) for e in entries[:MAX_ENTRIES]]
    if len(entries) > MAX_ENTRIES:
        out.append((_MORE, more_text(len(entries) - MAX_ENTRIES)))
    return out


async def sync_roster(
    channel: discord.TextChannel,
    me_id: int,
    entries: list[RosterEntry],
    *,
    reorder: bool = False,
) -> SyncResult:
    """Make the channel show exactly these entries. Looks at DMbot's own messages only; edits
    rather than reposts; the rest is picked up next time (`left_for_next_time`). A new entry goes
    at the end, so the order is only exact after a rebuild: with `reorder` (every start) whatever
    comes after the first message out of place is deleted and posted again in order. Idempotent."""
    result = SyncResult()
    wanted = wanted_messages(entries)
    wanted_text = dict(wanted)
    have: dict[str, discord.Message] = {}
    order: list[str] = []  # the keys of `have`, as they sit in the channel
    extras: list[discord.Message] = []  # a second copy of one thing, or one no longer wanted
    async for message in channel.history(limit=HISTORY_SCAN, oldest_first=True):
        if message.author.id != me_id:
            continue
        key = _MORE if message.content.startswith(MORE_TITLE) else _ref(message.content)
        if key is None:
            continue  # the pinned card, or something else of DMbot's
        if key in have or key not in wanted_text:
            extras.append(message)
        else:
            have[key] = message
            order.append(key)
    missing = [key for key, _ in wanted if key not in have]
    if reorder:
        # Keep the leading messages that are exactly the list's start; repost the rest.
        keep = 0
        while keep < len(order) and keep < len(wanted) and order[keep] == wanted[keep][0]:
            keep += 1
        if keep < len(order):
            for key in order[keep:]:
                extras.append(have.pop(key))
            missing = [key for key, _ in wanted if key not in have]
    elif missing and _MORE in have:
        extras.append(have.pop(_MORE))  # it is last, so it moves behind the new messages
        missing = [key for key, _ in wanted if key not in have]
    deletes_left = False
    for budget, message in enumerate(extras):
        if budget >= MAX_DELETES:
            deletes_left = True
            result.left_for_next_time += 1
            continue
        try:
            await message.delete()
        except discord.NotFound:
            pass  # already gone
        except discord.HTTPException as exc:
            log.warning("NPC channel: couldn't delete a message: %s", exc)
            deletes_left = True
            result.left_for_next_time += 1
            if isinstance(exc, discord.Forbidden):
                break  # no permission: don't hammer the rest
            continue
        result.deleted += 1
    edits = 0
    for key, message in have.items():
        if message.content == wanted_text[key]:
            continue
        if edits >= MAX_EDITS:
            result.left_for_next_time += 1
            continue
        edits += 1
        try:
            await message.edit(content=wanted_text[key], allowed_mentions=NO_PINGS)
        except discord.HTTPException as exc:
            log.warning("NPC channel: couldn't edit a message: %s", exc)
            result.left_for_next_time += 1
            if isinstance(exc, discord.Forbidden):
                break
            continue
        result.edited += 1
    if deletes_left:  # a new message now would land before ones still to be reposted
        result.left_for_next_time += len(missing)
        return result
    for key in missing:
        if result.sent >= MAX_SENDS:
            result.left_for_next_time += 1
            continue
        try:
            await channel.send(wanted_text[key], allowed_mentions=NO_PINGS, silent=True)
        except discord.HTTPException as exc:
            log.warning("NPC channel: couldn't post a message: %s", exc)
            result.left_for_next_time += 1
            if isinstance(exc, discord.Forbidden):
                break
            continue
        result.sent += 1
    return result


# ---- the channel ------------------------------------------------------------------------------


async def _fresh(guild: discord.Guild, campaign: Campaign) -> discord.TextChannel | None:
    """The saved channel as Discord has it now; None if it's gone, hidden from DMbot (a new one
    is made), or not an NPC channel."""
    if campaign.npcs_channel_id is None:
        return None
    try:
        channel = await guild.fetch_channel(campaign.npcs_channel_id)
    except (discord.NotFound, discord.Forbidden):
        return None
    if not isinstance(channel, discord.TextChannel) or not is_npcs_name(channel.name):
        return None
    return channel


async def _ensure_card(channel: discord.TextChannel, campaign: Campaign, me_id: int) -> None:
    """One current, pinned "What's this channel?" card. Best effort: the channel works without
    it."""
    text = messages.npcs_card(campaign.name)
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
        log.warning("NPC channel card not updated: %s", exc)


async def setup_npcs_channel(
    guild: discord.Guild,
    campaign_id: str,
    store: CampaignStore,
    *,
    category: discord.CategoryChannel | None = None,
    screen: discord.abc.GuildChannel | None = None,
) -> discord.TextChannel:
    """Create the campaign's NPC channel, or put an existing one right again (who may see it
    follows the DM screen's visibility; `screen` gives the peekers). Call after the DM screen is
    set up (which picks the campaign's channel number). Raises NpcsChannelError with a plain
    cause for the DM."""
    try:
        me = _me(guild)
    except DMScreenError as exc:
        raise NpcsChannelError(str(exc)) from exc
    async with campaign_lock(campaign_id):
        try:
            campaign = await store.get(guild.id, campaign_id)
            if campaign is None:
                raise NpcsChannelError(messages.CAMPAIGN_GONE)
            held = held_permissions(me)
            reason = f"DMbot: people and groups for {campaign.name}"
            channel = await _fresh(guild, campaign)
            # The peekers are the DM screen's (see `rules_channel.setup_rules_channel`).
            if screen is None:
                with contextlib.suppress(DMScreenError, discord.HTTPException):
                    screen = await fresh_screen(guild, campaign)
            plan = rules_plan(
                campaign.dm_screen_visibility,
                guild_id=guild.id,
                bot_id=me.id,
                dm_ids=sorted(campaign.dm_user_ids),
                peeker_ids=screen_peekers(screen),
            )
            if channel is None:
                channel = await guild.create_text_channel(
                    npcs_channel_name(campaign),
                    category=category,
                    overwrites=_discord_overwrites(guild, plan, held),
                    topic=messages.NPCS_TOPIC,
                    reason=reason,
                )
                for _ in range(CACHE_WAIT_STEPS):
                    if guild.get_channel(channel.id) is not None:
                        break
                    await asyncio.sleep(CACHE_WAIT_S)
                else:
                    log.warning("New NPC channel %s hasn't reached the cache yet", channel.id)
                try:
                    await store.set_npcs_channel(guild.id, campaign.id, channel.id)
                except BaseException:
                    with contextlib.suppress(discord.HTTPException):
                        await channel.delete(reason="DMbot: could not save the NPC channel")
                    raise
            else:
                await _repair(
                    channel, npcs_channel_name(campaign), plan, held, messages.NPCS_TOPIC, reason
                )
            await _ensure_card(channel, campaign, me.id)
        except discord.Forbidden as exc:
            raise NpcsChannelError(messages.TRANSCRIPT_FORBIDDEN) from exc
        except discord.HTTPException as exc:
            log.warning("NPC channel setup failed: %s", exc)
            raise NpcsChannelError(
                messages.transcript_discord_error(exc.text or str(exc.status))
            ) from exc
        except TimeoutError as exc:
            raise NpcsChannelError(messages.TRANSCRIPT_NO_ANSWER) from exc
    return channel


async def follow_screen(
    guild: discord.Guild, campaign: Campaign, screen: discord.TextChannel
) -> None:
    """The DM screen's visibility, DMs or peekers just changed: make the NPC channel (if the
    campaign has one) match, now. Never raises: the DM screen's change has already happened, and
    the next start repairs the rest."""
    if campaign.npcs_channel_id is None:
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
                npcs_channel_name(campaign),
                plan,
                held_permissions(me),
                messages.NPCS_TOPIC,
                f"DMbot: people and groups for {campaign.name}",
            )
    except Exception:
        log.warning("Couldn't update the NPC channel's visibility", exc_info=True)


async def sync_campaign(
    guild: discord.Guild, campaign: Campaign, recall: RecallService, *, reorder: bool = False
) -> SyncResult | None:
    """Bring the campaign's NPC channel up to date from what the players may know. None if the
    campaign has no NPC channel or it is gone (the next start makes it again). Raises on a
    Discord or database failure: the caller logs it."""
    async with sync_lock(campaign.id):
        channel = await _fresh(guild, campaign)
        me = guild.me
        if channel is None or me is None:
            return None
        entries = await recall.roster(guild.id, campaign.id)
        return await sync_roster(channel, me.id, entries, reorder=reorder)
