"""What happens to a campaign's Discord channels when retention deletes the campaign (#1598,
item 1 of #618; docs/PLAN.md, "Retention").

What happens to each channel follows whose words are in it:

- **The DM screen and the game-time channel are deleted.** They hold only DMbot's own messages,
  the DM screen holds DM-only notes, and the data behind both is gone.
- **The transcript channel and `dmb-chat` are kept**, with one closing note, and DMbot's own
  messages there lose their buttons (so nothing answers "I can't find that campaign"). They hold
  what people wrote or said, which is not DMbot's to delete; a server admin can.

Only the channels recorded on the campaign, found by id in the campaign's own server (a channel
of another server is never reached: it is looked up through this campaign's guild and its guild
is checked again). A channel that is gone, one DMbot may not manage, or one that does not answer
in time is skipped and counted, never an error that stops the job.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import discord

from dmbot.campaigns import Campaign

log = logging.getLogger(__name__)

CHANNEL_TIMEOUT_S = 15  # per Discord call; a slow channel is skipped, the job goes on
SCANNED_MESSAGES = 100  # the newest messages looked at for DMbot's own buttons
NO_PINGS = discord.AllowedMentions.none()
CLOSING_NOTE = (
    "This campaign was deleted on {date} because nobody played it for a long time. "
    "DMbot no longer uses this channel. A server admin can delete it."
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


async def retire_channels(
    guild: discord.Guild | None, campaign: Campaign, me_id: int, now: int
) -> ChannelCounts:
    """Do the above for a campaign that was just deleted. `guild`: the campaign's server as the
    bot has it (None: the bot is no longer there, so every recorded channel is skipped)."""
    counts = ChannelCounts()
    note = CLOSING_NOTE.format(date=f"<t:{now}:D>")
    plan = (
        (campaign.dm_screen_channel_id, True),
        (campaign.gametime_channel_id, True),
        (campaign.transcript_channel_id, False),
        (campaign.dmbchat_channel_id, False),
    )
    for channel_id, delete in plan:
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

                    if delete:
                        await channel.delete(reason="DMbot: the campaign was deleted")
                    else:
                        await _close(channel, me_id, note)
                    ok = True
            except (discord.HTTPException, TimeoutError, _NotOurs):
                ok = False
        if not ok:
            counts.skipped += 1
        elif delete:
            counts.deleted += 1
        else:
            counts.closed += 1
    if counts.skipped:  # once per campaign; the ids stay out of the log
        log.info("Retention: %d channel(s) of a deleted campaign were skipped", counts.skipped)
    return counts


async def _close(channel: discord.TextChannel, me_id: int, note: str) -> None:
    """Take the buttons off DMbot's own messages (the pinned ones and the newest), then say the
    closing note. Other people's messages are never touched."""
    seen: set[int] = set()
    messages: list[Any] = [*(await channel.pins())]
    async for message in channel.history(limit=SCANNED_MESSAGES):
        messages.append(message)
    for message in messages:
        if message.id in seen or message.author.id != me_id or not message.components:
            continue
        seen.add(message.id)
        try:
            await message.edit(view=None)
        except discord.NotFound:
            continue  # deleted meanwhile
    await channel.send(note, allowed_mentions=NO_PINGS)


class _NotOurs(Exception):
    """The recorded id is not a text channel of the campaign's own server: left alone."""
