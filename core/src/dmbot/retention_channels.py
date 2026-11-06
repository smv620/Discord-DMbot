"""What happens to a campaign's Discord channels when retention deletes the campaign (#1598,
item 1 of #618; docs/PLAN.md, "Retention").

What happens to each channel follows whose words are in it:

- **The DM screen, the game-time channel and the rules channel are deleted**, but only while
  they are still named like DMbot's (`is_screen_name`, `is_gametime_name`, `is_rules_name`): a
  channel an admin renamed and reused is skipped, never deleted. They hold only DMbot's own
  messages, the DM screen holds DM-only notes, and the data behind all of them is gone.
- **The transcript channel and `dmb-chat` are kept**, with one closing note (sent first), and
  DMbot's own messages there lose their buttons (best effort, at most 25), so nothing answers
  "I can't find that campaign". They hold what people wrote or said, which is not DMbot's to
  delete; a server admin can.

Only the channels recorded on the campaign, found by id in the campaign's own server (a channel
of another server is never reached: it is looked up through this campaign's guild and its guild
is checked again). A channel that is gone, one DMbot may not manage, or one that does not answer
in time is skipped and counted, never an error that stops the job.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import discord

from dmbot.dm_screen.dm_bot_channel import is_dm_bot_name
from dmbot.dm_screen.gametime_channel import is_gametime_name
from dmbot.dm_screen.names import is_screen_name
from dmbot.dm_screen.npcs_channel import is_npcs_name
from dmbot.dm_screen.rules_channel import is_rules_name
from dmbot.mentions import NO_PINGS

log = logging.getLogger(__name__)

CHANNEL_TIMEOUT_S = 15  # per Discord call; a slow channel is skipped, the job goes on
SCANNED_MESSAGES = 100  # the newest messages looked at for DMbot's own buttons
MAX_EDITS = 25  # buttons taken off per channel: bounded, so a busy channel cannot hold the job
CLOSING_NOTE = (
    "This campaign was deleted on {date} because nobody played it for a long time. "
    "DMbot no longer uses this channel. A server admin can delete it."
)
# When the campaign's plan holder deleted it by hand (#1608).
DELETED_NOTE = (
    "This campaign was deleted on {date}. "
    "DMbot no longer uses this channel. A server admin can delete it if it isn't needed."
)


@dataclass(slots=True)
class ChannelCounts:
    """What was done to the channels (counts only, for the log line)."""

    deleted: int = 0
    closed: int = 0
    skipped: int = 0

    def add(self, other: ChannelCounts) -> None:
        self.deleted += other.deleted
        self.closed += other.closed
        self.skipped += other.skipped


class HasChannels(Protocol):
    """What tidying a deleted campaign's channels needs: its server and the channel IDs recorded
    on it. A `Campaign` has them; so does the note a website delete leaves (#2554), once the
    campaign's row is gone."""

    @property
    def id(self) -> str: ...
    @property
    def guild_id(self) -> int: ...
    @property
    def dm_screen_channel_id(self) -> int | None: ...
    @property
    def gametime_channel_id(self) -> int | None: ...
    @property
    def dm_bot_channel_id(self) -> int | None: ...
    @property
    def rules_channel_id(self) -> int | None: ...
    @property
    def npcs_channel_id(self) -> int | None: ...
    @property
    def transcript_channel_id(self) -> int | None: ...
    @property
    def dmbchat_channel_id(self) -> int | None: ...


async def retire_channels(
    guild: discord.Guild | None,
    campaign: HasChannels,
    me_id: int,
    now: int,
    *,
    by_hand: bool = False,
) -> ChannelCounts:
    """Do the above for a campaign that was just deleted. `guild`: the campaign's server as the
    bot has it (None: the bot is no longer there, so every recorded channel is skipped)."""
    counts = ChannelCounts()
    note = (DELETED_NOTE if by_hand else CLOSING_NOTE).format(date=f"<t:{now}:D>")
    # (id, delete it?, the name it must still have to be deleted): the same trust rule the bot uses
    # everywhere else, so a channel an admin renamed and reused is never deleted.
    plan: tuple[tuple[int | None, bool, Callable[[str], bool]], ...] = (
        (campaign.dm_screen_channel_id, True, is_screen_name),
        (campaign.gametime_channel_id, True, is_gametime_name),
        (campaign.dm_bot_channel_id, True, is_dm_bot_name),
        (campaign.rules_channel_id, True, is_rules_name),  # DMbot's own: house rules (#1752)
        (campaign.npcs_channel_id, True, is_npcs_name),  # DMbot's own: the NPC roster (#1761)
        (campaign.transcript_channel_id, False, _any_name),
        (campaign.dmbchat_channel_id, False, _any_name),
    )
    for channel_id, delete, named_right in plan:
        if channel_id is None:
            continue
        ok = False
        if guild is not None:
            try:
                async with asyncio.timeout(CHANNEL_TIMEOUT_S):
                    channel = await guild.fetch_channel(channel_id)
                    if not isinstance(channel, discord.TextChannel) or (
                        channel.guild.id != campaign.guild_id
                    ):
                        raise _NotOurs

                    if delete and not named_right(channel.name):
                        raise _NotOurs  # renamed and reused: left alone, counted as skipped
                    if delete:
                        await channel.delete(reason="DMbot: the campaign was deleted")
                    else:
                        # The note first: it is the part that matters, and the campaign is
                        # gone, so nothing would try again if the buttons took too long.
                        await channel.send(note, allowed_mentions=NO_PINGS)
                    ok = True
            # (an id of another server raises InvalidData from fetch_channel itself: also skipped)
            except (discord.DiscordException, TimeoutError, _NotOurs):
                ok = False
            if ok and not delete and isinstance(channel, discord.TextChannel):
                await _drop_buttons(channel, me_id)
        if not ok:
            counts.skipped += 1
        elif delete:
            counts.deleted += 1
        else:
            counts.closed += 1
    if counts.skipped:  # once per campaign; the ids stay out of the log
        log.info("Retention: %d channel(s) of a deleted campaign were skipped", counts.skipped)
    return counts


async def _drop_buttons(channel: discord.TextChannel, me_id: int) -> None:
    """Take the buttons off DMbot's own messages (the pinned ones and the newest), as far as
    Discord lets it in the time given: a failure or a rate limit just stops here. Other people's
    messages are never touched. (A crash between the campaign's delete and this leaves the
    channels as they were: the ids went with the row, so nothing could find them again.)"""
    try:
        async with asyncio.timeout(CHANNEL_TIMEOUT_S):
            seen: set[int] = set()
            messages: list[Any] = [*(await channel.pins())]
            async for message in channel.history(limit=SCANNED_MESSAGES):
                messages.append(message)
            edited = 0
            for message in messages:
                if message.id in seen or message.author.id != me_id or not message.components:
                    continue
                seen.add(message.id)
                if edited >= MAX_EDITS:
                    break
                try:
                    await message.edit(view=None)
                    edited += 1
                except discord.NotFound:
                    continue  # deleted meanwhile
    except (discord.DiscordException, TimeoutError):
        log.info("Retention: some buttons were left on a closed channel")


def _any_name(name: str) -> bool:
    return True  # a closed channel only gets a note, whatever it is called now


class _NotOurs(Exception):
    """The recorded id is not a text channel of the campaign's own server: left alone."""
