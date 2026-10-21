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
import bisect
import contextlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import discord

from dmbot.campaigns import Campaign, CampaignStore
from dmbot.db import Database
from dmbot.discord_limits import fit_message
from dmbot.dm_screen import messages, npc_pages
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
    RECENT_SCAN,
    rules_plan,
    screen_peekers,
)
from dmbot.dm_screen.transcript_channel import _repair
from dmbot.memory.recall import RecallService, RosterEntry
from dmbot.mentions import NO_PINGS

log = logging.getLogger(__name__)

KIND = "npcs"
NAME_PREFIX = f"{PREFIX}-{KIND}-"
PIN_REASON = "DMbot: NPC channel card"
MAX_ENTRIES = 200  # messages for entries; past that, one last message says how many more
MAX_EDITS = 25  # edits per pass: a renamed group changes every member's message
MAX_DELETES = 100  # deletes per pass (bulk where Discord allows); the rest is done next time
BULK_DELETE_DAYS = 13  # Discord bulk-deletes messages under 14 days old only
REORDER_MIN = 10  # put the list in order again only when this many messages are out of place
OTHER_NAMES_MAX = 8  # other names shown for one entry, then "+N more"

PERSON_MARK, GROUP_MARK = "👤", "👥"
SETTLE_S = 3.0  # a burst of changes (a merge, a confirm-all) becomes one pass this much later
PASS_GAP_S = 60.0  # at most one pass for a campaign a minute: a busy session mustn't flood Discord

MORE_TITLE = "➕ **+"

_SYNC_LOCKS: dict[str, asyncio.Lock] = {}


def sync_lock(campaign_id: str) -> asyncio.Lock:
    """One message-posting pass at a time for a campaign, apart from the rules channel's and
    from `campaign_lock` (see `rules_channel.sync_lock`)."""
    return _SYNC_LOCKS.setdefault(campaign_id, asyncio.Lock())


def forget_campaign(campaign_id: str) -> None:
    """Drop a campaign's lock when the campaign is gone, unless a pass holds it right now."""
    lock = _SYNC_LOCKS.get(campaign_id)
    if lock is not None and not lock.locked():
        del _SYNC_LOCKS[campaign_id]


def prune_locks(alive: set[str]) -> None:
    """Drop the locks of campaigns that no longer exist (the start-up sweep)."""
    for campaign_id in [c for c in _SYNC_LOCKS if c not in alive]:
        forget_campaign(campaign_id)


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
    return fit_message("\n".join(lines))  # (belt: names are capped already)


def more_text(count: int) -> str:
    return f"{MORE_TITLE}{count} more.** Ask your DM for the rest."


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


class MessageMap(Protocol):
    """Which Discord message shows which entry (a table, so the text never carries an id)."""

    async def load(self, guild_id: int, campaign_id: str) -> dict[str, int]: ...

    async def save(
        self, guild_id: int, campaign_id: str, entry_id: str, message_id: int
    ) -> None: ...

    async def forget(self, guild_id: int, campaign_id: str, entry_ids: Sequence[str]) -> None: ...


class NpcMessageStore:
    """`MessageMap` in the database (table `npcs_channel_messages`, migration 0128)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def load(self, guild_id: int, campaign_id: str) -> dict[str, int]:
        async with self._db.guild(guild_id, snapshot=True) as conn:
            cur = await conn.execute(
                "SELECT entry_id, message_id FROM npcs_channel_messages"
                " WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            return {str(r["entry_id"]): int(r["message_id"]) for r in await cur.fetchall()}

    async def save(self, guild_id: int, campaign_id: str, entry_id: str, message_id: int) -> None:
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO npcs_channel_messages (guild_id, campaign_id, entry_id, message_id)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT (guild_id, campaign_id, entry_id)"
                " DO UPDATE SET message_id = EXCLUDED.message_id",
                (guild_id, campaign_id, entry_id, message_id),
            )

    async def forget(self, guild_id: int, campaign_id: str, entry_ids: Sequence[str]) -> None:
        if not entry_ids:
            return
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "DELETE FROM npcs_channel_messages WHERE guild_id = %s AND campaign_id = %s"
                " AND entry_id = ANY(%s)",
                (guild_id, campaign_id, list(entry_ids)),
            )


def _young(message: discord.Message) -> bool:
    """Discord only bulk-deletes messages under 14 days old (a day's margin is kept)."""
    made = getattr(message, "created_at", None)
    if not isinstance(made, datetime):
        return False
    return datetime.now(UTC) - made < timedelta(days=BULK_DELETE_DAYS)


async def _delete_messages(
    channel: discord.TextChannel, messages_: list[discord.Message]
) -> tuple[list[discord.Message], bool]:
    """Delete these messages, many at once where Discord allows it. Returns the ones gone and
    whether to stop for this pass (no permission, or Discord refused)."""
    gone: list[discord.Message] = []
    young = [m for m in messages_ if _young(m)]
    single = [m for m in messages_ if m not in young]
    for at in range(0, len(young), 100):
        chunk = young[at : at + 100]
        if len(chunk) < 2:
            single.extend(chunk)
            continue
        try:
            await channel.delete_messages(chunk)
            gone.extend(chunk)
        except discord.Forbidden as exc:
            log.warning("NPC channel: couldn't delete messages: %s", exc)
            return gone, True
        except discord.HTTPException:
            single.extend(chunk)  # one at a time, below
    for message in single:
        try:
            await message.delete()
        except discord.NotFound:
            pass  # already gone
        except discord.Forbidden as exc:
            log.warning("NPC channel: couldn't delete a message: %s", exc)
            return gone, True
        except discord.HTTPException as exc:
            log.warning("NPC channel: couldn't delete a message: %s", exc)
            continue
        gone.append(message)
    return gone, False


_AND_MORE = re.compile(r", and \d+ more$")


def _items(text: str) -> set[str]:
    """The names listed under the heading line, one by one ("Also called: A, B" gives A and B)."""
    out: set[str] = set()
    for line in text.splitlines()[1:]:
        _, _, names = line.partition(": ")
        out.update(n for n in _AND_MORE.sub("", names).split(", ") if n)
    return out


def _removes_text(old: str, new: str) -> bool:
    """Does this edit take a listed name away (an other name made secret, a tie that ended)?
    Such edits go first: what players may no longer see must not wait behind cosmetic changes
    (a new kind, a name added)."""
    return not _items(old) <= _items(new)


def _out_of_place(order: list[str], wanted_keys: list[str]) -> int:
    """How many of the messages (their keys, as they sit) are not in the wanted order: all but
    the longest run that already is. One entry added at the front is 1, not the whole list."""
    where = {key: at for at, key in enumerate(wanted_keys)}
    tails: list[int] = []
    for key in order:
        at = bisect.bisect_left(tails, where[key])
        if at == len(tails):
            tails.append(where[key])
        else:
            tails[at] = where[key]
    return len(order) - len(tails)


async def sync_roster(
    channel: discord.TextChannel,
    me_id: int,
    entries: list[RosterEntry],
    mapping: MessageMap,
    *,
    guild_id: int,
    campaign_id: str,
    reorder: bool = False,
    site_url: str = "",
) -> SyncResult:
    """Make the channel show exactly these entries. Looks at DMbot's own messages only (found by
    the message map, never by their text); edits rather than reposts; the rest is picked up next
    time (`left_for_next_time`). A new entry goes at the end. With `reorder` the list is put in
    order again, but only when `REORDER_MIN` or more messages are out of place: whatever follows
    the first message out of place is deleted (in bulk) and posted again. Idempotent. With a
    `site_url` each entry's message carries a 📖 Open link button to its page (#2265); a message
    gains, changes or loses it by an edit, like its text, within the same limits."""
    result = SyncResult()
    wanted = wanted_messages(entries)
    links = {
        key: npc_pages.page_url(site_url, guild_id, campaign_id, key)
        for key, _ in wanted
        if key != _MORE
    }
    wanted_text = dict(wanted)
    mapped = await mapping.load(guild_id, campaign_id)
    key_of = {message_id: key for key, message_id in mapped.items()}
    have: dict[str, discord.Message] = {}
    order: list[str] = []  # the keys of `have`, as they sit in the channel
    extras: list[discord.Message] = []  # a stale message, or one that was never recorded
    async for message in channel.history(limit=HISTORY_SCAN, oldest_first=True):
        if message.author.id != me_id or message.content.startswith(CARD_TITLE):
            continue
        key = key_of.get(message.id)
        if key is None or key not in wanted_text:
            extras.append(message)
        else:
            have[key] = message
            order.append(key)
    lost = [key for key in mapped if key not in have and key in wanted_text]
    if lost:  # the message is gone (deleted by hand): forget the row and post it again
        await mapping.forget(guild_id, campaign_id, lost)
    missing = [key for key, _ in wanted if key not in have]
    if reorder:
        keep = 0
        while keep < len(order) and keep < len(wanted) and order[keep] == wanted[keep][0]:
            keep += 1
        if _out_of_place(order, [k for k, _ in wanted]) >= REORDER_MIN:
            for key in order[keep:]:
                extras.append(have.pop(key))
            missing = [key for key, _ in wanted if key not in have]
    elif missing and _MORE in have:
        extras.append(have.pop(_MORE))  # it is last, so it moves behind the new messages
        missing = [key for key, _ in wanted if key not in have]
    deletes_left = False
    if extras:
        todo, rest = extras[:MAX_DELETES], extras[MAX_DELETES:]
        gone, stopped = await _delete_messages(channel, todo)
        result.deleted += len(gone)
        left = len(todo) - len(gone) + len(rest)
        result.left_for_next_time += left
        deletes_left = left > 0 or stopped
        await mapping.forget(guild_id, campaign_id, [k for k, m in key_of_messages(mapped, gone)])
    changes = [
        (k, m)
        for k, m in have.items()
        if m.content != wanted_text[k] or npc_pages.message_link(m) != links.get(k, "")
    ]
    changes.sort(key=lambda km: not _removes_text(km[1].content, wanted_text[km[0]]))
    edits = 0
    for key, message in changes:
        if edits >= MAX_EDITS:
            result.left_for_next_time += 1
            continue
        edits += 1
        try:
            await message.edit(
                content=wanted_text[key],
                view=npc_pages.open_view(links.get(key, "")),
                allowed_mentions=NO_PINGS,
            )
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
            view = npc_pages.open_view(links.get(key, ""))
            if view is not None:
                sent = await channel.send(
                    wanted_text[key], allowed_mentions=NO_PINGS, silent=True, view=view
                )
            else:
                sent = await channel.send(wanted_text[key], allowed_mentions=NO_PINGS, silent=True)
        except discord.HTTPException as exc:
            log.warning("NPC channel: couldn't post a message: %s", exc)
            result.left_for_next_time += 1
            if isinstance(exc, discord.Forbidden):
                break
            continue
        await mapping.save(guild_id, campaign_id, key, sent.id)
        result.sent += 1
    return result


def key_of_messages(
    mapped: dict[str, int], messages_: list[discord.Message]
) -> list[tuple[str, discord.Message]]:
    """The map keys of these messages (those that have one)."""
    by_id = {message_id: key for key, message_id in mapped.items()}
    return [(by_id[m.id], m) for m in messages_ if m.id in by_id]


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
    guild: discord.Guild,
    campaign: Campaign,
    recall: RecallService,
    mapping: MessageMap,
    *,
    reorder: bool = False,
    site_url: str = "",
    roster: npc_pages.NpcRosterStore | None = None,
    now: int = 0,
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
        saved = True
        if roster is not None:  # the page the buttons open shows what this pass lists (#2265)
            try:
                await roster.save(guild.id, campaign.id, entries, now)
            except Exception:
                log.warning("NPC channel: couldn't save the pages' view", exc_info=True)
                saved = False
        done = await sync_roster(
            channel,
            me.id,
            entries,
            mapping,
            guild_id=guild.id,
            campaign_id=campaign.id,
            reorder=reorder,
            site_url=site_url,
        )
        if not saved:  # run again soon: the page must not keep showing what the list no longer does
            done.left_for_next_time += 1
        return done
